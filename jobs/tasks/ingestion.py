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
    ("software engineer", ""),
    ("data scientist", ""),
    ("product manager", ""),
    ("backend developer", ""),
    ("frontend developer", ""),
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
# Dynamic and Cleanup tasks
# ---------------------------------------------------------------------------

@shared_task(name="jobs.fetch_dynamic_jobs")
def fetch_dynamic_jobs(query: str, location: str = "") -> dict:
    """Fetch jobs dynamically for a specific query, with a 1-hour cooldown per query."""
    from django.core.cache import cache
    from jobs.services.adzuna import AdzunaService
    from jobs.services.jooble import JoobleService
    from jobs.services.careerjet import CareerjetService
    from jobs.services.aggregator import JobAggregator
    
    cache_key = f"fetch_dynamic_lock:{query.lower()}:{location.lower()}"
    if not cache.add(cache_key, "locked", timeout=3600):
        logger.info("Skipping fetch_dynamic_jobs for %r (recently fetched)", query)
        return {"status": "skipped", "reason": "rate_limited"}
    
    aggregator = JobAggregator(providers=[AdzunaService(), JoobleService(), CareerjetService()])
    totals = {"saved": 0, "skipped": 0, "errors": 0}
    
    try:
        stats = aggregator.run(query=query, location=location)
        for key in totals:
            totals[key] += stats[key]
    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_dynamic_jobs error for %r: %s", query, exc)
        
    logger.info("fetch_dynamic_jobs complete for %r: %s", query, totals)
    return totals

@shared_task(name="jobs.cleanup_old_jobs")
def cleanup_old_jobs() -> dict:
    """Delete JobListing records older than 20 days."""
    from django.utils import timezone
    from datetime import timedelta
    from jobs.models import JobListing
    
    threshold = timezone.now() - timedelta(days=20)
    deleted_count, _ = JobListing.objects.filter(created_at__lt=threshold).delete()
    
    logger.info("cleanup_old_jobs: deleted %d jobs older than 20 days", deleted_count)
    return {"deleted": deleted_count}

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
def parse_resume_task(self, user_id: int, file_base64: str, content_type: str, original_filename: str = "resume") -> dict:
    import base64
    from django.core.files.base import ContentFile
    from django.contrib.auth import get_user_model
    from django.conf import settings
    from jobs.parsers import ResumeParser
    from jobs.views import ResumeParseView
    from jobs.models import Document, DocumentType

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

    # ── Grab the raw text so the AI generator can use it ─────────────────────
    resume_text: str = extracted.get("text", "") or ""

    # ── Save as Master Resume Document ──────────────────────────────────────
    # Demote any previous master resume for this user first.
    Document.objects.filter(user=user, is_master=True).update(is_master=False)

    storage_key = ""
    file_url = ""

    # ── Try to upload to Supabase Storage ────────────────────────────────────
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    supabase_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

    if supabase_url and supabase_key:
        try:
            from jobs.services.supabase_storage import upload_resume as _upload
            storage_key, file_url = _upload(
                user_id=user.pk,
                file_bytes=file_bytes,
                filename=original_filename,
                content_type=content_type,
            )
            logger.info(
                "parse_resume_task: uploaded resume for user %s → %s",
                user.pk, storage_key,
            )
        except Exception as exc:
            logger.warning(
                "parse_resume_task: Supabase upload failed (falling back to local): %s", exc
            )

    # Build the Document record, using Supabase URL when available, falling
    # back to Django's local FileField otherwise.
    doc = Document(
        user=user,
        file_name=original_filename,
        file_url=file_url,
        storage_key=storage_key,
        doc_type=DocumentType.RESUME,
        is_master=True,
        is_ai_generated=False,
        resume_text=resume_text,
    )

    if not file_url:
        # Supabase not configured / upload failed — keep a local copy.
        doc.file.save(original_filename, ContentFile(file_bytes), save=False)

    doc.save()

    # ── Merge extracted data into the user's Profile ─────────────────────────
    profile_updated = ResumeParseView._update_profile(user, extracted)

    return {
        "extracted": extracted,
        "profile_updated": profile_updated,
        "master_resume_id": doc.pk,
    }

@shared_task(bind=True)
def generate_application_content_task(self, user_id: int, job_listing_id: int, application_id=None) -> dict:
    from django.contrib.auth import get_user_model
    from jobs.models import JobListing, Application, Document, DocumentType
    from jobs.services.ai_generator import generate_application_content

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        job_listing = JobListing.objects.get(pk=job_listing_id)
        profile = user.profile
    except Exception as exc:
        return {"error": str(exc)}

    # ── Fetch the user's master resume text ───────────────────────────────────
    # We prefer the stored Supabase-uploaded resume text. If the Document has
    # resume_text populated (set during parse_resume_task), use it directly.
    # Fallback: try to re-download from Supabase and extract text on the fly.
    resume_text = ""
    try:
        master_doc = (
            Document.objects
            .filter(user=user, is_master=True, doc_type=DocumentType.RESUME)
            .order_by("-created_at")
            .first()
        )
        if master_doc:
            if master_doc.resume_text:
                # Best path: text was saved at parse time
                resume_text = master_doc.resume_text
            elif master_doc.storage_key:
                # Fallback: re-download from Supabase and extract
                from jobs.services.supabase_storage import download_file
                from jobs.parsers import ResumeParser
                file_bytes = download_file(master_doc.storage_key)
                if file_bytes:
                    parser = ResumeParser()
                    # Guess content type from filename
                    ct = "application/pdf"
                    fn = (master_doc.file_name or "").lower()
                    if fn.endswith(".docx"):
                        ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif fn.endswith(".txt"):
                        ct = "text/plain"
                    extracted = parser.parse(file_bytes, ct)
                    resume_text = extracted.get("text", "") or ""
                    # Back-fill the resume_text field so we avoid re-download next time
                    if resume_text:
                        master_doc.resume_text = resume_text
                        master_doc.save(update_fields=["resume_text"])
            elif master_doc.file:
                # Local file fallback (legacy / dev without Supabase)
                from jobs.parsers import ResumeParser
                try:
                    file_bytes = master_doc.file.read()
                    parser = ResumeParser()
                    ct = "application/pdf"
                    fn = (master_doc.file_name or "").lower()
                    if fn.endswith(".docx"):
                        ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif fn.endswith(".txt"):
                        ct = "text/plain"
                    extracted = parser.parse(file_bytes, ct)
                    resume_text = extracted.get("text", "") or ""
                except Exception as re_exc:
                    logger.warning("Could not extract text from local resume file: %s", re_exc)
    except Exception as exc:
        logger.warning("generate_application_content_task: could not fetch resume text: %s", exc)

    generated_content = generate_application_content(profile, job_listing, resume_text=resume_text)

    # Persist into Application.ai_content so the frontend can reload it.
    if application_id:
        try:
            application = Application.objects.get(pk=application_id, user=user)
            application.ai_content = generated_content
            application.save(update_fields=["ai_content", "updated_at"])
        except Application.DoesNotExist:
            pass

    return generated_content
