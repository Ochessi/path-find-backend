"""
JobAggregator — orchestrates all three providers and upserts into the DB.

Usage (from a Celery task or management command):

    from jobs.services import JobAggregator
    stats = JobAggregator().run(query="software engineer", location="london")
    print(stats)  # {"saved": 47, "skipped": 3, "errors": 0}
"""

from __future__ import annotations

import logging
from typing import TypedDict

from jobs.models import JobListing
from .adzuna import AdzunaService
from .jooble import JoobleService
from .careerjet import CareerjetService
from .base import BaseJobProvider

logger = logging.getLogger(__name__)

# Number of result pages to request from each provider per run.
# Adzuna: 50 results/page → 3 pages = up to 150 jobs
# Jooble/Careerjet: 20 results/page → 3 pages = up to 60 jobs
_PAGES_PER_PROVIDER = 3


class AggregatorStats(TypedDict):
    saved: int
    skipped: int
    errors: int


class JobAggregator:
    """
    Calls all configured providers, normalises their results, then
    bulk-upserts into the ``JobListing`` table.

    Deduplication key: ``(source, source_url)``
        - If a listing with the same key already exists, it is updated.
        - If the source_url is blank, the listing is still saved but
          uniqueness is not enforced (rare: Careerjet sometimes omits URLs).
    """

    def __init__(self, providers: list[BaseJobProvider] | None = None) -> None:
        self._providers: list[BaseJobProvider] = providers or [
            AdzunaService(),
            JoobleService(),
            CareerjetService(),
        ]

    def run(self, query: str, location: str) -> AggregatorStats:
        """
        Execute a full fetch-normalise-upsert cycle.

        Returns a stats dict with ``saved``, ``skipped``, and ``errors``
        counts for observability / logging.
        """
        stats: AggregatorStats = {"saved": 0, "skipped": 0, "errors": 0}

        for provider in self._providers:
            provider_name = provider.__class__.__name__
            logger.info("[%s] Starting fetch: query=%r location=%r", provider_name, query, location)

            raw_items = provider.safe_fetch(query, location, pages=_PAGES_PER_PROVIDER)
            logger.info("[%s] Fetched %d raw items.", provider_name, len(raw_items))

            for raw_item in raw_items:
                try:
                    normalized = provider.normalize(raw_item)
                    self._upsert(normalized, stats)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "[%s] Error normalising item: %s — %r",
                        provider_name,
                        exc,
                        raw_item,
                    )
                    stats["errors"] += 1

        logger.info(
            "Aggregation complete: saved=%d skipped=%d errors=%d",
            stats["saved"],
            stats["skipped"],
            stats["errors"],
        )
        return stats

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert(normalized: dict, stats: AggregatorStats) -> None:
        """
        Insert a new JobListing or update an existing one.

        Lookup key: (source, source_url).
        If source_url is blank we always insert (can't deduplicate without URL).
        """
        source_url = normalized.get("source_url", "").strip()
        source = normalized.get("source", "")

        if source_url:
            obj, created = JobListing.objects.update_or_create(
                source=source,
                source_url=source_url,
                defaults=normalized,
            )
            if created:
                stats["saved"] += 1
            else:
                stats["skipped"] += 1
        else:
            # No URL — insert blindly (rare edge case)
            JobListing.objects.create(**normalized)
            stats["saved"] += 1
