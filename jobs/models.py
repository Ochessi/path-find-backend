from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models


def _resume_upload_path(instance, filename):
    """Store master resumes under media/resumes/<user_id>/<filename>."""
    return f"resumes/{instance.user_id}/{filename}"


# ---------------------------------------------------------------------------
# Choice enumerations
# ---------------------------------------------------------------------------

class JobSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    ADZUNA = "adzuna", "Adzuna"
    JOOBLE = "jooble", "Jooble"
    CAREERJET = "careerjet", "Careerjet"
    LINKEDIN = "linkedin", "LinkedIn"
    INDEED = "indeed", "Indeed"
    GLASSDOOR = "glassdoor", "Glassdoor"
    LEVER = "lever", "Lever"
    GREENHOUSE = "greenhouse", "Greenhouse"
    OTHER = "other", "Other"


class EmploymentType(models.TextChoices):
    FULL_TIME = "full_time", "Full-time"
    PART_TIME = "part_time", "Part-time"
    CONTRACT = "contract", "Contract"
    INTERNSHIP = "internship", "Internship"
    FREELANCE = "freelance", "Freelance"


class ApplicationStatus(models.TextChoices):
    SAVED = "saved", "Saved"
    APPLIED = "applied", "Applied"
    PHONE_SCREEN = "phone_screen", "Phone Screen"
    INTERVIEWING = "interviewing", "Interviewing"
    OFFER = "offer", "Offer"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class DocumentType(models.TextChoices):
    RESUME = "resume", "Resume"
    COVER_LETTER = "cover_letter", "Cover Letter"
    PORTFOLIO = "portfolio", "Portfolio"
    OTHER = "other", "Other"


# ---------------------------------------------------------------------------
# JobListing
# ---------------------------------------------------------------------------

class JobListing(models.Model):
    """
    A normalised record of a job posting, regardless of source.

    Designed to be source-agnostic: whether a listing comes from a scraper,
    a manual entry by the user, or a future integration, it lands here in the
    same shape. The `source` + `source_url` fields preserve provenance.
    """

    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    source = models.CharField(
        max_length=20,
        choices=JobSource.choices,
        default=JobSource.MANUAL,
        db_index=True,
    )
    source_url = models.URLField(
        max_length=2000,
        blank=True,
        help_text="Original URL of the job posting, if available.",
    )

    employment_type = models.CharField(
        max_length=20,
        choices=EmploymentType.choices,
        default=EmploymentType.FULL_TIME,
        blank=True,
    )
    is_remote = models.BooleanField(default=False)

    salary_min = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Annual salary lower bound (currency assumed from user preference).",
    )
    salary_max = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Annual salary upper bound.",
    )

    posted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Original posting date from the source, if available.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "job listing"
        verbose_name_plural = "job listings"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "title"]),
            models.Index(fields=["source", "source_url"]),
        ]

    def __str__(self):
        return f"{self.title} @ {self.company}"


# ---------------------------------------------------------------------------
# JobEmbedding — cached Sentence-BERT vector for a JobListing
# ---------------------------------------------------------------------------

class JobEmbedding(models.Model):
    """
    Stores the precomputed Sentence-BERT embedding for a JobListing.

    Separating embeddings into their own table keeps the hot ``JobListing``
    rows lean and lets us invalidate / recompute vectors independently of the
    listing data.  The vector is persisted as a PostgreSQL float[] so it can
    be retrieved in a single SELECT without deserialisation overhead.

    Lifecycle:
      - Created / updated by the ``compute_job_embedding`` Celery task which
        is triggered via a post-save signal on ``JobListing``.
      - Read by ``CuratedFeedView`` to rank listings by cosine similarity.
    """

    job_listing = models.OneToOneField(
        JobListing,
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    # Dense float vector — length depends on the model (384 for MiniLM-L6-v2).
    vector = ArrayField(
        models.FloatField(),
        help_text="L2-normalised embedding vector produced by the SBERT model.",
    )
    model_name = models.CharField(
        max_length=200,
        default="all-MiniLM-L6-v2",
        help_text="Identifier of the model used to produce this vector.",
    )
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "job embedding"
        verbose_name_plural = "job embeddings"

    def __str__(self):
        return f"Embedding for: {self.job_listing}"


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

class Document(models.Model):
    """
    A reference to a file hosted in an external storage bucket (S3 / Cloudinary).

    This model intentionally stores only *metadata and the URL* — the file
    itself lives in the bucket. The `file_url` can be a public URL or a
    pre-signed URL refreshed at read time by the storage backend.

    `is_ai_generated` flags documents produced by Pathfind's AI tools
    (tailored resumes, generated cover letters) for analytics and auditing.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
    )

    file_url = models.URLField(
        max_length=2000,
        help_text="Full URL to the file in the storage bucket.",
    )
    file_name = models.CharField(
        max_length=500,
        help_text="Original filename as uploaded by the user or generated by AI.",
    )
    # S3 object key so we can delete / refresh the file later without
    # parsing the URL (which changes shape between public and pre-signed).
    storage_key = models.CharField(
        max_length=1000,
        blank=True,
        help_text="Storage backend object key (e.g. S3 key). Used for deletion and refresh.",
    )

    doc_type = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
        default=DocumentType.RESUME,
        db_index=True,
    )
    is_ai_generated = models.BooleanField(
        default=False,
        help_text="True if this document was generated or tailored by Pathfind AI.",
    )
    is_master = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if this is the user's original uploaded Master Resume.",
    )
    # Raw file stored locally (populated during resume-parse onboarding).
    # Cloud-hosted documents leave this blank and use file_url instead.
    file = models.FileField(
        upload_to=_resume_upload_path,
        null=True,
        blank=True,
        help_text="Raw uploaded file (local storage fallback when no cloud bucket is set).",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "document"
        verbose_name_plural = "documents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_doc_type_display()} — {self.file_name}"


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class Application(models.Model):
    """
    The central join table that tracks a user's job application pipeline.

    Each row represents one user's pursuit of one job listing.  The `status`
    field drives the Kanban / list view in the frontend.  Optional FK links to
    the specific resume and cover letter documents used for this application
    enable AI-tailoring workflows.

    Constraint: a user can only have one Application per JobListing
    (`unique_together`), which prevents accidental duplicates while still
    allowing multiple users to apply to the same listing.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    job_listing = models.ForeignKey(
        JobListing,
        on_delete=models.CASCADE,
        related_name="applications",
    )

    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SAVED,
        db_index=True,
    )

    # Tailored documents attached to this specific application
    resume = models.ForeignKey(
        Document,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applications_as_resume",
        help_text="Tailored resume used for this application.",
    )
    cover_letter = models.ForeignKey(
        Document,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applications_as_cover_letter",
        help_text="Cover letter used for this application.",
    )

    notes = models.TextField(
        blank=True,
        help_text="Private notes about this application (interviews, contacts, etc.).",
    )
    # Stores AI-generated tailored_bullets, cover_letter, and form_fields.
    # Populated by generate_application_content_task; editable by the user.
    ai_content = models.JSONField(
        null=True,
        blank=True,
        help_text="AI-generated application content (tailored_bullets, cover_letter, form_fields).",
    )
    applied_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user actually submitted the application.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "application"
        verbose_name_plural = "applications"
        ordering = ["-updated_at"]
        # One application entry per user per job listing.
        unique_together = [("user", "job_listing")]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.user.email} → {self.job_listing} [{self.get_status_display()}]"
