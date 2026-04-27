"""
jobs.services
=============
Service layer for external job-board API integrations.

Providers
---------
- AdzunaService   — https://developer.adzuna.com/
- JoobleService   — https://jooble.org/api/
- CareerjetService — https://www.careerjet.com/partners/api/

All providers inherit from ``BaseJobProvider`` and expose:
  * fetch(query, location, page) -> list[dict]   (raw API response items)
  * normalize(raw_item)          -> dict          (JobListing-compatible dict)

The ``JobAggregator`` orchestrates all providers and bulk-upserts results.
"""

from .adzuna import AdzunaService
from .jooble import JoobleService
from .careerjet import CareerjetService
from .aggregator import JobAggregator

__all__ = [
    "AdzunaService",
    "JoobleService",
    "CareerjetService",
    "JobAggregator",
]
