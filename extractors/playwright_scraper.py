"""
Two-mode scraper:

  REDDIT HTML MODE  — navigates reddit.com listing pages (no .json API needed).
                      Extracts shreddit-post web-component attributes + body text
                      from the light-DOM slot, then visits individual post pages
                      to harvest shreddit-comment trees.
                      Configured via REDDIT_TARGETS.

  DOM SCRAPING MODE — renders a page and extracts text via CSS selectors.
                      For authenticated platforms (Facebook, Discord, forums).
                      Configured via TARGET_PAGES.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright

from parsers.regex_extractor import extract_fields
from storage.db import insert_record, make_id

log = logging.getLogger(__name__)

STATE_FILE = Path("storage_state.json")

# ---------------------------------------------------------------------------
# Reddit HTML mode — subreddit listing targets
# ---------------------------------------------------------------------------
REDDIT_TARGETS: list[dict] = [
    # Dedicated CEGEP R-score subreddit — maximise scroll depth
    {"subreddit": "cegepcoteR",        "scroll_pages": 15},
    # School subreddits
    {"subreddit": "Dawson",            "scroll_pages": 5},
    {"subreddit": "cegep",             "scroll_pages": 5},
    # Larger subreddits need extra load_wait for shreddit-post to render
    {"subreddit": "mcgill",            "scroll_pages": 3,  "load_wait": 6},
    {"subreddit": "VanierCollege",     "scroll_pages": 3,  "load_wait": 6},
    {"subreddit": "JohnAbbottCollege", "scroll_pages": 3,  "load_wait": 6},
    {"subreddit": "Concordia",         "scroll_pages": 3,  "load_wait": 6},
]

# Max post-comment-pages to visit per subreddit (rate-limiting safeguard)
_MAX_COMMENT_PAGES = 50

# Scroll iterations within each post's comment page before giving up
_MAX_COMMENT_SCROLLS = 10

# ---------------------------------------------------------------------------
# DOM scraping mode — authenticated platforms (Facebook, Discord, forums)
# ---------------------------------------------------------------------------
TARGET_PAGES: list[dict] = [
    # Add URLs here after running session_manager.py:
    # {"url": "https://www.facebook.com/groups/YOUR_GROUP_ID",
    #  "platform": "facebook", "scroll_pages": 5},
]

_DOM_SELECTORS = [
    "[data-testid='post_message']",   # Facebook post body
    "[data-ad-preview='message']",    # Facebook (ad-free variant)
    "article",                        # generic
    ".message-content",               # Discord-style
]

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}

_LOGIN_WALL_SIGNALS = [
    "log in", "sign in", "create an account",
    "login required", "connexion requise",
]

_INTER_PAGE_DELAY = (1.5, 2.8)   # seconds between HTTP requests


# ---------------------------------------------------------------------------
# JavaScript extractors — confirmed against live Reddit DOM (2026-11-10)
#
# shreddit-post attributes (always present, no shadow DOM needed):
#   id              → "t3_<post_id>"
#   post-title      → title string
#   permalink       → "/r/sub/comments/…"
#   author          → username
#   created-timestamp → ISO 8601 string
#   comment-count   → integer string
#
# shreddit-post light-DOM child:
#   shreddit-post-text-body[slot="text-body"] → selftext innerText
#
# shreddit-comment attributes (no shadow DOM):
#   thingid  → "t1_<comment_id>"
#   author   → username
#   created  → ISO 8601 string
#
# shreddit-comment light-DOM child:
#   details  → first child, innerText includes author/timestamp + body
# ---------------------------------------------------------------------------

_JS_POSTS = """
() => Array.from(document.querySelectorAll('shreddit-post')).map(el => ({
    id:           (el.getAttribute('id') || '').replace(/^t3_/, ''),
    title:         el.getAttribute('post-title')        || '',
    permalink:     el.getAttribute('permalink')         || '',
    author:        el.getAttribute('author')            || '',
    created:       el.getAttribute('created-timestamp') || '',
    commentCount:  parseInt(el.getAttribute('comment-count') || '0', 10),
    body:         (el.querySelector('shreddit-post-text-body') || {innerText: ''})
                  .innerText.trim()
})).filter(p => p.id && p.title)
"""

_JS_COMMENTS = """
() => Array.from(document.querySelectorAll('shreddit-comment')).map(el => ({
    id:      (el.getAttribute('thingid') || '').replace(/^t1_/, ''),
    author:   el.getAttribute('author')  || '',
    created:  el.getAttribute('created') || '',
    body:    (el.querySelector('details') || {innerText: ''})
             .innerText.trim().slice(0, 2000)
})).filter(c => c.id && c.body.length > 10)
"""

_JS_CLICK_MORE = """
() => {
    let n = 0;
    document.querySelectorAll('button').forEach(btn => {
        const t = (btn.textContent || '').trim().toLowerCase();
        if (/more (repl|comment)/.test(t) || /load more/.test(t) || /plus de r/.test(t)) {
            try { btn.click(); n++; } catch (e) {}
        }
    });
    return n;
}
"""

_JS_POST_BODY = """
() => {
    const el = document.querySelector('shreddit-post shreddit-post-text-body');
    return el ? el.innerText.trim() : '';
}
"""


# ---------------------------------------------------------------------------
# Browser setup
# ---------------------------------------------------------------------------

async def _block(route: Route) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def _build_context(pw) -> tuple[Browser, BrowserContext]:
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu",
              "--disable-extensions", "--mute-audio"],
    )
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    kw: dict[str, Any] = dict(viewport={"width": 1280, "height": 900}, user_agent=ua)
    if STATE_FILE.exists():
        log.info("Loading session from %s", STATE_FILE)
        kw["storage_state"] = str(STATE_FILE)
    else:
        log.info("No %s — running unauthenticated", STATE_FILE)

    ctx = await browser.new_context(**kw)
    await ctx.route("**/*", _block)
    return browser, ctx


# ---------------------------------------------------------------------------
# Pre-flight login-wall detection
# ---------------------------------------------------------------------------

async def _is_login_wall(page: Page) -> bool:
    url = page.url.lower()
    if any(s in url for s in ("login", "signin", "checkpoint")):
        log.error("LOGIN WALL — URL: %s  |  Run session_manager.py.", page.url)
        return True
    try:
        snippet = (await page.inner_text("body"))[:800].lower()
        if any(s in snippet for s in _LOGIN_WALL_SIGNALS):
            log.error("LOGIN WALL via page text at %s — run session_manager.py.", page.url)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Shared record writer
# ---------------------------------------------------------------------------

def _write(text: str, platform: str, source_id: str,
           url: str, author: str | None, ts: str | None) -> bool:
    if not text or len(text.strip()) < 5:
        return False
    fields = extract_fields(text)
    return insert_record({
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
        "raw_text":      text[:2000],
    })


# ---------------------------------------------------------------------------
# Comment page fetcher
# ---------------------------------------------------------------------------

async def _fetch_comments(page: Page, post_id: str, post_url: str) -> int:
    try:
        await page.goto(post_url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(2.5)

        full_body: str = await page.evaluate(_JS_POST_BODY)
        if full_body:
            _write(full_body, "reddit", f"{post_id}_body", post_url, None, None)

        seen_ids: set[str] = set()
        saved = 0
        stale = 0

        for _ in range(_MAX_COMMENT_SCROLLS):
            # Click "more replies / load more comments" buttons before extracting
            await page.evaluate(_JS_CLICK_MORE)
            await asyncio.sleep(0.8)

            comments: list[dict] = await page.evaluate(_JS_COMMENTS)
            new_this_scroll = 0
            for c in comments:
                if c["id"] in seen_ids:
                    continue
                seen_ids.add(c["id"])
                new_this_scroll += 1
                if _write(c["body"], "reddit", c["id"], post_url,
                          c["author"], c["created"] or None):
                    saved += 1
                    f = extract_fields(c["body"])
                    log.info("      + comment %-12s  r=%-6s  grade=%s",
                             c["id"], f["r_score"], f["grade"])

            if new_this_scroll == 0:
                stale += 1
                if stale >= 2:
                    break
            else:
                stale = 0

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(1.5, 2.5))

        log.info("    %s — %d comments seen, %d new saved", post_id, len(seen_ids), saved)
        return saved
    except Exception as exc:
        log.warning("    Comment fetch failed for %s: %s", post_url, exc)
        return 0


# ---------------------------------------------------------------------------
# Reddit HTML mode — subreddit listing scraper
# ---------------------------------------------------------------------------

async def _scrape_subreddit(ctx: BrowserContext, target: dict) -> int:
    subreddit   = target["subreddit"]
    scroll_pages = target.get("scroll_pages", 5)
    listing_url  = f"https://www.reddit.com/r/{subreddit}/?sort=new"

    load_wait = target.get("load_wait", 3)
    log.info("── r/%-20s  scroll_pages=%d  load_wait=%ds ──", subreddit, scroll_pages, load_wait)
    page = await ctx.new_page()

    try:
        await page.goto(listing_url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(load_wait)

        if await _is_login_wall(page):
            return 0

        # Wait for first posts to render; larger subs need a longer timeout
        try:
            await page.wait_for_selector("shreddit-post", timeout=20_000)
        except Exception:
            log.warning("  No shreddit-post elements on r/%s — page may be empty", subreddit)
            return 0

        seen_ids: set[str]   = set()
        posts_to_visit: list[dict] = []
        total_new = 0

        for scroll_i in range(scroll_pages + 1):
            posts: list[dict] = await page.evaluate(_JS_POSTS)
            new_this_batch = 0

            for post in posts:
                pid = post["id"]
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                new_this_batch += 1

                combined  = f"{post['title']}\n{post['body']}".strip()
                post_url  = f"https://reddit.com{post['permalink']}"
                fields    = extract_fields(combined)
                was_new   = _write(combined, "reddit", pid, post_url,
                                   post["author"], post["created"] or None)
                if was_new:
                    total_new += 1
                    log.info("  + post %-10s  r=%-6s  grade=%-6s  avg=%-6s  cegep=%s",
                             pid, fields["r_score"], fields["grade"],
                             fields["class_average"], fields["cegep"])

                # Queue for comment visit if regex found anything OR post has comments
                has_match = any(fields[k] is not None
                                for k in ("r_score", "grade", "class_average"))
                if has_match or post["commentCount"] >= 3:
                    posts_to_visit.append(post)

            # Scroll down and wait for new posts to load
            if scroll_i < scroll_pages:
                if new_this_batch == 0 and scroll_i > 0:
                    log.info("  Scroll %d — no new posts, stopping early", scroll_i)
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(2.0, 3.0))

        log.info("  r/%s listing: %d unique posts seen, %d new in DB",
                 subreddit, len(seen_ids), total_new)
    finally:
        await page.close()

    # --- Comment page pass ---
    visit_cap = min(len(posts_to_visit), _MAX_COMMENT_PAGES)
    if visit_cap == 0:
        return total_new

    log.info("  r/%s — visiting %d post pages for comments", subreddit, visit_cap)
    cpage = await ctx.new_page()
    try:
        for post in posts_to_visit[:visit_cap]:
            purl = f"https://reddit.com{post['permalink']}"
            total_new += await _fetch_comments(cpage, post["id"], purl)
            await asyncio.sleep(random.uniform(*_INTER_PAGE_DELAY))
    finally:
        await cpage.close()

    return total_new


async def _run_reddit_mode(ctx: BrowserContext) -> int:
    if not REDDIT_TARGETS:
        log.info("REDDIT_TARGETS empty — skipping")
        return 0
    total = 0
    for target in REDDIT_TARGETS:
        total += await _scrape_subreddit(ctx, target)
    return total


# ---------------------------------------------------------------------------
# DOM scraping mode (Facebook, Discord, etc.)
# ---------------------------------------------------------------------------

async def _extract_dom_texts(page: Page) -> list[str]:
    for selector in _DOM_SELECTORS:
        try:
            els = await page.query_selector_all(selector)
            texts = [(await el.inner_text()).strip() for el in els]
            texts = [t for t in texts if len(t) > 20]
            if texts:
                return texts
        except Exception:
            continue
    return []


async def _run_dom_page(ctx: BrowserContext, cfg: dict) -> int:
    url      = cfg["url"]
    platform = cfg["platform"]
    scrolls  = cfg.get("scroll_pages", 3)

    log.info("DOM mode: %s  (%s)", url, platform)
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

        if await _is_login_wall(page):
            return 0

        saved: int         = 0
        seen: set[str]     = set()

        for scroll_i in range(scrolls + 1):
            texts = await _extract_dom_texts(page)
            new_batch = 0
            for raw in texts:
                key = raw[:200]
                if key in seen:
                    continue
                seen.add(key)
                new_batch += 1
                ts = datetime.now(timezone.utc).isoformat()
                if _write(raw, platform, f"{url}:{key}", url, None, ts):
                    saved += 1
                    f = extract_fields(raw)
                    log.info("  + [%s] cegep=%-12s crc=%s", platform, f["cegep"], f["r_score"])

            if scroll_i < scrolls:
                if new_batch == 0:
                    log.info("  Scroll %d — no new elements, stopping", scroll_i + 1)
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

        log.info("  → %d new records from %s", saved, url)
        return saved
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if not REDDIT_TARGETS and not TARGET_PAGES:
        log.warning("Both REDDIT_TARGETS and TARGET_PAGES are empty — nothing to do.")
        return

    async with async_playwright() as pw:
        browser, ctx = await _build_context(pw)

        reddit_new = await _run_reddit_mode(ctx)

        dom_new = 0
        for cfg in TARGET_PAGES:
            dom_new += await _run_dom_page(ctx, cfg)

        await browser.close()

    from storage.db import row_count
    log.info("Done — Reddit: +%d  DOM: +%d  DB total: %d",
             reddit_new, dom_new, row_count())


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
