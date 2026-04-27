"""
Celery periodic tasks for job-board data ingestion.

Each task is a thin wrapper around the corresponding provider service.
Tasks are registered in CELERY_BEAT_SCHEDULE (settings.py) to run
every 8 hours.

Run manually from the Django shell:
    from jobs.tasks import fetch_all_jobs
    fetch_all_jobs.delay()

Or run a single provider:
    from jobs.tasks import fetch_adzuna_jobs
    fetch_adzuna_jobs.delay()
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

# Default search parameters — override via Django settings if needed.
_DEFAULT_QUERIES = [
    ("software engineer", "remote"),
    ("data scientist", "remote"),
    ("product manager", "remote"),
    ("backend developer", "remote"),
    ("frontend developer", "remote"),
]


# ---------------------------------------------------------------------------
# Individual provider tasks
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=300, name="jobs.fetch_adzuna_jobs")
def fetch_adzuna_jobs(self) -> dict:
    """Fetch jobs from Adzuna across default search queries."""
    from jobs.services.adzuna import AdzunaService
    from jobs.services.aggregator import JobAggregator

    aggregator = JobAggregator(providers=[AdzunaService()])
    totals = {"saved": 0, "skipped": 0, "errors": 0}

    for query, location in _DEFAULT_QUERIES:
        try:
            stats = aggregator.run(query=query, location=location)
            for key in totals:
                totals[key] += stats[key]
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetch_adzuna_jobs error for %r/%r: %s", query, location, exc)
            raise self.retry(exc=exc)

    logger.info("fetch_adzuna_jobs complete: %s", totals)
    return totals


@shared_task(bind=True, max_retries=3, default_retry_delay=300, name="jobs.fetch_jooble_jobs")
def fetch_jooble_jobs(self) -> dict:
    """Fetch jobs from Jooble across default search queries."""
    from jobs.services.jooble import JoobleService
    from jobs.services.aggregator import JobAggregator

    aggregator = JobAggregator(providers=[JoobleService()])
    totals = {"saved": 0, "skipped": 0, "errors": 0}

    for query, location in _DEFAULT_QUERIES:
        try:
            stats = aggregator.run(query=query, location=location)
            for key in totals:
                totals[key] += stats[key]
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetch_jooble_jobs error for %r/%r: %s", query, location, exc)
            raise self.retry(exc=exc)

    logger.info("fetch_jooble_jobs complete: %s", totals)
    return totals


@shared_task(bind=True, max_retries=3, default_retry_delay=300, name="jobs.fetch_careerjet_jobs")
def fetch_careerjet_jobs(self) -> dict:
    """Fetch jobs from Careerjet across default search queries."""
    from jobs.services.careerjet import CareerjetService
    from jobs.services.aggregator import JobAggregator

    aggregator = JobAggregator(providers=[CareerjetService()])
    totals = {"saved": 0, "skipped": 0, "errors": 0}

    for query, location in _DEFAULT_QUERIES:
        try:
            stats = aggregator.run(query=query, location=location)
            for key in totals:
                totals[key] += stats[key]
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetch_careerjet_jobs error for %r/%r: %s", query, location, exc)
            raise self.retry(exc=exc)

    logger.info("fetch_careerjet_jobs complete: %s", totals)
    return totals


# ---------------------------------------------------------------------------
# Convenience task: run all providers in sequence
# ---------------------------------------------------------------------------

@shared_task(name="jobs.fetch_all_jobs")
def fetch_all_jobs() -> dict:
    """
    Run all three provider fetches in sequence.
    Useful for triggering a full refresh manually.
    """
    from jobs.services import JobAggregator

    aggregator = JobAggregator()
    totals = {"saved": 0, "skipped": 0, "errors": 0}

    for query, location in _DEFAULT_QUERIES:
        stats = aggregator.run(query=query, location=location)
        for key in totals:
            totals[key] += stats[key]

    logger.info("fetch_all_jobs complete: %s", totals)
    return totals
