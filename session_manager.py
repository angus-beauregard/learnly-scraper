"""
One-time session capture utility.

Run this ONCE from a machine with a display (macOS dev environment).
It opens a visible Chromium window, waits for you to log in manually,
then saves the authenticated browser state to storage_state.json.

Subsequent headless runs in playwright_scraper.py load that file without
re-prompting for credentials.

Usage:
    python session_manager.py https://www.facebook.com
    python session_manager.py https://discord.com/channels/@me
"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

STATE_PATH = Path("storage_state.json")


async def capture(target_url: str) -> None:
    print(f"\nOpening browser → {target_url}")
    print("Log in, navigate to any groups/channels you want to scrape,")
    print("then press ENTER here to save the session.\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(target_url, wait_until="domcontentloaded")

        # Block here until the user manually logs in and presses Enter
        try:
            input(">>> Press ENTER when logged in and ready to save session: ")
        except EOFError:
            pass  # non-interactive environment — save immediately

        await context.storage_state(path=str(STATE_PATH))
        await browser.close()

    print(f"\nSession saved to {STATE_PATH.resolve()}")
    print("You can now run extractors/playwright_scraper.py headlessly.")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.facebook.com"
    asyncio.run(capture(url))
