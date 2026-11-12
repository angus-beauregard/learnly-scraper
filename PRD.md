# Product Requirements Document (PRD)
**Project:** Learnly R-Score Data Pipeline
**Objective:** Build a multi-platform, automated data collection pipeline to harvest self-reported Quebec CEGEP academic records (Grades, Averages, R-Scores, Standard Deviations, Credits, High Schools, and CEGEPs). This data will feed into Learnly's regression models to reverse-engineer proprietary Ministry of Education variables ($IDGZ$ and $ISGZ$).

---

## 1. System Architecture
A hybrid data collection engine utilizing a centralized SQLite database, a Python-based regex parsing core, headless desktop scrapers (Playwright), and mobile hardware nodes (iOS/Appium) pushing payloads to a local API network.

---

## 2. Current State (What's Already Made)

### 2.1 Storage & Schema (`storage/db.py`)
* **SQLite Database:** Configured with WAL (Write-Ahead Logging) for concurrent access and idempotent schema migrations.
* **Schema Built:** 
  * Primary fields: `r_score`, `grade`, `class_average`, `cegep`.
  * Advanced fields: `std_dev`, `credits`, `high_school`.
* **Deduplication:** Uses `INSERT OR IGNORE` logic via unique hashes to prevent duplicate entries from overlapping scraping runs.

### 2.2 Extraction Engine (`parsers/regex_extractor.py`)
* **Bilingual Regex Parsing:** Successfully identifies and extracts numerical and string data across French and English text.
* **Flexible Matching:** Handles both forward ("cote r 35.2") and inverted ("35.2 cote r") linguistic structures without false positives on course codes (e.g., BIO 101).
* **Named Entity Recognition:** Detects CEGEP names and targeted high schools (e.g., Brébeuf, Grasset) using exact lists and keyword fallbacks.

### 2.3 Web Harvesting (`extractors/`)
* **Playwright Session Manager:** Bypasses 403 Forbidden blocks and rate limits via real-browser DOM rendering and `storage_state.json` cookie persistence.
* **Reddit Crawler:** Navigates search results, target subreddits, and successfully traverses deep/nested comment trees to capture granular grade breakdowns.
* **Resource Blocking:** Configured to block images/CSS/fonts to keep RAM usage under ~150MB for future ARM deployment.

### 2.4 Ingestion API & Infrastructure (`api/receiver.py` & Docker)
* **Local Ingestion Server:** FastAPI server running on port `8000` with a `/ingest` POST endpoint for external hardware (mobile scrapers) to push JSON payloads.
* **Monitoring:** `/stats` and `/health` endpoints built to track data volume across all regex fields.
* **Containerization:** Multi-stage `Dockerfile` and `docker-compose.yml` prepped for `linux/arm/v7` and `arm64`, configured with persistent volume mounts (`/app/data`).

---

## 3. Pending Implementation (What Needs to be Added / Setup)

### 3.1 Phase 1: The iOS TikTok Node (Mobile Scraping)
* **Hardware:** Spare iPhone 8 Plus with Apple Developer Account.
* **Appium Infrastructure:** Install Appium (`appium-python-client`) and deploy `WebDriverAgent` to the iOS device via Xcode.
* **Script (`tiktok_appium.py`):** 
  * Automate TikTok app launch on the iPhone.
  * Search target hashtags (`#coteR`, `#cegep`, `#dawson`).
  * Implement randomized auto-scroll (swiping up).
  * Extract text from comment UI accessibility nodes.
  * Construct JSON payload and send via `POST` to the MacBook's local IP (`http://<mac-ip>:8000/ingest`).

### 3.2 Phase 2: CasaOS / Raspberry Pi Deployment
* **Migration:** Transfer the Dockerized application from the MacBook Air to the Raspberry Pi 3 CasaOS cluster.
* **Container Execution:** Build and spin up the headless API and database node via `docker-compose`.
* **Automation:** Configure a cron job or CasaOS task scheduler to execute the Playwright (Reddit/Facebook/Discord) scraper on a weekly interval.
* **Network Rerouting:** Update the iOS Appium script to point its POST requests to the Raspberry Pi's local IP address instead of the MacBook.

### 3.3 Phase 3: Facebook & Discord Expansion
* **Target Mapping:** Manually join high-density Facebook groups (e.g., CEGEP Spotted pages, university admission groups) and Discord servers using the `session_manager.py` browser window.
* **Scraper Updates:** Add the resulting URLs to `TARGET_PAGES` inside `playwright_scraper.py` and refine DOM selectors if necessary to support Facebook/Discord layouts.

### 3.4 Phase 4: OCR Integration (Future Enhancement)
* **Image Recognition:** Integrate `pytesseract` or `easyocr` into the ingestion pipeline.
* **Logic:** When the Playwright scraper detects a transcript screenshot or image attachment, download it locally, extract the text, and pipe the string through `regex_extractor.py`.

### 3.5 Phase 5: Downstream Data Science
* **Data Export:** Generate clean CSV exports from the SQLite database.
* **Regression Modeling:** Feed the sanitized `(Grade, Average, StdDev, Credits, R-Score)` tuples into Learnly's inverse regression engine to map $ISGZ$ and $IDGZ$ parameters by cohort year.