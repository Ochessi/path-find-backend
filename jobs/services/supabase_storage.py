"""
jobs.services.supabase_storage
================================
Thin wrapper around the Supabase Storage REST API for uploading and
retrieving user resume files.

The bucket name and Supabase credentials are read from Django settings
(which in turn read from environment variables):

    SUPABASE_URL               — https://<project>.supabase.co
    SUPABASE_SERVICE_ROLE_KEY  — service_role secret (never the anon key)
    SUPABASE_STORAGE_BUCKET    — name of the Storage bucket (e.g. "resumes")

Usage
-----
::

    from jobs.services.supabase_storage import upload_resume, get_public_url

    storage_key, public_url = upload_resume(
        user_id=42,
        file_bytes=b"...",
        filename="cv.pdf",
        content_type="application/pdf",
    )

    # To download the raw bytes back later:
    from jobs.services.supabase_storage import download_file
    file_bytes = download_file(storage_key)
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def _get_client():
    """
    Lazily initialise the supabase-py client so the import doesn't fail if
    the package isn't installed (local dev without storage configured).
    """
    from django.conf import settings

    try:
        from supabase import create_client, Client  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "supabase-py is not installed. Run: pip install supabase"
        )

    url: str = getattr(settings, "SUPABASE_URL", "")
    key: str = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in settings / .env"
        )

    client: Client = create_client(url, key)
    return client


def _bucket_name() -> str:
    from django.conf import settings
    return getattr(settings, "SUPABASE_STORAGE_BUCKET", "resumes")


def upload_resume(
    user_id: int,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> tuple[str, str]:
    """
    Upload a resume file to the configured Supabase Storage bucket.

    Parameters
    ----------
    user_id     : int   — Django User PK; used to namespace the storage path.
    file_bytes  : bytes — Raw file content.
    filename    : str   — Original filename (e.g. "my_resume.pdf").
    content_type: str   — MIME type (e.g. "application/pdf").

    Returns
    -------
    (storage_key, public_url) : tuple[str, str]
        storage_key — the path inside the bucket (e.g. "42/uuid-my_resume.pdf").
        public_url  — the public or signed URL for the uploaded file.

    Raises
    ------
    RuntimeError if the upload fails.
    """
    client = _get_client()
    bucket = _bucket_name()

    # Build a unique, collision-resistant object key.
    unique_prefix = uuid.uuid4().hex[:8]
    safe_name = filename.replace(" ", "_")
    storage_key = f"{user_id}/{unique_prefix}-{safe_name}"

    try:
        # supabase-py Storage upload
        response = client.storage.from_(bucket).upload(
            path=storage_key,
            file=file_bytes,
            file_options={"content-type": content_type, "upsert": "false"},
        )
    except Exception as exc:
        logger.error("Supabase upload failed for user %s: %s", user_id, exc)
        raise RuntimeError(f"Supabase upload failed: {exc}") from exc

    # Build the public URL
    public_url = _get_public_url(storage_key)

    logger.info(
        "Uploaded resume for user %s → bucket=%r key=%r url=%r",
        user_id, bucket, storage_key, public_url,
    )
    return storage_key, public_url


def _get_public_url(storage_key: str) -> str:
    """Return the public URL for a storage object."""
    from django.conf import settings

    supabase_url: str = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    bucket = _bucket_name()
    # Supabase public URL format:
    # <supabase_url>/storage/v1/object/public/<bucket>/<key>
    return f"{supabase_url}/storage/v1/object/public/{bucket}/{storage_key}"


def get_signed_url(storage_key: str, expires_in: int = 3600) -> str:
    """
    Generate a temporary signed URL for a private-bucket object.

    Parameters
    ----------
    storage_key : str — the object path inside the bucket.
    expires_in  : int — seconds until the URL expires (default 1 hour).

    Returns
    -------
    str — the signed URL.
    """
    client = _get_client()
    bucket = _bucket_name()

    try:
        response = client.storage.from_(bucket).create_signed_url(
            path=storage_key,
            expires_in=expires_in,
        )
        return response.get("signedURL") or response.get("signed_url") or ""
    except Exception as exc:
        logger.error("Failed to generate signed URL for %r: %s", storage_key, exc)
        return ""


def download_file(storage_key: str) -> Optional[bytes]:
    """
    Download the raw bytes of a file stored in Supabase Storage.

    Returns None if the download fails (logged as an error).
    """
    client = _get_client()
    bucket = _bucket_name()

    try:
        data = client.storage.from_(bucket).download(storage_key)
        return data
    except Exception as exc:
        logger.error("Failed to download %r from Supabase: %s", storage_key, exc)
        return None


def delete_file(storage_key: str) -> bool:
    """
    Delete a file from Supabase Storage.

    Returns True on success, False on failure.
    """
    client = _get_client()
    bucket = _bucket_name()

    try:
        client.storage.from_(bucket).remove([storage_key])
        logger.info("Deleted Supabase object: %r", storage_key)
        return True
    except Exception as exc:
        logger.error("Failed to delete %r from Supabase: %s", storage_key, exc)
        return False
