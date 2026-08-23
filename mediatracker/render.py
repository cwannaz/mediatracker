"""Last-resort headless rendering.

Used only by adapters for journals whose article body or comment thread exists
only after client-side JavaScript runs (an SPA, a lazy-loaded comment widget).
Everything obtainable with a plain GET must go through fetch.py instead — this
module is heavy (a real browser) and is imported lazily so the daemon has no hard
dependency on Playwright being installed.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


async def render_html(url: str, *, wait_selector: str | None = None,
                      timeout_ms: int = 30000, user_agent: str | None = None) -> str:
    """Load `url` in headless Chromium and return the fully rendered HTML.

    Raises RuntimeError if Playwright is not installed. `wait_selector`, when
    given, waits for that element (e.g. the comment container) before capturing.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is required for JS rendering: pip install playwright && playwright install chromium"
        ) from exc

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=user_agent)
            page = await context.new_page()
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:
                    log.debug("wait_selector %r not found on %s", wait_selector, url)
            html = await page.content()
            return html
        finally:
            await browser.close()
