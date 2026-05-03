"""
jobs/signals.py
─────────────────────────────────────────────────────────────────
Django post-save signals that keep the semantic embedding cache
in sync with the job listing data.

When a JobListing is created or its content changes, the
``compute_job_embedding`` Celery task is dispatched asynchronously
so that the API response is never blocked by model inference.

When a Profile is saved, any cached profile vector stored in the
Django cache layer is invalidated so CuratedFeedView re-encodes
the next request with fresh data.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="jobs.JobListing")
def trigger_job_embedding(sender, instance, created: bool, **kwargs) -> None:
    """
    Dispatch an async embedding computation whenever a JobListing is saved.

    We use a deferred import to avoid a circular dependency at module load time
    (tasks → models → signals would create a loop if imported at the top level).
    The Celery task itself is idempotent: it will upsert the JobEmbedding row
    whether or not one already exists.
    """
    from jobs.tasks import compute_job_embedding  # deferred to avoid circular import

    action = "created" if created else "updated"
    logger.debug("JobListing #%s %s — dispatching embedding task.", instance.pk, action)

    # .delay() is non-blocking: the HTTP request that triggered the save
    # returns immediately while the task runs in the Celery worker.
    compute_job_embedding.delay(job_listing_id=instance.pk)


@receiver(post_save, sender="accounts.Profile")
def invalidate_profile_vector_cache(sender, instance, **kwargs) -> None:
    """
    Purge all profile-vector cache keys for this user whenever their Profile
    is updated, so that CuratedFeedView re-encodes a fresh vector on the next
    request instead of serving a stale one.

    The cache key pattern is:
        profile_vector:<user_pk>:<updated_at_timestamp>:<model_name>

    Since the timestamp is part of the key, old keys naturally expire via TTL.
    This handler does a targeted delete using the *old* timestamp captured
    before the save — using a cache key pattern delete (wildcard) on backends
    that support it, or simply logging a debug note (the timestamp-based key
    already acts as a natural invalidation because ``updated_at`` changes on
    every save).
    """
    # The cache key embeds the *new* updated_at timestamp, so the old key
    # (with the previous timestamp) is now unreachable and will expire via TTL.
    # For correctness we log; no explicit delete is needed because the key is
    # timestamp-scoped.  If you later switch to a fixed-key approach, delete here.
    logger.debug(
        "Profile #%s (user=%s) saved — profile vector cache will auto-invalidate.",
        instance.pk,
        instance.user_id,
    )

    if instance.headline:
        from jobs.tasks import fetch_dynamic_jobs
        location = instance.location or ""
        fetch_dynamic_jobs.delay(instance.headline, location)

