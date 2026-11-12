"""
Learnly Public R-Score Scraper
No API keys required — uses Reddit's unauthenticated JSON endpoints.
"""

import os
import re
import time
import logging
from datetime import datetime, timezone

import requests
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUBREDDITS = ["cegepcoteR", "Dawson", "mcgill", "VanierCollege", "Concordia"]
QUERIES = ["cote r", "r-score", "moyenne", "CRC"]
OUTPUT_CSV = os.getenv("OUTPUT_CSV", "rscore_data.csv")
CSV_HEADERS = ["source", "timestamp", "program_or_course", "grade", "class_average", "r_score", "raw_text"]

REQUEST_DELAY = 1.5  # seconds between every HTTP request

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# Bilingual regex patterns (preserved from scraper.py)
# ---------------------------------------------------------------------------

RE_RSCORE = re.compile(
    r"""
    (?:r[\s\-]?score|cote[\s\-]?r|crc)
    [\s:=of]*
    (\d{1,2}(?:[.,]\d{1,2})?)
    """,
    re.IGNORECASE | re.VERBOSE,
)

RE_CLASS_AVG = re.compile(
    r"""
    (?:class\s+avg(?:erage)?|group\s+avg(?:erage)?|moyenne(?:\s+(?:de\s+)?(?:groupe|classe))?)
    [\s:=of]*
    (\d{1,3}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

RE_GRADE = re.compile(
    r"""
    (?:got|grade[d]?|scored?|mark|note(?:\s+de)?|j'ai\s+eu|obtenu|résultat)
    [\s:=of]*
    (\d{1,3}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

RE_PROGRAM = re.compile(
    r"""
    (?:in|pour|en|for|cours?e?|programme?)\s+
    ([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-]{2,40})
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _first_match(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    if m:
        return m.group(1).replace(",", ".").strip()
    return None


def extract_fields(text: str) -> dict:
    return {
        "grade": _first_match(RE_GRADE, text),
        "class_average": _first_match(RE_CLASS_AVG, text),
        "r_score": _first_match(RE_RSCORE, text),
        "program_or_course": _first_match(RE_PROGRAM, text),
    }


def has_relevant_data(fields: dict) -> bool:
    return any(fields[k] is not None for k in ("grade", "class_average", "r_score"))

# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_or_init_csv(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_csv(path)
    return pd.DataFrame(columns=CSV_HEADERS)


def append_rows(path: str, rows: list[dict]) -> None:
    if not rows:
        log.info("No new rows to append.")
        return
    new_df = pd.DataFrame(rows, columns=CSV_HEADERS)
    existing = load_or_init_csv(path)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.drop_duplicates(subset=["source", "raw_text"], inplace=True)
    combined.to_csv(path, index=False)
    log.info("Saved %d rows → %s (total %d)", len(rows), path, len(combined))

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def get_json(url: str, params: dict | None = None) -> dict | list | None:
    """GET a URL, return parsed JSON, or None on any error."""
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 429:
            log.warning("HTTP 429 rate-limited on %s — sleeping 30 s", url)
            time.sleep(30)
            return None
        if resp.status_code != 200:
            log.warning("HTTP %d on %s", resp.status_code, url)
            return None
        return resp.json()
    except requests.RequestException as exc:
        log.error("Request failed for %s: %s", url, exc)
        return None

# ---------------------------------------------------------------------------
# Comment fetcher
# ---------------------------------------------------------------------------

def _walk_comments(comment_list: list, post_url: str, rows: list[dict]) -> None:
    """Recursively walk Reddit's comment tree and extract matching text."""
    for item in comment_list:
        if not isinstance(item, dict):
            continue
        data = item.get("data", {})
        # comment body
        body = data.get("body", "")
        if body and body not in ("[deleted]", "[removed]"):
            fields = extract_fields(body)
            if has_relevant_data(fields):
                rows.append({
                    "source": post_url,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "program_or_course": fields["program_or_course"],
                    "grade": fields["grade"],
                    "class_average": fields["class_average"],
                    "r_score": fields["r_score"],
                    "raw_text": body[:500],
                })
        # recurse into nested replies
        replies = data.get("replies", "")
        if isinstance(replies, dict):
            children = replies.get("data", {}).get("children", [])
            _walk_comments(children, post_url, rows)


def fetch_comments(subreddit: str, post_id: str, post_url: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    log.info("  Fetching comments: %s", url)
    time.sleep(REQUEST_DELAY)
    data = get_json(url)
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    rows: list[dict] = []
    comment_children = data[1].get("data", {}).get("children", [])
    _walk_comments(comment_children, post_url, rows)
    return rows

# ---------------------------------------------------------------------------
# Search + post processing
# ---------------------------------------------------------------------------

def search_subreddit(subreddit: str, query: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    params = {"q": query, "restrict_sr": "1", "limit": "100", "sort": "new"}
    log.info("Searching r/%s for '%s' …", subreddit, query)
    time.sleep(REQUEST_DELAY)
    data = get_json(url, params=params)
    if not data:
        return []

    posts = data.get("data", {}).get("children", [])
    log.info("  → %d posts returned", len(posts))

    rows: list[dict] = []
    seen_post_ids: set[str] = set()

    for post in posts:
        pdata = post.get("data", {})
        post_id = pdata.get("id", "")
        if not post_id or post_id in seen_post_ids:
            continue
        seen_post_ids.add(post_id)

        post_url = f"https://www.reddit.com{pdata.get('permalink', '')}"
        title = pdata.get("title", "")
        selftext = pdata.get("selftext", "")
        combined = f"{title} {selftext}".strip()

        # extract from post body
        fields = extract_fields(combined)
        if has_relevant_data(fields):
            rows.append({
                "source": post_url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "program_or_course": fields["program_or_course"],
                "grade": fields["grade"],
                "class_average": fields["class_average"],
                "r_score": fields["r_score"],
                "raw_text": combined[:500],
            })

        # fetch and extract from comments
        rows.extend(fetch_comments(subreddit, post_id, post_url))

    return rows

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    all_rows: list[dict] = []

    for subreddit in SUBREDDITS:
        for query in QUERIES:
            rows = search_subreddit(subreddit, query)
            log.info("  r/%s / '%s' → %d matched rows", subreddit, query, len(rows))
            all_rows.extend(rows)

    log.info("Total matched rows before dedup: %d", len(all_rows))
    append_rows(OUTPUT_CSV, all_rows)
    log.info("Done.")


if __name__ == "__main__":
    main()
