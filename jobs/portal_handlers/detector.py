"""
jobs.portal_handlers.detector
==============================
Sniffs the apply URL (and optionally the live DOM) to determine which ATS
platform is hosting the application form, then returns the correct handler.

Detection order
---------------
1. URL pattern matching  — fast, no network round-trip required.
2. DOM meta-tag / title  — fallback for proxied / embedded forms.

Supported platforms
-------------------
- Greenhouse  boards.greenhouse.io / app.greenhouse.io
- Lever       jobs.lever.co
- Workday     myworkdayjobs.com / wd1.myworkday.com / wd3.myworkday.com

Unknown platforms raise ``PortalHandlerError`` with a descriptive message so
callers can surface a meaningful error to the user rather than a silent failure.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from jobs.portal_handlers.base import PortalHandlerError

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL-level pattern catalogue
# ---------------------------------------------------------------------------

_ATS_URL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Greenhouse
    (re.compile(r"boards\.greenhouse\.io", re.I), "greenhouse"),
    (re.compile(r"app\.greenhouse\.io", re.I), "greenhouse"),
    (re.compile(r"greenhouse\.io/embed", re.I), "greenhouse"),
    # Lever
    (re.compile(r"jobs\.lever\.co", re.I), "lever"),
    (re.compile(r"lever\.co/", re.I), "lever"),
    # Workday
    (re.compile(r"myworkdayjobs\.com", re.I), "workday"),
    (re.compile(r"wd\d+\.myworkday\.com", re.I), "workday"),
    (re.compile(r"workday\.com/.*apply", re.I), "workday"),
]


def _detect_from_url(url: str) -> str | None:
    """Return ATS platform slug if the URL matches a known pattern."""
    for pattern, platform in _ATS_URL_PATTERNS:
        if pattern.search(url):
            logger.debug("ATS detected from URL pattern: %s → %s", url, platform)
            return platform
    return None


async def _detect_from_dom(page: "Page") -> str | None:
    """
    Inspect the page DOM for ATS fingerprints.

    Checks (in order):
      1. <meta name="application-name"> content
      2. Page <title> text
      3. Well-known CSS class names injected by each ATS SDK
    """
    try:
        # Meta application-name tag
        meta = await page.get_attribute('meta[name="application-name"]', "content")
        if meta:
            meta_lower = meta.lower()
            if "greenhouse" in meta_lower:
                return "greenhouse"
            if "lever" in meta_lower:
                return "lever"

        # Page title
        title = await page.title()
        title_lower = title.lower()
        if "greenhouse" in title_lower:
            return "greenhouse"
        if "lever" in title_lower:
            return "lever"

        # DOM class fingerprints
        gh_present = await page.query_selector(".application--greenhouse, #application--greenhouse, [data-gh-job-board]")
        if gh_present:
            return "greenhouse"

        lever_present = await page.query_selector(".postings-btn, .posting-apply")
        if lever_present:
            return "lever"

        # Workday — data-automation-id is injected by the Workday SDK
        wd_present = await page.query_selector(
            "[data-automation-id='wd-ApplicationStep'], "
            "[data-automation-id='currentStep'], "
            "#mainContent.wd-popup-content"
        )
        if wd_present:
            return "workday"

    except Exception as exc:  # noqa: BLE001
        logger.warning("DOM ATS detection failed: %s", exc)

    return None


async def detect_handler(url: str, page: "Page"):  # -> BasePortalHandler
    """
    Detect the ATS from *url* and optionally the live *page* DOM.

    Returns an instantiated handler object.

    Raises
    ------
    PortalHandlerError
        When the platform cannot be detected or is not yet supported.
    """
    # Late imports to avoid circular references
    from jobs.portal_handlers.greenhouse import GreenhouseHandler
    from jobs.portal_handlers.lever import LeverHandler
    from jobs.portal_handlers.workday import WorkdayHandler

    _HANDLERS = {
        "greenhouse": GreenhouseHandler,
        "lever": LeverHandler,
        "workday": WorkdayHandler,
    }

    platform = _detect_from_url(url)
    if not platform:
        logger.info("URL did not match any known ATS, trying DOM detection...")
        platform = await _detect_from_dom(page)

    if not platform:
        raise PortalHandlerError(
            f"Could not detect ATS platform for URL: {url!r}. "
            "Only Greenhouse and Lever are currently supported."
        )

    handler_class = _HANDLERS.get(platform)
    if handler_class is None:
        raise PortalHandlerError(
            f"ATS platform {platform!r} was detected but has no handler implementation yet."
        )

    logger.info("Using %s handler for %s", handler_class.__name__, url)
    return handler_class()
