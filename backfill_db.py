"""
Backfill std_dev and credits for all existing rows.

Iterates the raw_text of every record in harvest.db, re-runs the updated
regex extractor, and UPDATEs std_dev / credits where new matches are found.
Only rows that gain at least one non-NULL value are written.
"""

import logging
import os
import sqlite3

from parsers.regex_extractor import extract_fields

log = logging.getLogger(__name__)
DB_PATH = os.getenv("DB_PATH", "data/harvest.db")


def backfill() -> dict:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    # Trigger the column migration so std_dev / credits exist before we write
    from storage.db import _conn  # noqa: F401 — side-effect: runs ALTER TABLE
    with _conn():
        pass

    total = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    log.info("Backfilling %d existing rows for std_dev / credits…", total)

    read_cur  = con.execute("SELECT id, raw_text FROM records")
    write_cur = con.cursor()
    updated = skipped = 0

    for row in read_cur:
        fields  = extract_fields(row["raw_text"] or "")
        std_dev = fields["std_dev"]
        credits = fields["credits"]

        if std_dev is None and credits is None:
            skipped += 1
            continue

        write_cur.execute(
            """UPDATE records
               SET std_dev = CASE WHEN :sd IS NOT NULL THEN :sd ELSE std_dev END,
                   credits = CASE WHEN :cr IS NOT NULL THEN :cr ELSE credits END
               WHERE id = :id""",
            {"sd": std_dev, "cr": credits, "id": row["id"]},
        )
        updated += 1
        log.info(
            "  updated %-20s  std_dev=%-6s  credits=%s",
            row["id"][:20], std_dev, credits,
        )

    con.commit()
    con.close()

    result = {"total": total, "updated": updated, "skipped": skipped}
    log.info(
        "Backfill complete — %d / %d rows updated, %d had no new matches",
        updated, total, skipped,
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    backfill()
