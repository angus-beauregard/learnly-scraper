"""
FastAPI ingest receiver — port 8000.

Accepts raw text payloads from external scrapers (iOS Appium, mobile, etc.),
runs them through the bilingual regex parser, and writes results to SQLite.

Endpoints:
  POST /ingest   — parse + store a single record
  GET  /health   — liveness check with DB row count
  GET  /stats    — field-level coverage summary
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from parsers.regex_extractor import extract_fields
from storage.db import insert_record, make_id, row_count

log = logging.getLogger(__name__)

app = FastAPI(
    title="Learnly Ingest API",
    description="Receives raw text from external scrapers and stores extracted academic fields.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    platform: str
    raw_text: str
    author:    str | None = None
    url:       str | None = None
    timestamp: str | None = None   # ISO-8601 or Unix epoch string

    @field_validator("raw_text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        if not v or len(v.strip()) < 5:
            raise ValueError("raw_text must be at least 5 characters")
        return v.strip()

    @field_validator("platform")
    @classmethod
    def platform_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("platform must not be empty")
        return v.strip().lower()


class IngestResponse(BaseModel):
    inserted:    bool
    platform:    str
    fields:      dict
    record_id:   str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_timestamp(ts: str | None) -> str:
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return ts


def _source_id(platform: str, raw_text: str) -> str:
    return hashlib.sha1(f"{platform}:{raw_text[:200]}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/ingest", response_model=IngestResponse, status_code=200)
def ingest(payload: IngestRequest) -> IngestResponse:
    fields = extract_fields(payload.raw_text)
    source_id = _source_id(payload.platform, payload.raw_text)
    rec_id    = make_id(payload.platform, source_id)

    was_new = insert_record({
        "id":            rec_id,
        "platform":      payload.platform,
        "timestamp":     _resolve_timestamp(payload.timestamp),
        "author":        payload.author,
        "url":           payload.url,
        "cegep":         fields["cegep"],
        "course":        fields["course"],
        "grade":         fields["grade"],
        "class_average": fields["class_average"],
        "r_score":       fields["r_score"],
        "std_dev":       fields["std_dev"],
        "credits":       fields["credits"],
        "high_school":   fields["high_school"],
        "raw_text":      payload.raw_text[:2000],
    })

    log.info(
        "[%s] inserted=%s  r=%s  grade=%s  avg=%s  hs=%s",
        payload.platform, was_new,
        fields["r_score"], fields["grade"],
        fields["class_average"], fields["high_school"],
    )
    return IngestResponse(
        inserted=was_new,
        platform=payload.platform,
        fields=fields,
        record_id=rec_id,
    )


@app.get("/health")
def health() -> dict:
    try:
        total = row_count()
        return {"status": "ok", "total_records": total}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/stats")
def stats() -> dict:
    import sqlite3, os
    db_path = os.getenv("DB_PATH", "data/harvest.db")
    try:
        con = sqlite3.connect(db_path)
        row = con.execute("""
            SELECT
                COUNT(*)                                            AS total,
                SUM(CASE WHEN r_score       IS NOT NULL THEN 1 ELSE 0 END) AS has_r_score,
                SUM(CASE WHEN grade         IS NOT NULL THEN 1 ELSE 0 END) AS has_grade,
                SUM(CASE WHEN class_average IS NOT NULL THEN 1 ELSE 0 END) AS has_class_avg,
                SUM(CASE WHEN std_dev       IS NOT NULL THEN 1 ELSE 0 END) AS has_std_dev,
                SUM(CASE WHEN credits       IS NOT NULL THEN 1 ELSE 0 END) AS has_credits,
                SUM(CASE WHEN high_school   IS NOT NULL THEN 1 ELSE 0 END) AS has_high_school,
                SUM(CASE WHEN cegep         IS NOT NULL THEN 1 ELSE 0 END) AS has_cegep
            FROM records
        """).fetchone()
        con.close()
        keys = ["total","has_r_score","has_grade","has_class_avg",
                "has_std_dev","has_credits","has_high_school","has_cegep"]
        return dict(zip(keys, row))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Dev entry point  (python api/receiver.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    uvicorn.run("api.receiver:app", host="0.0.0.0", port=8000, reload=True)
