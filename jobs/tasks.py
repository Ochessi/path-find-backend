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


# ---------------------------------------------------------------------------
# Semantic Matching — Embedding tasks
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="jobs.compute_job_embedding",
)
def compute_job_embedding(self, job_listing_id: int) -> dict:
    """
    Compute and persist the Sentence-BERT embedding for a single JobListing.

    Triggered automatically via a post-save Django signal on JobListing so
    that newly ingested or updated listings are always backed by a fresh vector.

    Can also be called manually:
        from jobs.tasks import compute_job_embedding
        compute_job_embedding.delay(job_listing_id=42)
    """
    from jobs.models import JobListing, JobEmbedding
    from jobs.services.embedding_service import build_job_text, encode
    from django.conf import settings

    try:
        job = JobListing.objects.get(pk=job_listing_id)
    except JobListing.DoesNotExist:
        logger.warning("compute_job_embedding: JobListing #%s not found.", job_listing_id)
        return {"status": "not_found", "job_listing_id": job_listing_id}

    model_name: str = getattr(settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

    try:
        text = build_job_text(job)
        vector = encode(text).tolist()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding encoding failed for JobListing #%s: %s", job_listing_id, exc)
        raise self.retry(exc=exc)

    JobEmbedding.objects.update_or_create(
        job_listing=job,
        defaults={"vector": vector, "model_name": model_name},
    )

    logger.info("Embedding computed for JobListing #%s.", job_listing_id)
    return {"status": "ok", "job_listing_id": job_listing_id, "dim": len(vector)}


@shared_task(name="jobs.recompute_all_embeddings")
def recompute_all_embeddings() -> dict:
    """
    Back-fill embeddings for all JobListings that satisfy either condition:
      1. No embedding row exists yet (newly ingested listings).
      2. The stored embedding was produced by a different model than the one
         currently configured in ``settings.EMBEDDING_MODEL_NAME`` (model upgrade).

    Useful after:
      - First deployment of the semantic matching feature.
      - Upgrading the embedding model: change EMBEDDING_MODEL_NAME in settings,
        then this nightly task (or a manual .delay()) will re-queue all stale rows.

    Run manually:
        from jobs.tasks import recompute_all_embeddings
        recompute_all_embeddings.delay()
    """
    from django.conf import settings
    from jobs.models import JobListing, JobEmbedding

    model_name: str = getattr(settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

    # ── 1. Listings with no embedding row at all ───────────────────────────
    embedded_ids = JobEmbedding.objects.values_list("job_listing_id", flat=True)
    missing_ids = list(
        JobListing.objects.exclude(pk__in=embedded_ids).values_list("id", flat=True)
    )

    # ── 2. Listings whose embedding was built with a different model ───────
    stale_ids = list(
        JobEmbedding.objects
        .exclude(model_name=model_name)
        .values_list("job_listing_id", flat=True)
    )

    all_ids = list(set(missing_ids) | set(stale_ids))

    logger.info(
        "recompute_all_embeddings: %d missing, %d stale (model mismatch) → %d total to queue.",
        len(missing_ids), len(stale_ids), len(all_ids),
    )

    for jid in all_ids:
        compute_job_embedding.delay(jid)

    return {
        "status": "queued",
        "missing": len(missing_ids),
        "stale": len(stale_ids),
        "total": len(all_ids),
        "model_name": model_name,
    }

@shared_task(bind=True)
def parse_resume_task(self, user_id: int, file_base64: str, content_type: str) -> dict:
    import base64
    from django.contrib.auth import get_user_model
    from jobs.parsers import ResumeParser
    from jobs.views import ResumeParseView

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return {"error": "User not found."}

    file_bytes = base64.b64decode(file_base64)
    parser = ResumeParser()
    
    try:
        extracted = parser.parse(file_bytes, content_type)
    except Exception as exc:
        logger.exception("Resume parsing failed in task: %s", exc)
        return {"error": "Resume parsing failed. Please try again."}

    # Merge extracted data into the user's Profile
    profile_updated = ResumeParseView._update_profile(user, extracted)

    return {"extracted": extracted, "profile_updated": profile_updated}

@shared_task(bind=True)
def generate_application_content_task(self, user_id: int, job_listing_id: int) -> dict:
    from django.contrib.auth import get_user_model
    from jobs.models import JobListing
    from jobs.services.ai_generator import generate_application_content
    
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        job_listing = JobListing.objects.get(pk=job_listing_id)
        profile = user.profile
    except Exception as exc:
        return {"error": str(exc)}
        
    generated_content = generate_application_content(profile, job_listing)
    return generated_content
