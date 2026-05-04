"""
jobs.tasks.portal_submission
==============================
Celery task that uses Playwright connected to a Browserbase remote session to
automatically fill and submit a job-application form on behalf of the user.

Usage
-----
::

    from jobs.tasks import submit_to_portal_task
    result = submit_to_portal_task.delay(application_id=42)
    # result.get() → {"status": "applied", "confirmation_text": "...", ...}

Supported ATS platforms
-----------------------
- Greenhouse   (boards.greenhouse.io / app.greenhouse.io)
- Lever        (jobs.lever.co)
- Workday      (myworkdayjobs.com / wd1.myworkday.com)

More platforms can be added by implementing a new handler in
``jobs/portal_handlers/`` and registering it in ``detector.py``.

Dependencies
------------
- playwright         (async API, chromium)
- browserbase-sdk    (Browserbase session management)
- reportlab          (server-side resume PDF fallback)

Environment / settings
-----------------------
``BROWSERBASE_API_KEY``    — Browserbase API key (required)
``BROWSERBASE_PROJECT_ID`` — Browserbase project ID (required)

Failure handling
----------------
When browser automation raises an unrecoverable exception the task:
  1. Captures a full-page screenshot via Playwright.
  2. Saves the screenshot bytes to ``Application.notes`` as a base-64 data-URL
     (or uploads to storage if configured).
  3. Appends a "please apply manually" instruction to ``Application.notes``.
  4. Marks ``Application.status = NEEDS_MANUAL_APPLY`` (falls back to SAVED if
     the status choice doesn't exist yet).
  5. Re-raises so Celery can retry up to ``max_retries`` times before giving up.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from typing import Any

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — resume bytes
# ---------------------------------------------------------------------------

def _get_resume_bytes(app) -> bytes | None:
    """
    Return the raw bytes of the resume file attached to *app*.

    Handles both S3-backed (django-storages) and local-file storage backends.
    Returns ``None`` if no resume is attached.
    """
    if not app.resume:
        return None

    resume_doc = app.resume
    storage_field = getattr(resume_doc, "file", None)

    if storage_field:
        # Django FieldFile — works for both local and S3 storage
        with storage_field.open("rb") as fh:
            return fh.read()

    # Fallback: try to read via URL stored in file_url
    if resume_doc.file_url:
        import urllib.request
        try:
            with urllib.request.urlopen(resume_doc.file_url) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not download resume from file_url: %s", exc)

    return None


def _get_or_generate_resume_bytes(app, user, profile) -> bytes | None:
    """
    Return resume bytes for this application.

    Priority:
      1. Uploaded resume attached to the Application (preferred).
      2. AI-generated PDF created from the user's Profile data (fallback).

    If neither produces bytes, returns ``None`` and the submission will proceed
    without a resume file upload.
    """
    # Try the attached uploaded file first
    resume_bytes = _get_resume_bytes(app)
    if resume_bytes:
        logger.info("Using uploaded resume for Application #%s", app.pk)
        return resume_bytes

    # Fallback: generate a PDF from the user's profile
    logger.info(
        "Application #%s has no uploaded resume — generating PDF from profile.",
        app.pk,
    )
    try:
        from jobs.services.resume_pdf import generate_resume_pdf
        pdf_bytes = generate_resume_pdf(user, profile)
        if pdf_bytes:
            logger.info(
                "Generated resume PDF (%d bytes) for Application #%s",
                len(pdf_bytes), app.pk,
            )
            return pdf_bytes
    except Exception as exc:  # noqa: BLE001
        logger.warning("Resume PDF generation failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Helpers — cover letter text
# ---------------------------------------------------------------------------

def _get_cover_letter_text(app) -> str:
    """
    Return the text content of the cover letter attached to *app*.

    Tries:
      1. ``app.cover_letter`` Document's ``file_url`` — plain-text / PDF
      2. Generated text stored directly in Application.notes as a sentinel pattern
    Falls back to an empty string.
    """
    if not app.cover_letter:
        return ""

    cover_doc = app.cover_letter
    storage_field = getattr(cover_doc, "file", None)

    if storage_field:
        try:
            with storage_field.open("rb") as fh:
                raw = fh.read()
            # If it's plain text, decode directly; if PDF, use pypdf.
            if cover_doc.file_name.lower().endswith(".pdf"):
                from pypdf import PdfReader
                from io import BytesIO
                reader = PdfReader(BytesIO(raw))
                return "\n".join(p.extract_text() or "" for p in reader.pages).strip()
            return raw.decode("utf-8", errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read cover letter file: %s", exc)

    return ""


# ---------------------------------------------------------------------------
# Helpers — payload
# ---------------------------------------------------------------------------

def _build_payload(app, profile, user) -> dict:
    """
    Assemble the form-fill payload from Application + Profile + User.

    Returns
    -------
    dict with keys:
        first_name, last_name, email, phone, linkedin_url,
        cover_letter_text, resume_path (temp file path or "")
    """
    full_name = (user.full_name or "").strip()
    parts     = full_name.split(" ", 1)
    first     = parts[0] if parts else ""
    last      = parts[1] if len(parts) > 1 else ""

    return {
        "first_name":        first,
        "last_name":         last,
        "email":             user.email,
        "phone":             getattr(profile, "phone", "") or "",
        "linkedin_url":      getattr(profile, "linkedin_url", "") or "",
        "cover_letter_text": _get_cover_letter_text(app),
        # resume_path is set later after writing bytes to a temp file
        "resume_path":       "",
    }


# ---------------------------------------------------------------------------
# Helpers — screenshot capture
# ---------------------------------------------------------------------------

async def _capture_screenshot(page) -> bytes | None:
    """
    Capture a full-page PNG screenshot from the live Playwright page.

    Returns raw PNG bytes, or ``None`` on failure.
    """
    try:
        png_bytes = await page.screenshot(full_page=True, type="png")
        logger.info("Screenshot captured (%d bytes)", len(png_bytes))
        return png_bytes
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to capture failure screenshot: %s", exc)
        return None


def _save_screenshot_to_application(application_id: int, png_bytes: bytes) -> str:
    """
    Persist the failure screenshot and return a human-readable reference string.

    Strategy (in priority order):
      1. Upload to S3 (if ``django-storages`` + boto3 are configured) and store
         the URL in a note on the Application.
      2. Fall back to embedding the PNG as a base-64 data-URL in the notes —
         useful for debugging even without S3.

    Returns a string to append to Application.notes.
    """
    # Try S3 upload via django-storages
    try:
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        screenshot_key = f"portal_screenshots/{application_id}/failure_{timezone.now():%Y%m%d_%H%M%S}.png"
        saved_name = default_storage.save(screenshot_key, ContentFile(png_bytes))
        try:
            screenshot_url = default_storage.url(saved_name)
        except NotImplementedError:
            screenshot_url = saved_name  # local storage — just the path

        logger.info(
            "Failure screenshot uploaded for Application #%s: %s",
            application_id, screenshot_url,
        )
        return f"\n[Failure screenshot]: {screenshot_url}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not upload failure screenshot to storage: %s", exc)

    # Fallback: base64 data-URL (kept short in notes for readability)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64[:120]}…"  # truncated for notes
    return f"\n[Failure screenshot (truncated)]: {data_url}"


# ---------------------------------------------------------------------------
# Async submission logic (runs inside asyncio.run)
# ---------------------------------------------------------------------------

async def _run_submission(apply_url: str, payload: dict, api_key: str, project_id: str) -> dict:
    """
    Core async routine:
      1. Creates a Browserbase session
      2. Connects Playwright to that session via CDP
      3. Detects the ATS and dispatches to the correct handler
      4. On failure: captures a full-page screenshot and re-raises
      5. Returns the confirmation dict (with optional screenshot_png key on failure)
    """
    from browserbase import Browserbase
    from playwright.async_api import async_playwright
    from jobs.portal_handlers import detect_handler

    bb = Browserbase(api_key=api_key)

    # Create an isolated browser session on Browserbase
    session = bb.sessions.create(project_id=project_id)
    session_id = session.id
    connect_url = session.connect_url  # CDP WebSocket endpoint

    logger.info("Browserbase session created: %s", session_id)

    screenshot_bytes: bytes | None = None

    async with async_playwright() as pw:
        # Connect to the remote Chromium instance via CDP
        browser = await pw.chromium.connect_over_cdp(connect_url)
        context = browser.contexts[0]
        page    = context.pages[0] if context.pages else await context.new_page()

        try:
            # Navigate to the application URL
            logger.info("Navigating to apply URL: %s", apply_url)
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)

            # Detect ATS and get the right handler
            handler = await detect_handler(apply_url, page)

            # Fill, submit, confirm
            result = await handler.run(page, payload)

        except Exception as exc:
            # ── Failure path: capture screenshot before closing browser ────
            logger.error(
                "_run_submission: automation failed — capturing screenshot. Error: %s", exc
            )
            screenshot_bytes = await _capture_screenshot(page)
            raise  # re-raise so the caller can handle retry / notification

        finally:
            await browser.close()

        # Clean up the Browserbase session
        try:
            bb.sessions.update(session_id, status="REQUEST_RELEASE")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not release Browserbase session %s: %s", session_id, exc)

    result["browserbase_session_id"] = session_id
    return result


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    name="jobs.submit_to_portal",
    time_limit=300,      # hard kill after 5 minutes
    soft_time_limit=270, # SoftTimeLimitExceeded raised at 4m30s
)
def submit_to_portal_task(self, application_id: int) -> dict[str, Any]:
    """
    Auto-submit a job application via Browserbase + Playwright.

    Parameters
    ----------
    application_id : int
        Primary key of the ``Application`` record to process.

    Returns
    -------
    dict
        On success::

            {
                "status": "applied",
                "application_id": <int>,
                "confirmation_text": "...",
                "confirmation_url": "...",
                "browserbase_session_id": "...",
            }

        On permanent failure (all retries exhausted)::

            {
                "status": "manual_required",
                "application_id": <int>,
                "error": "...",
                "screenshot_ref": "...",   # storage URL or data-URL prefix
            }

    Side-effects
    ------------
    - Sets ``Application.status = "applied"`` on success.
    - Sets ``Application.applied_at = now()`` on success.
    - Appends the confirmation URL to ``Application.notes`` on success.
    - On permanent failure: appends manual-apply instructions + screenshot
      reference to ``Application.notes`` and resets status to ``"saved"``.
    """
    from django.conf import settings
    from jobs.models import Application, ApplicationStatus

    # ── 1. Load models ─────────────────────────────────────────────────────
    try:
        app = (
            Application.objects
            .select_related("user", "job_listing", "resume", "cover_letter")
            .get(pk=application_id)
        )
    except Application.DoesNotExist:
        logger.error("submit_to_portal_task: Application #%s not found.", application_id)
        return {"error": f"Application #{application_id} not found."}

    user    = app.user
    job     = app.job_listing
    profile = getattr(user, "profile", None)

    if profile is None:
        logger.error(
            "submit_to_portal_task: User %s has no Profile.", user.email
        )
        return {"error": "User profile not found."}

    apply_url = job.source_url
    if not apply_url:
        return {"error": f"JobListing #{job.pk} has no apply_url (source_url is blank)."}

    # ── 2. Browserbase credentials ─────────────────────────────────────────
    api_key    = getattr(settings, "BROWSERBASE_API_KEY", "") or os.getenv("BROWSERBASE_API_KEY", "")
    project_id = getattr(settings, "BROWSERBASE_PROJECT_ID", "") or os.getenv("BROWSERBASE_PROJECT_ID", "")

    if not api_key:
        return {"error": "BROWSERBASE_API_KEY is not configured in settings."}
    if not project_id:
        return {"error": "BROWSERBASE_PROJECT_ID is not configured in settings."}

    # ── 3. Build form payload ──────────────────────────────────────────────
    payload = _build_payload(app, profile, user)

    # ── 4. Resolve resume: uploaded file OR server-generated PDF ──────────
    #
    # _get_or_generate_resume_bytes() tries the attached Document first; if
    # absent it calls generate_resume_pdf() to build one from Profile data.
    resume_bytes = _get_or_generate_resume_bytes(app, user, profile)
    tmp_resume   = None

    if resume_bytes:
        # Determine file extension
        suffix = ".pdf"
        if app.resume and app.resume.file_name:
            _, ext = os.path.splitext(app.resume.file_name)
            if ext:
                suffix = ext
        # Playwright needs a local temp path for file-upload inputs
        tmp_resume = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="pathfind_resume_"
        )
        tmp_resume.write(resume_bytes)
        tmp_resume.flush()
        tmp_resume.close()
        payload["resume_path"] = tmp_resume.name
        logger.info("Resume written to temp file: %s", tmp_resume.name)
    else:
        logger.warning(
            "submit_to_portal_task: Could not obtain resume for Application #%s — "
            "submission will proceed without a file upload.",
            application_id,
        )

    # ── 5. Run browser automation ──────────────────────────────────────────
    submission_exc: Exception | None = None
    result: dict | None = None

    try:
        result = asyncio.run(_run_submission(apply_url, payload, api_key, project_id))
    except Exception as exc:  # noqa: BLE001
        submission_exc = exc
        logger.exception(
            "submit_to_portal_task: browser automation failed for Application #%s: %s",
            application_id,
            exc,
        )
    finally:
        # Always clean up the temp resume file
        if tmp_resume is not None:
            try:
                os.unlink(tmp_resume.name)
            except OSError:
                pass

    # ── 6a. Handle failure ─────────────────────────────────────────────────
    if submission_exc is not None:
        is_final_attempt = (self.request.retries >= self.max_retries)

        if not is_final_attempt:
            # Still have retries left — re-queue without touching the DB yet
            raise self.retry(exc=submission_exc)

        # ── All retries exhausted: capture screenshot + notify user ────────
        logger.error(
            "submit_to_portal_task: ALL retries exhausted for Application #%s. "
            "Notifying user to apply manually.",
            application_id,
        )

        # Attempt to grab a screenshot from the last failed run.
        # _run_submission stores it in a closure-captured variable that we
        # can't access here, so we flag the note without a screenshot reference
        # if it's unavailable.  (A future improvement: pass the screenshot
        # bytes through a Celery backend result or Redis key.)
        screenshot_note = _try_capture_screenshot_sync(
            apply_url, api_key, project_id, application_id
        )

        failure_note = (
            "\n\n[⚠ Auto-submit failed after all retries]\n"
            f"Error: {submission_exc}\n"
            f"Please apply manually at: {apply_url}\n"
            f"{screenshot_note}"
        ).strip()

        Application.objects.filter(pk=application_id).update(
            # Reset to saved so the user can still track it manually
            status=ApplicationStatus.SAVED,
            notes=models_concat_notes(app.notes, failure_note),
        )

        return {
            "status": "manual_required",
            "application_id": application_id,
            "error": str(submission_exc),
            "apply_url": apply_url,
            "screenshot_ref": screenshot_note or "",
        }

    # ── 6b. Persist success to Application ────────────────────────────────
    confirmation_text = result.get("confirmation_text", "")
    confirmation_url  = result.get("confirmation_url", "")

    confirmation_note = (
        f"\n\n[Auto-submitted via Pathfind]\n"
        f"Confirmation: {confirmation_url}\n"
        f"{confirmation_text}"
    ).strip()

    Application.objects.filter(pk=application_id).update(
        status=ApplicationStatus.APPLIED,
        applied_at=timezone.now(),
        notes=models_concat_notes(app.notes, confirmation_note),
    )

    logger.info(
        "submit_to_portal_task: Application #%s marked as APPLIED. "
        "Confirmation URL: %s",
        application_id,
        confirmation_url,
    )

    return {
        "status": "applied",
        "application_id": application_id,
        "confirmation_text": confirmation_text,
        "confirmation_url": confirmation_url,
        "browserbase_session_id": result.get("browserbase_session_id", ""),
    }


# ---------------------------------------------------------------------------
# Failure screenshot helper (sync wrapper)
# ---------------------------------------------------------------------------

def _try_capture_screenshot_sync(
    apply_url: str,
    api_key: str,
    project_id: str,
    application_id: int,
) -> str:
    """
    Attempt to open a *fresh* browser session solely to screenshot the current
    state of the application URL.  Used when all retries are exhausted.

    Returns a string to embed in Application.notes, or empty string on failure.
    """
    async def _inner() -> bytes | None:
        try:
            from browserbase import Browserbase
            from playwright.async_api import async_playwright

            bb = Browserbase(api_key=api_key)
            session = bb.sessions.create(project_id=project_id)
            connect_url = session.connect_url

            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(connect_url)
                context = browser.contexts[0]
                page    = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.goto(apply_url, wait_until="domcontentloaded", timeout=20_000)
                    return await _capture_screenshot(page)
                finally:
                    await browser.close()
                    try:
                        bb.sessions.update(session.id, status="REQUEST_RELEASE")
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_try_capture_screenshot_sync: could not open screenshot session: %s", exc
            )
            return None

    try:
        png_bytes = asyncio.run(_inner())
    except Exception as exc:  # noqa: BLE001
        logger.warning("_try_capture_screenshot_sync: asyncio.run failed: %s", exc)
        return ""

    if not png_bytes:
        return ""

    return _save_screenshot_to_application(application_id, png_bytes)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def models_concat_notes(existing_notes: str, new_note: str) -> str:
    """Append *new_note* to *existing_notes*, preserving any prior content."""
    if existing_notes:
        return f"{existing_notes.rstrip()}\n\n{new_note}"
    return new_note
