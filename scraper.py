"""
Learnly R-Score Scraper
Pulls CEGEP grade/average/R-score data from Reddit and appends it to a CSV.
"""

import os
import re
import time
import logging
from datetime import datetime, timezone

import praw
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUBREDDITS = ["cegepcoteR", "Dawson", "mcgill"]
OUTPUT_CSV = os.getenv("OUTPUT_CSV", "rscore_data.csv")
CSV_HEADERS = ["source", "timestamp", "program_or_course", "grade", "class_average", "r_score", "raw_text"]

# How many top/new posts to fetch per subreddit per run
POST_LIMIT = 100

# Politeness delay between subreddit fetches (seconds)
INTER_SUBREDDIT_DELAY = 2


# ---------------------------------------------------------------------------
# Regex patterns  (bilingual: English + French)
# ---------------------------------------------------------------------------

# R-score / Cote R  — e.g. "R-score of 32.5", "cote r: 31", "CRC 30.8", "cote-r 29"
RE_RSCORE = re.compile(
    r"""
    (?:r[\s\-]?score|cote[\s\-]?r|crc)   # label
    [\s:=of]*                              # separator
    (\d{1,2}(?:[.,]\d{1,2})?)             # value  e.g. 32 or 32.5 or 32,5
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Class average / Moyenne de groupe  — e.g. "class avg 72%", "moyenne 68.5", "group avg: 75"
RE_CLASS_AVG = re.compile(
    r"""
    (?:class\s+avg(?:erage)?|group\s+avg(?:erage)?|moyenne(?:\s+(?:de\s+)?(?:groupe|classe))?)
    [\s:=of]*
    (\d{1,3}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Individual grade  — e.g. "got 85%", "grade: 78", "j'ai eu 92%", "note de 88"
RE_GRADE = re.compile(
    r"""
    (?:got|grade[d]?|scored?|mark|note(?:\s+de)?|j'ai\s+eu|obtenu|résultat)
    [\s:=of]*
    (\d{1,3}(?:[.,]\d{1,2})?)
    \s*%?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Program / course name heuristic  — grabs words following "in", "pour", "en", "for"
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
        return
    new_df = pd.DataFrame(rows, columns=CSV_HEADERS)
    existing = load_or_init_csv(path)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.drop_duplicates(subset=["source", "raw_text"], inplace=True)
    combined.to_csv(path, index=False)
    log.info("Appended %d new rows → %s (total %d)", len(rows), path, len(combined))


# ---------------------------------------------------------------------------
# Reddit auth
# ---------------------------------------------------------------------------

def build_reddit_client() -> praw.Reddit:
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "learnly-scraper/1.0")

    if not client_id or not client_secret:
        raise EnvironmentError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set in .env"
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


# ---------------------------------------------------------------------------
# Scraping logic
# ---------------------------------------------------------------------------

def scrape_submission(submission, subreddit_name: str) -> list[dict]:
    """Extract rows from a submission's title, selftext, and all comments."""
    rows = []

    def process_text(text: str, url: str) -> None:
        if not text or not text.strip():
            return
        fields = extract_fields(text)
        if has_relevant_data(fields):
            rows.append({
                "source": url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "program_or_course": fields["program_or_course"],
                "grade": fields["grade"],
                "class_average": fields["class_average"],
                "r_score": fields["r_score"],
                "raw_text": text[:500],
            })

    post_url = f"https://reddit.com{submission.permalink}"
    process_text(submission.title + " " + (submission.selftext or ""), post_url)

    try:
        submission.comments.replace_more(limit=0)
        for comment in submission.comments.list():
            process_text(comment.body, post_url)
    except Exception as exc:
        log.warning("Could not fetch comments for %s: %s", post_url, exc)

    return rows


def scrape_subreddit(reddit: praw.Reddit, subreddit_name: str) -> list[dict]:
    rows = []
    log.info("Scraping r/%s …", subreddit_name)
    try:
        sub = reddit.subreddit(subreddit_name)
        for submission in sub.new(limit=POST_LIMIT):
            try:
                rows.extend(scrape_submission(submission, subreddit_name))
                time.sleep(0.5)  # stay well inside Reddit's rate limit
            except Exception as exc:
                log.warning("Error on submission %s: %s", submission.id, exc)
    except Exception as exc:
        log.error("Failed to access r/%s: %s", subreddit_name, exc)
    log.info("r/%s → %d candidate rows", subreddit_name, len(rows))
    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    reddit = build_reddit_client()
    all_rows: list[dict] = []

    for subreddit_name in SUBREDDITS:
        all_rows.extend(scrape_subreddit(reddit, subreddit_name))
        time.sleep(INTER_SUBREDDIT_DELAY)

    append_rows(OUTPUT_CSV, all_rows)
    log.info("Done. Total candidate rows this run: %d", len(all_rows))


if __name__ == "__main__":
    main()
