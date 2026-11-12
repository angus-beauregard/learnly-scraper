"""
TikTok web hashtag scraper — imagePost slideshow node.

Flow per hashtag:
  1. Playwright navigates to /tag/<hashtag>
  2. Response handler intercepts XHR feed calls (/api/challenge/item_list/,
     /api/search/item/) and accumulates imagePost CDN URLs in real time.
  3. Waits for [data-e2e="challenge-item"] to confirm feed has rendered,
     then scrolls slightly to trigger lazy-load.
  4. Primary extraction: walks __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON.
  5. Fallback: uses URLs captured by the response handler when primary yields 0.
  6. Downloads each CDN image (httpx) → preprocessor → OCR → omnivox parser
  7. Posts each valid row (r_score in [5,50]) to http://localhost:8000/ingest

Resource constraints (Pi 3):
  - Full asset blocking (images, fonts, stylesheets, media, analytics)
  - Feed XHR (fetch type) is NOT blocked — required for the response fallback
  - Viewport 1024×768
  - No video decoding — imagePost slides only
  - In-memory URL dedup set (_SEEN) prevents reprocessing across hashtags
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import urllib.parse

import httpx
from playwright.async_api import async_playwright

from parsers.omnivox_ocr_parser import parse_omnivox_table
from workers.ocr.ocr_engine import extract_text_from_slide
from workers.ocr.preprocessor import preprocess_transcript_image

log = logging.getLogger(__name__)

INGEST_URL = "http://localhost:8000/ingest"
HASHTAGS = ["coter", "omnivox", "cegep"]

# Session state candidates — tiktok_state.json (from save_session.py) preferred
_STATE_CANDIDATES = ("tiktok_state.json", "storage_state.json")


def _find_storage_state(override: str | None = None) -> str | None:
    """Return the first existing session-state file, or None."""
    if override:
        return override if os.path.exists(override) else None
    for path in _STATE_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}
_BLOCKED_HOSTS = (
    "google-analytics.com",
    "analytics.tiktok.com",
    "ads.tiktok.com",
    "pixel.tiktok.com",
    "log.tiktok.com",
    "mon.tiktok.com",
)

_REHYDRATION_RE = re.compile(
    r"window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*(\{.+?\});\s*</script",
    re.DOTALL,
)

# Module-level dedup: CDN URLs are unique per image — same URL = same pixel data
_SEEN: set[str] = set()
_slide_counter = 0


async def _block_route(route) -> None:
    req = route.request
    if req.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    if any(host in req.url for host in _BLOCKED_HOSTS):
        await route.abort()
        return
    await route.continue_()


def _collect_image_urls(obj, urls: list[str]) -> None:
    """Recursively walk the rehydration JSON for imagePost CDN URLs."""
    if isinstance(obj, dict):
        if "imagePost" in obj:
            ip = obj["imagePost"]
            if isinstance(ip, dict):
                for img in ip.get("images", []):
                    url_list = img.get("imageURL", {}).get("urlList", [])
                    if url_list:
                        urls.append(url_list[0])
        for v in obj.values():
            _collect_image_urls(v, urls)
    elif isinstance(obj, list):
        for item in obj:
            _collect_image_urls(item, urls)


async def _process_image(url: str, http: httpx.AsyncClient) -> int:
    """Download one CDN image, run OCR pipeline, dispatch valid rows.  Returns rows posted."""
    global _slide_counter
    if url in _SEEN:
        return 0
    _SEEN.add(url)
    posted = 0
    try:
        resp = await http.get(url, timeout=15.0)
        resp.raise_for_status()

        processed = preprocess_transcript_image(resp.content)
        raw_text = extract_text_from_slide(processed)
        log.info("[OCR Raw] %s...", raw_text[:150].replace("\n", " "))
        rows = parse_omnivox_table(raw_text)
        for row in rows:
            r = row.get("r_score")
            if r is None or not (5.0 <= r <= 50.0):
                continue
            parts: list[str] = []
            if row.get("course_code"):
                parts.append(f"course: {row['course_code']}")
            if row.get("credits") is not None:
                parts.append(f"credits: {int(row['credits'])}")
            if row.get("grade") is not None:
                parts.append(f"grade: {row['grade']}%")
            if row.get("class_average") is not None:
                parts.append(f"class average: {row['class_average']}")
            if row.get("std_dev") is not None:
                parts.append(f"std dev: {row['std_dev']}")
            parts.append(f"cote r {r}")

            payload = {
                "platform": "tiktok",
                "raw_text": "  ".join(parts),
                "is_tabular": True,
                "metadata": {"source_url": url, "parsed_row": row},
            }
            ingest_resp = await http.post(INGEST_URL, json=payload, timeout=10.0)
            if ingest_resp.status_code == 200:
                posted += 1
    except Exception:
        log.warning("Failed to process image %s", url, exc_info=True)
    return posted


async def _scrape_hashtag(
    tag: str, context, http: httpx.AsyncClient, limit: int | None = None
) -> int:
    """Scrape one hashtag page.  Returns total rows posted to /ingest."""
    page = await context.new_page()
    await page.route("**/*", _block_route)

    # Accumulate imagePost CDN URLs from XHR feed responses as they arrive.
    # Registered before goto() so responses during initial load are captured.
    intercepted_urls: list[str] = []

    async def _on_response(response) -> None:
        url = response.url
        if "/api/challenge/item_list/" not in url and "/api/search/item/" not in url:
            return
        try:
            data = await response.json()
            _collect_image_urls(data, intercepted_urls)
        except Exception:
            pass

    page.on("response", _on_response)

    total_posted = 0
    try:
        nav_url = f"https://www.tiktok.com/tag/{tag}"
        log.info("Navigating to %s", nav_url)
        await page.goto(nav_url, wait_until="domcontentloaded", timeout=30_000)

        try:
            await page.wait_for_selector('[data-e2e="challenge-item"]', timeout=15_000)
        except Exception:
            log.info("#%s: challenge-item selector timed out — continuing anyway", tag)

        await page.mouse.wheel(0, 1000)
        await page.wait_for_timeout(2_000)

        content = await page.content()
        image_urls: list[str] = []
        m = _REHYDRATION_RE.search(content)
        if m:
            _collect_image_urls(json.loads(m.group(1)), image_urls)

        if not image_urls:
            log.info(
                "#%s: rehydration yielded 0 URLs — falling back to %d XHR-intercepted URLs",
                tag, len(intercepted_urls),
            )
            image_urls = intercepted_urls

        if limit is not None:
            image_urls = image_urls[:limit]
        log.info("#%s: processing %d image slideshow URLs (limit=%s)", tag, len(image_urls), limit)

        for img_url in image_urls:
            posted = await _process_image(img_url, http)
            total_posted += posted

    except Exception:
        log.warning("Error scraping #%s", tag, exc_info=True)
    finally:
        await page.close()
    return total_posted


async def _scrape_search(
    query: str, context, http: httpx.AsyncClient, limit: int | None = None
) -> int:
    """Scrape one TikTok search results page.  Returns total rows posted to /ingest."""
    page = await context.new_page()
    await page.route("**/*", _block_route)

    intercepted_urls: list[str] = []

    async def _on_response(response) -> None:
        url = response.url
        if "/api/challenge/item_list/" not in url and "/api/search/item/" not in url:
            return
        try:
            data = await response.json()
            _collect_image_urls(data, intercepted_urls)
        except Exception:
            pass

    page.on("response", _on_response)

    total_posted = 0
    try:
        nav_url = f"https://www.tiktok.com/search?q={urllib.parse.quote(query)}"
        log.info("Navigating to %s", nav_url)
        await page.goto(nav_url, wait_until="domcontentloaded", timeout=30_000)

        try:
            await page.wait_for_selector('[data-e2e="search-item"]', timeout=15_000)
        except Exception:
            log.info("search %r: search-item selector timed out — continuing anyway", query)

        await page.mouse.wheel(0, 1000)
        await page.wait_for_timeout(2_000)

        content = await page.content()
        image_urls: list[str] = []
        m = _REHYDRATION_RE.search(content)
        if m:
            _collect_image_urls(json.loads(m.group(1)), image_urls)

        if not image_urls:
            log.info(
                "search %r: rehydration yielded 0 URLs — falling back to %d XHR-intercepted URLs",
                query, len(intercepted_urls),
            )
            image_urls = intercepted_urls

        if limit is not None:
            image_urls = image_urls[:limit]
        log.info("search %r: processing %d image slideshow URLs (limit=%s)", query, len(image_urls), limit)

        for img_url in image_urls:
            posted = await _process_image(img_url, http)
            total_posted += posted

    except Exception:
        log.warning("Error scraping search %r", query, exc_info=True)
    finally:
        await page.close()
    return total_posted


async def run(
    headless: bool = True,
    storage_state_path: str | None = None,
    hashtags: list[str] | None = None,
    searches: list[str] | None = None,
    limit: int | None = None,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    state = _find_storage_state(storage_state_path)
    if state:
        log.info("Loading session state from %s", state)
    else:
        log.warning("No session state found — TikTok may serve a login wall")

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--no-first-run",
        "--disable-renderer-backgrounding",
        "--disable-dev-shm-usage",
    ]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, args=launch_args)
        ctx_kwargs: dict = {
            "viewport": {"width": 1024, "height": 768},
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if state:
            ctx_kwargs["storage_state"] = state
        context = await browser.new_context(**ctx_kwargs)

        # If only --search was given, don't also run the default hashtag list
        tags    = hashtags if hashtags is not None else ([] if searches else HASHTAGS)
        queries = searches or []

        async with httpx.AsyncClient() as http:
            grand_total = 0
            for tag in tags:
                posted = await _scrape_hashtag(tag, context, http, limit=limit)
                log.info("#%s: posted %d rows to /ingest", tag, posted)
                grand_total += posted
            for query in queries:
                posted = await _scrape_search(query, context, http, limit=limit)
                log.info("search %r: posted %d rows to /ingest", query, posted)
                grand_total += posted

        await context.close()
        await browser.close()
        log.info("Done — %d rows posted across all targets", grand_total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TikTok web hashtag scraper")
    parser.add_argument(
        "--storage-state",
        metavar="PATH",
        default=None,
        help="Path to Playwright session-state JSON (default: auto-detect tiktok_state.json or storage_state.json)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run browser with a visible window (useful for manual CAPTCHA interaction)",
    )
    parser.add_argument(
        "--hashtag",
        metavar="TAG",
        default=None,
        help="Single hashtag to scrape instead of the default list (e.g. coter)",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="Maximum number of image URLs to process per target",
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        default=None,
        help="Search query (navigates to /search?q=QUERY instead of a hashtag page)",
    )
    args = parser.parse_args()
    asyncio.run(run(
        headless=not args.headful,
        storage_state_path=args.storage_state,
        hashtags=[args.hashtag] if args.hashtag else None,
        searches=[args.search] if args.search else None,
        limit=args.limit,
    ))
