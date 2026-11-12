"""
SQLite storage with WAL mode — optimised for low-memory ARM targets.
Every public function opens and closes its own connection so the caller
never needs to manage connection lifetime.
"""

import csv
import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/harvest.db")

_DDL = """
CREATE TABLE IF NOT EXISTS records (
    id            TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,
    timestamp     TEXT,
    author        TEXT,
    url           TEXT,
    cegep         TEXT,
    course        TEXT,
    grade         REAL,
    class_average REAL,
    r_score       REAL,
    std_dev       REAL,
    credits       REAL,
    high_school   TEXT,
    raw_text      TEXT NOT NULL,
    created_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_platform ON records(platform);
CREATE INDEX IF NOT EXISTS idx_cegep    ON records(cegep);
CREATE INDEX IF NOT EXISTS idx_r_score  ON records(r_score);
"""

_INSERT = """
INSERT OR IGNORE INTO records
  (id, platform, timestamp, author, url, cegep, course,
   grade, class_average, r_score, std_dev, credits, high_school, raw_text)
VALUES
  (:id, :platform, :timestamp, :author, :url, :cegep, :course,
   :grade, :class_average, :r_score, :std_dev, :credits, :high_school, :raw_text)
"""


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL = writers don't block readers; NORMAL sync is safe enough for a Pi SD card
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_DDL)
    # Idempotent column migration — adds std_dev / credits to pre-existing tables
    existing = {row[1] for row in con.execute("PRAGMA table_info(records)")}
    for col, typedef in [("std_dev", "REAL"), ("credits", "REAL"), ("high_school", "TEXT")]:
        if col not in existing:
            con.execute(f"ALTER TABLE records ADD COLUMN {col} {typedef}")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def make_id(platform: str, source_id: str) -> str:
    """Deterministic composite primary key so re-runs never duplicate rows."""
    return hashlib.sha1(f"{platform}:{source_id}".encode()).hexdigest()


def insert_record(record: dict) -> bool:
    """Write one record. Returns True if inserted, False if already present."""
    full = {"std_dev": None, "credits": None, "high_school": None, **record}
    with _conn() as con:
        cur = con.execute(_INSERT, full)
        return cur.rowcount > 0


def row_count() -> int:
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM records").fetchone()[0]


def export_csv(output_path: str) -> int:
    """Stream all records to CSV without loading the full table into RAM."""
    count = 0
    with _conn() as con:
        cur = con.execute("SELECT * FROM records ORDER BY created_at")
        columns = [d[0] for d in cur.description]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in cur:
                writer.writerow(tuple(row))
                count += 1
    log.info("Exported %d rows → %s", count, output_path)
    return count


def export_jsonl(output_path: str) -> int:
    """Stream all records to newline-delimited JSON (one object per line)."""
    count = 0
    with _conn() as con:
        cur = con.execute("SELECT * FROM records ORDER BY created_at")
        with open(output_path, "w", encoding="utf-8") as f:
            for row in cur:
                f.write(json.dumps(dict(row)) + "\n")
                count += 1
    log.info("Exported %d rows → %s", count, output_path)
    return count
