"""
jobs.tasks package
==================
Re-exports all shared tasks so that the Celery task registry continues to
find them under their original dotted names (e.g. ``jobs.fetch_adzuna_jobs``).

Sub-modules
-----------
- ingestion          : periodic job-board fetching & embedding tasks
- portal_submission  : browser-automation task that auto-fills & submits
                       job application forms via Browserbase + Playwright
"""

# ── Ingestion & embedding tasks (unchanged public API) ─────────────────────
from jobs.tasks.ingestion import (  # noqa: F401
    fetch_adzuna_jobs,
    fetch_jooble_jobs,
    fetch_careerjet_jobs,
    fetch_all_jobs,
    fetch_dynamic_jobs,
    cleanup_old_jobs,
    compute_job_embedding,
    recompute_all_embeddings,
    parse_resume_task,
    generate_application_content_task,
)

# ── Portal submission task ──────────────────────────────────────────────────
from jobs.tasks.portal_submission import submit_to_portal_task  # noqa: F401

__all__ = [
    "fetch_adzuna_jobs",
    "fetch_jooble_jobs",
    "fetch_careerjet_jobs",
    "fetch_all_jobs",
    "fetch_dynamic_jobs",
    "cleanup_old_jobs",
    "compute_job_embedding",
    "recompute_all_embeddings",
    "parse_resume_task",
    "generate_application_content_task",
    "submit_to_portal_task",
]
