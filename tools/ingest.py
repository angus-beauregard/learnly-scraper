"""
Generic JSON ingestion utility.

Reads an arbitrary JSON dump (from mobile scrapers, Discord exports, CSV
row dumps, etc.), extracts text from each record, runs it through the
bilingual regex parser, and writes results to the SQLite database.

The tool auto-detects the text field and handles several common JSON shapes:

  Array of objects:
    [{"text": "..."}, {"body": "...", "author": "user"}]

  Object with a list container:
    {"posts": [...], "metadata": {...}}    → uses "posts"
    {"data": {"children": [...]}}          → uses "children"

  Single object:
    {"text": "...", "source": "discord"}

  Nested (one level):
    [[{...}, {...}], [{...}]]              → flattened

Usage:
    python -m tools.ingest data.json
    cat data.json | python -m tools.ingest --stdin
    python -m tools.ingest data.json --platform discord --text-key message
    python -m tools.ingest data.json --dry-run
    python -m tools.ingest data.json --stats-only
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from parsers.regex_extractor import extract_fields
from storage.db import insert_record, make_id

log = logging.getLogger(__name__)

# Candidate keys tried in priority order when --text-key is not given
_TEXT_KEYS   = ["text", "body", "content", "selftext", "message", "post",
                "description", "caption", "comment", "value"]
_TS_KEYS     = ["timestamp", "created_at", "created_utc", "time", "date",
                "posted_at", "created", "published_at"]
_AUTHOR_KEYS = ["author", "username", "user", "poster", "from", "name",
                "screen_name", "handle"]
_URL_KEYS    = ["url", "link", "permalink", "source", "href"]

# Container keys whose value is a list that holds the actual records
_CONTAINER_KEYS = ["posts", "items", "data", "results", "records",
                   "comments", "messages", "children", "entries"]


# ---------------------------------------------------------------------------
# JSON shape normalisation
# ---------------------------------------------------------------------------

def _find_field(obj: dict, keys: list[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    return None


def _flatten(data: Any) -> list[dict]:
    """Coerce any supported JSON shape into a flat list of dicts."""
    if isinstance(data, list):
        items: list = data
    elif isinstance(data, dict):
        # Traverse one level of container keys
        for key in _CONTAINER_KEYS:
            val = data.get(key)
            if isinstance(val, list):
                items = val
                break
            # Reddit-style: data.children
            if isinstance(val, dict):
                inner = val.get("children")
                if isinstance(inner, list):
                    items = inner
                    break
        else:
            items = [data]
    else:
        return []

    flat: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            flat.append(item)
        elif isinstance(item, list):
            flat.extend(d for d in item if isinstance(d, dict))
    return flat


# ---------------------------------------------------------------------------
# Core ingestion logic (also importable by other modules)
# ---------------------------------------------------------------------------

def ingest(
    data: Any,
    *,
    platform: str = "external",
    text_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Parse and ingest a JSON payload.

    Returns a summary dict:
        total      — number of items found in the payload
        inserted   — new rows written to DB (0 when dry_run=True)
        skipped    — duplicates or items with no usable text
        matched    — items where ≥1 regex field (r_score/grade/class_average) matched
    """
    items = _flatten(data)
    inserted = skipped = matched = 0

    for item in items:
        # ── text extraction ────────────────────────────────────────────────
        if text_key:
            raw_text = item.get(text_key, "")
        else:
            raw_text = _find_field(item, _TEXT_KEYS) or ""

        if not isinstance(raw_text, str):
            raw_text = str(raw_text)
        raw_text = raw_text.strip()

        if len(raw_text) < 10:
            skipped += 1
            continue

        # ── regex parsing ──────────────────────────────────────────────────
        fields = extract_fields(raw_text)
        has_match = any(fields[k] is not None
                        for k in ("r_score", "grade", "class_average"))
        if has_match:
            matched += 1

        if dry_run:
            log.info(
                "[dry-run] r=%-6s  grade=%-6s  avg=%-6s  cegep=%-14s  %s…",
                fields["r_score"], fields["grade"], fields["class_average"],
                fields["cegep"] or "—",
                raw_text[:60].replace("\n", " "),
            )
            skipped += 1
            continue

        # ── metadata extraction ────────────────────────────────────────────
        author_raw = _find_field(item, _AUTHOR_KEYS)
        author = str(author_raw) if author_raw else None
        url    = _find_field(item, _URL_KEYS)

        ts_raw = _find_field(item, _TS_KEYS)
        if ts_raw is not None:
            try:
                ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc).isoformat()
            except (TypeError, ValueError):
                ts = str(ts_raw)
        else:
            ts = datetime.now(timezone.utc).isoformat()

        # Deterministic ID: prefer an explicit "id" field, else hash text+author
        source_id = str(item.get("id") or _text_hash(raw_text, author))

        was_new = insert_record({
            "id":            make_id(platform, source_id),
            "platform":      platform,
            "timestamp":     ts,
            "author":        author,
            "url":           url,
            "cegep":         fields["cegep"],
            "course":        fields["course"],
            "grade":         fields["grade"],
            "class_average": fields["class_average"],
            "r_score":       fields["r_score"],
            "std_dev":       fields["std_dev"],
            "credits":       fields["credits"],
            "high_school":   fields["high_school"],
            "raw_text":      raw_text[:2000],
        })

        if was_new:
            inserted += 1
            log.info(
                "+ [%s] r=%-6s  grade=%-6s  avg=%-6s  cegep=%s",
                platform,
                fields["r_score"], fields["grade"],
                fields["class_average"], fields["cegep"],
            )
        else:
            skipped += 1

    return {
        "total":    len(items),
        "inserted": inserted,
        "skipped":  skipped,
        "matched":  matched,
    }


def _text_hash(text: str, author: str | None) -> str:
    key = f"{author or ''}:{text[:200]}"
    return hashlib.sha1(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "file", nargs="?",
        help="JSON file to ingest (omit or use --stdin to read from stdin)",
    )
    p.add_argument("--stdin",      action="store_true", help="Read JSON from stdin")
    p.add_argument("--platform",   default="external",  help="Platform label stored in DB (default: external)")
    p.add_argument("--text-key",   dest="text_key",     default=None,
                   help="JSON key containing the text to parse (auto-detected if omitted)")
    p.add_argument("--dry-run",    action="store_true", help="Parse and log matches without writing to DB")
    p.add_argument("--stats-only", action="store_true", help="Print field-match statistics without writing to DB")
    args = p.parse_args(argv)

    # ── read input ─────────────────────────────────────────────────────────
    if args.stdin or not args.file:
        log.info("Reading JSON from stdin…")
        raw = sys.stdin.read()
    else:
        log.info("Reading %s", args.file)
        with open(args.file, encoding="utf-8") as fh:
            raw = fh.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON: %s", exc)
        sys.exit(1)

    # ── ingest ────────────────────────────────────────────────────────────
    result = ingest(
        data,
        platform=args.platform,
        text_key=args.text_key,
        dry_run=args.dry_run or args.stats_only,
    )

    mode = "[DRY RUN] " if (args.dry_run or args.stats_only) else ""
    log.info(
        "%sDone — total=%d  inserted=%d  already_existed=%d  matched_fields=%d",
        mode,
        result["total"],
        result["inserted"],
        result["skipped"],
        result["matched"],
    )


if __name__ == "__main__":
    main()
