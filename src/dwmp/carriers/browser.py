"""Headless browser automation for carriers that need JavaScript rendering.

Used by carriers like Amazon where order data is decrypted client-side,
making it impossible to scrape with plain HTTP requests.  Playwright
launches headless Chromium, loads the user's session cookies, navigates
to the target page, waits for JS to render, and returns the final HTML.
"""

import asyncio
import json
import logging
import os
import re

from dwmp.carriers.base import CarrierAuthError

logger = logging.getLogger(__name__)

# Serialise browser launches so a memory-constrained pod doesn't OOM.
_browser_lock = asyncio.Lock()

# Default UA matching the Linux container. Overridable per-call so browser
# carriers can replay with the exact UA their cookies were issued to.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Launch real Chrome (installed via `playwright install chrome`) by default so
# the TLS ClientHello fingerprint matches Chrome-stable — what Cloudflare's
# cf_clearance is actually bound to. Set PLAYWRIGHT_BROWSER_CHANNEL="" at runtime
# to force bundled Chromium (e.g. in environments where Chrome isn't installed).
_BROWSER_CHANNEL = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "chrome") or None


def _platform_from_ua(user_agent: str) -> str:
    """Pick a navigator.platform override that's consistent with the UA string."""
    lower = user_agent.lower()
    if "windows" in lower:
        return "Win32"
    if "mac os x" in lower or "macintosh" in lower:
        return "MacIntel"
    return "Linux x86_64"


def _stealth(user_agent: str):
    """Return a configured Stealth instance for use with ``use_async``.

    Aligns the navigator platform with the effective user-agent so the JS
    fingerprint stays consistent. Cloudflare's bot check flags mismatches
    between the UA string and navigator.platform.
    """
    from playwright_stealth import Stealth

    return Stealth(
        navigator_platform_override=_platform_from_ua(user_agent),
        navigator_user_agent_override=user_agent,
    )


def _locale_from_ua(user_agent: str) -> tuple[str, str]:
    """Extract (locale, timezone) hints from UA; fall back to Dutch locale."""
    # Most browsers don't put locale in UA strings, so this is mostly a hook
    # for future accept-language inference. For now: sensible NL default.
    match = re.search(r"\b([a-z]{2}-[A-Z]{2})\b", user_agent)
    locale = match.group(1) if match else "nl-NL"
    return locale, "Europe/Amsterdam"


async def _launch_browser(pw):
    """Launch Chromium using the configured channel, falling back gracefully."""
    try:
        return await pw.chromium.launch(headless=True, channel=_BROWSER_CHANNEL)
    except Exception as exc:
        if _BROWSER_CHANNEL:
            logger.warning(
                "Failed to launch channel=%r (%s) — falling back to bundled Chromium",
                _BROWSER_CHANNEL, exc,
            )
            return await pw.chromium.launch(headless=True)
        raise


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
    user_agent: str | None = None,
) -> tuple[str, str]:
    """Launch headless Chromium, load cookies, navigate, capture rendered HTML.

    Returns ``(html, updated_cookies_json)``.

    ``user_agent`` should match the browser that issued the cookies — Cloudflare
    binds ``cf_clearance`` to (IP, UA, TLS fingerprint), so a mismatch
    invalidates the session. When omitted, the default Linux Chrome UA is used.

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
    effective_ua = user_agent or _USER_AGENT
    locale, tz = _locale_from_ua(effective_ua)

    async with _browser_lock:
        async with _stealth(effective_ua).use_async(async_playwright()) as pw:
            browser = await _launch_browser(pw)
            try:
                context = await browser.new_context(
                    user_agent=effective_ua,
                    viewport={"width": 1280, "height": 800},
                    locale=locale,
                    timezone_id=tz,
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
                        # Log enough context to diagnose selector misses
                        # without leaking account data — URL + title only,
                        # plus a sanitized snippet of the first body text.
                        try:
                            title = await page.title()
                        except Exception:
                            title = "<unavailable>"
                        try:
                            body_text = await page.evaluate(
                                "() => (document.body?.innerText || '').slice(0, 400)"
                            )
                        except Exception:
                            body_text = ""
                        logger.warning(
                            "Selector %r not found within %dms on %s "
                            "(title=%r, url=%s) — capturing page as-is. "
                            "First body text: %s",
                            wait_selector,
                            wait_timeout_ms,
                            carrier_name,
                            title,
                            page.url,
                            body_text.replace("\n", " ")[:400],
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
