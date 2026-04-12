"""Headless browser automation for carriers that need JavaScript rendering.

Used by carriers like Amazon where order data is decrypted client-side,
making it impossible to scrape with plain HTTP requests.  Playwright
launches headless Chromium, loads the user's session cookies, navigates
to the target page, waits for JS to render, and returns the final HTML.
"""

import asyncio
import json
import logging

from dwmp.carriers.base import CarrierAuthError

logger = logging.getLogger(__name__)

# Serialise browser launches so a memory-constrained pod doesn't OOM.
_browser_lock = asyncio.Lock()


def _normalize_cookies(raw_cookies: list[dict]) -> list[dict]:
    """Normalize cookies from various export formats to Playwright format.

    Handles Playwright's own format as well as the Cookie-Editor browser
    extension format (``expirationDate`` instead of ``expires``, lowercase
    ``sameSite``, etc.).
    """
    normalized: list[dict] = []
    for c in raw_cookies:
        cookie: dict = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }

        if "expires" in c:
            cookie["expires"] = c["expires"]
        elif "expirationDate" in c:
            cookie["expires"] = c["expirationDate"]

        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        if "secure" in c:
            cookie["secure"] = c["secure"]
        if "sameSite" in c:
            val = str(c["sameSite"]).lower()
            cookie["sameSite"] = (
                val.capitalize() if val in ("strict", "lax", "none") else "Lax"
            )

        normalized.append(cookie)
    return normalized


async def capture_page_html(
    url: str,
    cookies_json: str,
    carrier_name: str,
    login_indicators: list[str] | None = None,
    wait_selector: str | None = None,
    wait_timeout_ms: int = 15_000,
) -> tuple[str, str]:
    """Launch headless Chromium, load cookies, navigate, capture rendered HTML.

    Returns ``(html, updated_cookies_json)``.

    Raises :class:`CarrierAuthError` when the session has expired (login
    redirect detected) or the browser cannot be started.
    """
    from playwright.async_api import async_playwright

    if login_indicators is None:
        login_indicators = ["signin", "login", "auth"]

    try:
        cookies = json.loads(cookies_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CarrierAuthError(
            carrier_name,
            f"Invalid cookies JSON. Export your cookies as a JSON array. ({exc})",
        )

    cookies = _normalize_cookies(cookies)

    async with _browser_lock:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                    locale="nl-NL",
                    timezone_id="Europe/Amsterdam",
                )
                await context.add_cookies(cookies)

                page = await context.new_page()
                await page.goto(
                    url, wait_until="networkidle", timeout=wait_timeout_ms
                )

                # Detect login redirect
                current_url = page.url.lower()
                if any(ind in current_url for ind in login_indicators):
                    raise CarrierAuthError(
                        carrier_name,
                        "Session expired — redirected to login page "
                        f"({page.url}). Export fresh cookies from your browser.",
                    )

                if wait_selector:
                    try:
                        await page.wait_for_selector(
                            wait_selector, timeout=wait_timeout_ms
                        )
                    except Exception:
                        logger.warning(
                            "Selector %r not found within %dms — "
                            "capturing page as-is",
                            wait_selector,
                            wait_timeout_ms,
                        )

                html = await page.content()
                updated_cookies = await context.cookies()
                updated_json = json.dumps(updated_cookies)

                logger.info(
                    "Browser captured %d bytes of HTML from %s",
                    len(html),
                    url,
                )
            finally:
                await browser.close()

    return html, updated_json
