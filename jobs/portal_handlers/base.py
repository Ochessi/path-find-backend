"""
jobs.portal_handlers.base
==========================
Abstract interface that every ATS handler must satisfy.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


class PortalHandlerError(Exception):
    """Raised when an ATS handler cannot complete the submission."""


class BasePortalHandler(ABC):
    """
    Abstract base class for ATS application-form handlers.

    Subclasses must implement:
      - ``fill_form(page, payload)``  — navigate & populate every field
      - ``submit(page)``              — click submit / confirm
      - ``get_confirmation(page)``    — scrape & return confirmation text

    The ``run(page, payload)`` orchestrator calls these three steps in order
    and injects human-like random delays between actions.
    """

    #: Minimum and maximum delay (seconds) injected between browser actions.
    DELAY_MIN: float = 0.5
    DELAY_MAX: float = 1.5

    # ── Public orchestrator ────────────────────────────────────────────────

    async def run(self, page: "Page", payload: dict) -> dict:
        """
        Full lifecycle: navigate → fill → submit → confirm.

        Parameters
        ----------
        page:
            An active Playwright ``Page`` already connected to the ATS URL.
        payload:
            Dict with keys: first_name, last_name, email, phone,
            cover_letter_text, linkedin_url, resume_path (local file path).

        Returns
        -------
        dict
            ``{"confirmation_text": str, "confirmation_url": str}``
        """
        logger.info("[%s] Starting form fill", self.__class__.__name__)
        await self.fill_form(page, payload)

        logger.info("[%s] Submitting form", self.__class__.__name__)
        await self.submit(page)

        logger.info("[%s] Fetching confirmation", self.__class__.__name__)
        return await self.get_confirmation(page)

    # ── Subclass contract ──────────────────────────────────────────────────

    @abstractmethod
    async def fill_form(self, page: "Page", payload: dict) -> None:
        """Navigate to the apply page and populate all visible fields."""

    @abstractmethod
    async def submit(self, page: "Page") -> None:
        """Click the final submit button and wait for navigation/response."""

    @abstractmethod
    async def get_confirmation(self, page: "Page") -> dict:
        """
        Scrape the post-submission page and return::

            {"confirmation_text": "...", "confirmation_url": "..."}
        """

    # ── Shared helpers ─────────────────────────────────────────────────────

    @staticmethod
    async def human_delay() -> None:
        """Sleep for a random interval to mimic human interaction speed."""
        delay = random.uniform(BasePortalHandler.DELAY_MIN, BasePortalHandler.DELAY_MAX)
        await asyncio.sleep(delay)

    @staticmethod
    async def safe_fill(page: "Page", selector: str, value: str, *, timeout: int = 8000) -> bool:
        """
        Fill a text field by CSS selector, with a pre-fill human delay.

        Returns ``True`` if the element was found and filled, ``False`` otherwise.
        Failures are logged as warnings rather than exceptions so the handler
        can continue with remaining fields.
        """
        await BasePortalHandler.human_delay()
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            await page.fill(selector, value)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("safe_fill: could not fill %r — %s", selector, exc)
            return False

    @staticmethod
    async def safe_click(page: "Page", selector: str, *, timeout: int = 8000) -> bool:
        """Click an element by CSS selector, with a pre-click human delay."""
        await BasePortalHandler.human_delay()
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            await page.click(selector)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("safe_click: could not click %r — %s", selector, exc)
            return False

    @staticmethod
    async def safe_upload(page: "Page", selector: str, file_path: str, *, timeout: int = 8000) -> bool:
        """Set a file upload input to the given local path."""
        await BasePortalHandler.human_delay()
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            await page.set_input_files(selector, file_path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("safe_upload: could not upload to %r — %s", selector, exc)
            return False
