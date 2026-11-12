"""
Reddit unauthenticated public JSON scraper.

Uses .json endpoints — no API key required. Paginates with 'after' tokens,
fetches full comment trees, and writes each record directly to SQLite so
memory footprint stays flat regardless of dataset size.

NOTE: Reddit has progressively locked unauthenticated access. If all requests
return HTTP 403, the runtime environment's IP is blocked. In that case use
scraper.py (PRAW/OAuth) instead, or proxy the requests.
"""

import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

import requests

from parsers.regex_extractor import extract_fields
from storage.db import insert_record, make_id

log = logging.getLogger(__name__)

BASE = "https://www.reddit.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Referer": "https://www.reddit.com/",
}

SUBREDDITS = [
    "cegepcoteR", "Dawson", "mcgill", "VanierCollege",
    "JohnAbbottCollege", "Concordia", "cegep",
]

QUERIES = [
    "cote r", "r score", "r-score", "CRC",
    "moyenne", "cote de rendement",
    "NYA", "NYB", "NYC",
]

_MIN_DELAY = 1.2
_MAX_DELAY = 2.5


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _sleep() -> None:
    time.sleep(random.uniform(_MIN_DELAY, _MAX_DELAY))


def _get(session: requests.Session, url: str, params: dict | None = None):
    """GET with backoff; returns parsed JSON or None on any error."""
    try:
        resp = session.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        log.error("Request error %s: %s", url, exc)
        return None

    if resp.status_code == 429:
        wait = int(resp.headers.get("Retry-After", 60))
        log.warning("HTTP 429 — sleeping %ds then retrying", wait)
        time.sleep(wait)
        return _get(session, url, params)   # single retry after backoff

    if resp.status_code == 403:
        log.warning("HTTP 403 on %s — unauthenticated access blocked by Reddit", url)
        return None

    if resp.status_code != 200:
        log.warning("HTTP %d on %s", resp.status_code, url)
        return None

    try:
        return resp.json()
    except ValueError as exc:
        log.error("JSON decode error on %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Record construction
# ---------------------------------------------------------------------------

def _ts(created_utc) -> str | None:
    try:
        return datetime.fromtimestamp(float(created_utc), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _write_post(post: dict) -> None:
    post_id  = post.get("id", "")
    title    = post.get("title", "") or ""
    selftext = post.get("selftext", "") or ""
    text     = f"{title}\n{selftext}".strip()
    if not text or not post_id:
        return

    fields = extract_fields(text)
    inserted = insert_record({
        "id":            make_id("reddit", post_id),
        "platform":      "reddit",
        "timestamp":     _ts(post.get("created_utc")),
        "author":        post.get("author"),
        "url":           f"https://reddit.com{post.get('permalink', '')}",
        "cegep":         fields["cegep"],
        "course":        fields["course"],
        "grade":         fields["grade"],
        "class_average": fields["class_average"],
        "r_score":       fields["r_score"],
        "std_dev":       fields["std_dev"],
        "credits":       fields["credits"],
        "high_school":   fields["high_school"],
        "raw_text":      text[:2000],
    })
    if inserted:
        log.info(
            "+ post %s  cegep=%-12s grade=%-6s avg=%-6s crc=%s",
            post_id, fields["cegep"], fields["grade"],
            fields["class_average"], fields["r_score"],
        )


def _write_comment(comment: dict, post_url: str) -> None:
    body = comment.get("body", "") or ""
    if not body or body in ("[deleted]", "[removed]"):
        return

    comment_id = comment.get("id", "")
    if not comment_id:
        return

    fields = extract_fields(body)
    inserted = insert_record({
        "id":            make_id("reddit", comment_id),
        "platform":      "reddit",
        "timestamp":     _ts(comment.get("created_utc")),
        "author":        comment.get("author"),
        "url":           post_url,
        "cegep":         fields["cegep"],
        "course":        fields["course"],
        "grade":         fields["grade"],
        "class_average": fields["class_average"],
        "r_score":       fields["r_score"],
        "std_dev":       fields["std_dev"],
        "credits":       fields["credits"],
        "high_school":   fields["high_school"],
        "raw_text":      body[:2000],
    })
    if inserted:
        log.info("  + comment %s  crc=%s", comment_id, fields["r_score"])


# ---------------------------------------------------------------------------
# Comment tree
# ---------------------------------------------------------------------------

def _walk_comments(children: list, post_url: str) -> None:
    for item in children:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "t1":   # t1 = comment; skip "more" nodes
            continue
        data = item.get("data", {})
        _write_comment(data, post_url)
        replies = data.get("replies", "")
        if isinstance(replies, dict):
            nested = replies.get("data", {}).get("children", [])
            _walk_comments(nested, post_url)


def _fetch_comments(
    session: requests.Session, subreddit: str, post_id: str, post_url: str,
) -> None:
    url = f"{BASE}/r/{subreddit}/comments/{post_id}.json"
    _sleep()
    data = _get(session, url, params={"limit": 500, "depth": 10})
    if not data or not isinstance(data, list) or len(data) < 2:
        return
    children = data[1].get("data", {}).get("children", [])
    _walk_comments(children, post_url)


# ---------------------------------------------------------------------------
# Search pagination
# ---------------------------------------------------------------------------

def _search(session: requests.Session, subreddit: str, query: str) -> None:
    url   = f"{BASE}/r/{subreddit}/search.json"
    after = None
    page  = 0

    while True:
        params: dict = {
            "q":           query,
            "restrict_sr": "1",
            "sort":        "new",
            "limit":       "100",
            "t":           "all",
        }
        if after:
            params["after"] = after

        log.info("r/%-20s  q=%-22s  page=%d", subreddit, repr(query), page)
        _sleep()
        data = _get(session, url, params=params)
        if not data:
            break

        listing  = data.get("data", {})
        children = listing.get("children", [])
        if not children:
            break

        for child in children:
            if child.get("kind") != "t3":   # t3 = submission
                continue
            post = child.get("data", {})
            _write_post(post)
            pid      = post.get("id", "")
            post_url = f"https://reddit.com{post.get('permalink', '')}"
            num_comments = post.get("num_comments", 0)
            if pid and num_comments:
                _fetch_comments(session, subreddit, pid, post_url)

        after = listing.get("after")
        if not after:
            break
        page += 1


# ---------------------------------------------------------------------------
# PRAW mode — used when REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set
# ---------------------------------------------------------------------------

def _make_praw() -> Any | None:
    """Return a PRAW Reddit instance if credentials are available, else None."""
    cid    = os.getenv("REDDIT_CLIENT_ID")
    secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    try:
        import praw  # noqa: PLC0415
        kwargs: dict = dict(
            client_id=cid,
            client_secret=secret,
            user_agent="learnly-scraper:1.0 (harvesting CEGEP academic data)",
        )
        username = os.getenv("REDDIT_USERNAME")
        password = os.getenv("REDDIT_PASSWORD")
        if username and password:
            kwargs["username"] = username
            kwargs["password"] = password
        return praw.Reddit(**kwargs)
    except Exception as exc:
        log.warning("PRAW init failed (%s) — falling back to requests", exc)
        return None


def _walk_praw_comments(comments: list, post_url: str) -> None:
    """Write a flat list of PRAW Comment objects to DB."""
    for c in comments:
        body = getattr(c, "body", "") or ""
        if not body or body in ("[deleted]", "[removed]"):
            continue
        _write_comment({
            "id":           c.id,
            "body":         body,
            "created_utc":  getattr(c, "created_utc", None),
            "author":       str(c.author) if getattr(c, "author", None) else None,
        }, post_url)


def _run_praw(reddit: Any) -> None:
    for sub in SUBREDDITS:
        for q in QUERIES:
            log.info("PRAW r/%-20s  q=%s", sub, q)
            try:
                results = reddit.subreddit(sub).search(
                    q, sort="new", limit=100, time_filter="all",
                )
                for submission in results:
                    _write_post({
                        "id":           submission.id,
                        "title":        submission.title,
                        "selftext":     submission.selftext or "",
                        "created_utc":  submission.created_utc,
                        "author":       str(submission.author) if submission.author else None,
                        "permalink":    submission.permalink,
                    })
                    if submission.num_comments > 0:
                        post_url = f"https://reddit.com{submission.permalink}"
                        try:
                            # expand up to 3 "more" nodes; each is an API call
                            submission.comments.replace_more(limit=3)
                            _walk_praw_comments(submission.comments.list(), post_url)
                        except Exception as exc:
                            log.warning("  Comment error %s: %s", submission.id, exc)
                    _sleep()
            except Exception as exc:
                log.warning("PRAW search failed r/%s q=%s: %s", sub, q, exc)


def _run_requests() -> None:
    session = requests.Session()
    session.headers.update(_HEADERS)
    total_combos = len(SUBREDDITS) * len(QUERIES)
    log.info("Requests mode: %d subreddits × %d queries = %d combos",
             len(SUBREDDITS), len(QUERIES), total_combos)
    for sub in SUBREDDITS:
        for q in QUERIES:
            _search(session, sub, q)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    reddit = _make_praw()
    if reddit:
        log.info("PRAW credentials found — using authenticated API with full comment trees")
        _run_praw(reddit)
    else:
        log.info("No PRAW credentials — using unauthenticated JSON endpoints (may 403)")
        _run_requests()

    from storage.db import row_count
    log.info("Done. Total records in DB: %d", row_count())


if __name__ == "__main__":
    run()
