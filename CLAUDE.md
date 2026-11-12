# Learnly R-Score Data Pipeline
**Primary Goal:** Harvest self-reported academic data to reverse-engineer Quebec Ministry of Education R-Score parameters ($IDGZ$ and $ISGZ$).

## System Architecture
* **Scraping Engine:** Headless Playwright (authenticated via `storage_state.json`), Reddit crawler (handling nested comments), and future Appium mobile nodes.
* **Extraction Engine:** `parsers/regex_extractor.py` handles bilingual text parsing, guarding against false positives (like course codes), and handles both forward ("cote r 35.2") and inverted ("35.2 cote r") linguistic structures.
* **Ingestion API:** `api/receiver.py` (FastAPI) listens on port 8000 for JSON payloads `{"platform": "...", "raw_text": "..."}` from mobile devices and external scripts.
* **Database:** SQLite in WAL mode (`data/harvest.db`) for concurrent reads/writes and idempotent migrations.
* **Infrastructure:** Multi-stage Docker build optimized for CasaOS on Raspberry Pi 3 (`linux/arm/v7` / `arm64`).

## Database Schema (`records` table)
Whenever writing new ingestion scripts, aim to populate these fields via the regex extractor:
* `id` (Primary Key, unique hash)
* `platform` (TEXT)
* `raw_text` (TEXT)
* `r_score` (REAL)
* `grade` (REAL)
* `class_average` (REAL)
* `cegep` (TEXT)
* `std_dev` (REAL)
* `credits` (REAL)
* `high_school` (TEXT)

## Core Commands (Cheat Sheet)
**1. Run Local API Receiver (for mobile scraper testing):**
```bash
uvicorn api.receiver:app --host 0.0.0.0 --port 8000
```

**2. Run Headless Desktop Scraper (Reddit/Facebook/Discord):**
```bash
python3 -m extractors.playwright_scraper
```
*(Note: Requires `storage_state.json` generated via `python3 session_manager.py`)*

**3. Check Database Coverage & Stats:**
```bash
curl http://localhost:8000/stats
# OR via SQLite directly:
sqlite3 -header -column data/harvest.db "SELECT platform, COUNT(*) AS total, SUM(CASE WHEN r_score IS NOT NULL THEN 1 ELSE 0 END) AS has_r_score, SUM(CASE WHEN high_school IS NOT NULL THEN 1 ELSE 0 END) AS has_hs FROM records GROUP BY platform;"
```

**4. Deploy to Docker / CasaOS:**
```bash
TARGETARCH=arm docker-compose up -d --build
```

## AI Agent Rules (Strict Directives)
1. **Idempotency:** Always use `INSERT OR IGNORE` for DB writes. Schema updates must use safe `ALTER TABLE ... ADD COLUMN` statements wrapped in try/except blocks to prevent failure on existing tables.
2. **Regex Updates:** When updating `parsers/regex_extractor.py`, ensure backward compatibility. Use `\b` guards to prevent false matches (e.g., catching 01 in "BIO 101"), and always account for bilingual (French/English) variations.
3. **Resource Efficiency:** The final target hardware is a Raspberry Pi 3. Ensure memory usage stays low (e.g., block resources in Playwright, use SQLite read cursors for large iterations). 
4. **Context Preservation:** Never overwrite `TARGET_PAGES` with dummy data if existing URLs (Reddit, Dawson, McGill) are already configured.