"""
Abstract base class for job-board API provider services.

Every concrete provider must implement:
    fetch(query, location, page)  -> list[dict]
    normalize(raw_item)           -> dict

The dict returned by ``normalize`` must be compatible with the JobListing
model field names so that the aggregator can call ``JobListing(**normalized)``
directly.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseJobProvider(ABC):
    """Abstract base for job-board API provider services."""

    # Subclasses should declare their source label (must match JobSource choice).
    source_label: str = ""

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch(self, query: str, location: str, page: int = 1) -> list[dict]:
        """
        Call the external API and return a list of raw job dicts.

        Returns an empty list if the provider key is not configured or
        if the HTTP call fails — never raises so the beat schedule
        continues running other providers.
        """

    @abstractmethod
    def normalize(self, raw_item: dict) -> dict:
        """
        Transform a single raw API item into a dict whose keys match
        ``JobListing`` model fields.

        Required keys in the returned dict:
            title       str
            company     str
            location    str          (may be empty string)
            description str          (may be empty string)
            source      str          (e.g. 'adzuna')
            source_url  str
        Optional:
            employment_type str
            is_remote       bool
            salary_min      int | None
            salary_max      int | None
            posted_at       datetime | None
        """

    # ------------------------------------------------------------------
    # Helper: safe API call wrapper
    # ------------------------------------------------------------------

    def _is_configured(self) -> bool:
        """Return True only when all required API credentials are present."""
        return True  # Subclasses override and return False when keys are missing

    def safe_fetch(self, query: str, location: str, pages: int = 3) -> list[dict]:
        """
        Convenience wrapper that:
        1. Checks configuration — returns [] if keys are missing.
        2. Iterates ``pages`` pages of results.
        3. Catches any exception and logs it rather than crashing.
        """
        if not self._is_configured():
            logger.info(
                "[%s] Skipping fetch — API credentials not configured.",
                self.__class__.__name__,
            )
            return []

        results: list[dict] = []
        for page in range(1, pages + 1):
            try:
                page_results = self.fetch(query, location, page)
                results.extend(page_results)
                if not page_results:
                    break  # No more results from this provider
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "[%s] Error fetching page %d: %s",
                    self.__class__.__name__,
                    page,
                    exc,
                )
                break

        return results
