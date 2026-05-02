from __future__ import annotations

import logging

import numpy as np
from django.conf import settings
from django.core.cache import cache
from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import JobListing, Document, Application, JobEmbedding
from .parsers import ResumeParser
from .serializers import (
    JobListingSerializer,
    DocumentSerializer,
    ApplicationSerializer,
    ApplicationStatusUpdateSerializer,
)

logger = logging.getLogger(__name__)


class JobListingViewSet(viewsets.ModelViewSet):
    """
    CRUD for job listings.
    All authenticated users can read; only staff can create/update/delete
    (regular users create listings implicitly through Applications).
    """

    queryset = JobListing.objects.all()
    serializer_class = JobListingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "company", "location"]
    ordering_fields = ["created_at", "posted_at", "salary_min", "salary_max"]
    ordering = ["-created_at"]

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [permissions.IsAdminUser()]
        return super().get_permissions()


class DocumentViewSet(viewsets.ModelViewSet):
    """
    CRUD for documents belonging to the authenticated user.
    Users can only see and manage their own documents.
    """

    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Document.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ApplicationViewSet(viewsets.ModelViewSet):
    """
    CRUD for the authenticated user's application pipeline.

    Provides an additional PATCH action at /applications/{id}/status/
    for lightweight Kanban status updates without sending the full payload.
    """

    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["updated_at", "applied_at", "created_at"]
    ordering = ["-updated_at"]

    def get_queryset(self):
        qs = Application.objects.select_related(
            "job_listing", "resume", "cover_letter"
        ).filter(user=self.request.user)

        # Optional filter: ?status=applied
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        return qs

    def get_serializer_class(self):
        if self.action == "update_status":
            return ApplicationStatusUpdateSerializer
        return ApplicationSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        """PATCH /api/jobs/applications/{id}/status/ — move pipeline stage."""
        application = self.get_object()
        serializer = ApplicationStatusUpdateSerializer(
            application, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ApplicationSerializer(application).data)


# ---------------------------------------------------------------------------
# Resume Parsing Pipeline
# ---------------------------------------------------------------------------

class ResumeParseView(APIView):
    """
    POST /api/jobs/resume/parse/

    Accept a PDF or DOCX resume upload, run NLP extraction, and merge
    the results into the authenticated user's Profile.

    Request (multipart/form-data):
        file    — the resume file (PDF or DOCX)

    Response 200:
        {
            "extracted": {
                "skills":     ["Python", "Django", ...],
                "job_titles": ["Senior Backend Engineer"],
                "companies":  ["Acme Corp", ...],
                "emails":     ["jane@example.com"]
            },
            "profile_updated": {
                "headline": "Senior Backend Engineer",
                "skills":   [...],   // full updated list
                "experience": [...]  // full updated list
            }
        }
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response(
                {"detail": "No file provided. Send a 'file' field in multipart/form-data."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_type = uploaded_file.content_type or ""
        allowed = (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
            "text/plain",
        )
        if not any(ct in content_type for ct in allowed):
            return Response(
                {
                    "detail": (
                        f"Unsupported file type '{content_type}'. "
                        "Upload a PDF, DOCX, or plain-text resume."
                    )
                },
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        file_bytes = uploaded_file.read()
        parser = ResumeParser()

        try:
            extracted = parser.parse(file_bytes, content_type)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Resume parsing failed: %s", exc)
            return Response(
                {"detail": "Resume parsing failed. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ── Merge extracted data into the user's Profile ──────────────────
        profile_updated = self._update_profile(request.user, extracted)

        return Response(
            {"extracted": extracted, "profile_updated": profile_updated},
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _update_profile(user, extracted: dict) -> dict:
        """
        Merge NLP-extracted entities into the Profile model.

        Rules:
        - Skills: append newly found skills (by name, case-insensitive dedup).
        - Headline: set only if profile.headline is currently blank and we
          found at least one job title.
        - Experience: append a placeholder entry for each unique company
          found in the resume that isn't already in the experience list.
        """
        try:
            profile = user.profile
        except Exception:  # noqa: BLE001
            # Profile doesn't exist yet — create it.
            from accounts.models import Profile
            profile = Profile.objects.create(user=user)

        changed = False

        # ── Skills ────────────────────────────────────────────────────────
        existing_skill_names: set[str] = {
            s.get("name", "").lower() for s in (profile.skills or [])
        }
        new_skills = list(profile.skills or [])
        for skill_name in extracted.get("skills", []):
            if skill_name.lower() not in existing_skill_names:
                new_skills.append({"name": skill_name, "level": "intermediate"})
                existing_skill_names.add(skill_name.lower())
                changed = True
        if changed:
            profile.skills = new_skills

        # ── Headline ──────────────────────────────────────────────────────
        if not profile.headline:
            job_titles = extracted.get("job_titles", [])
            if job_titles:
                profile.headline = job_titles[0]
                changed = True

        # ── Experience (company stubs) ─────────────────────────────────────
        existing_companies: set[str] = {
            e.get("company", "").lower() for e in (profile.experience or [])
        }
        new_experience = list(profile.experience or [])
        for company_name in extracted.get("companies", []):
            if company_name.lower() not in existing_companies and len(company_name) > 2:
                new_experience.append(
                    {
                        "title": "",
                        "company": company_name,
                        "start_date": None,
                        "end_date": None,
                        "current": False,
                        "description": "",
                    }
                )
                existing_companies.add(company_name.lower())
                changed = True
        if changed:
            profile.experience = new_experience

        if changed:
            profile.save(update_fields=["skills", "headline", "experience", "updated_at"])

        return {
            "headline": profile.headline,
            "skills": profile.skills,
            "experience": profile.experience,
        }


# ---------------------------------------------------------------------------
# Curated Feed — Semantic Matching
# ---------------------------------------------------------------------------

class CuratedFeedView(APIView):
    """
    GET /api/jobs/feed/

    Returns job listings dynamically ranked by semantic similarity between
    the authenticated user's profile and each job listing's Sentence-BERT
    embedding.

    Algorithm
    ─────────
    1. Build a natural-language text document from the user's Profile fields
       (headline, skills, experience, preferences, etc.).
    2. Encode the profile text into a dense L2-normalised vector using the
       cached Sentence-BERT singleton.
    3. Retrieve all pre-computed job embeddings from the DB in a single query.
    4. Compute cosine similarity (= dot product of normalised vectors) between
       the profile vector and every job vector — this is an in-memory NumPy
       operation and is very fast (< 1 ms for thousands of jobs).
    5. Sort listings by descending score and return a paginated response.

    Query parameters
    ────────────────
    limit  (int, default 20) — Number of results per page.
    offset (int, default 0)  — Pagination offset.
    min_score (float, default 0.0) — Only return jobs with a similarity score
               at or above this threshold (0 = all, 0.5 = moderately similar).

    Response 200
    ────────────
    {
        "count":   <total matching jobs>,
        "limit":   20,
        "offset":  0,
        "profile_complete": true,   // false → profile too sparse to score
        "results": [
            {
                "id": 1,
                "title": "Senior Python Engineer",
                ...                          // all JobListing fields
                "similarity_score": 0.847
            },
            ...
        ]
    }

    Notes
    ─────
    - Jobs without a pre-computed embedding are excluded from the ranked feed.
      They still appear in the standard /api/jobs/listings/ endpoint.
    - If the user's profile is empty (no headline / skills / experience), the
      endpoint falls back to returning listings ordered by creation date with
      ``profile_complete: false`` to inform the frontend.
    - Embedding computation is non-blocking: new jobs are encoded by a Celery
      worker; they appear in the curated feed once the task completes (usually
      within seconds).
    """

    permission_classes = [permissions.IsAuthenticated]

    # Minimum text length (chars) below which we consider the profile too sparse
    # to produce a meaningful embedding and fall back to date-ordered results.
    _MIN_PROFILE_TEXT_LEN = 30

    # Safety cap: never load more than this many vectors into memory at once.
    # At 384 floats × 4 bytes each, 50 000 vectors ≈ 77 MB — very manageable.
    _MAX_JOB_VECTORS = 50_000

    # Profile vector cache TTL (seconds).  Invalidated on profile save via
    # a separate post_save signal (see jobs/signals.py).
    _PROFILE_VECTOR_TTL = 3600  # 1 hour

    def get(self, request):
        from jobs.services.embedding_service import (
            build_profile_text,
            encode,
            rank_jobs_by_similarity,
        )

        # ── Parse query parameters ────────────────────────────────────────
        try:
            limit = max(1, min(100, int(request.query_params.get("limit", 20))))
            offset = max(0, int(request.query_params.get("offset", 0)))
            min_score = float(request.query_params.get("min_score", 0.0))
        except (ValueError, TypeError):
            return Response(
                {"detail": "Invalid query parameters. 'limit', 'offset', and 'min_score' must be numbers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Build and encode profile text ────────────────────────────────
        profile_text = ""
        profile_complete = False
        profile_updated_at = None

        try:
            profile = request.user.profile
            profile_text = build_profile_text(profile)
            profile_complete = len(profile_text) >= self._MIN_PROFILE_TEXT_LEN
            profile_updated_at = profile.updated_at
        except Exception:  # noqa: BLE001
            # Profile doesn't exist yet — treat as incomplete.
            pass

        # ── Fallback: no meaningful profile → return by date ──────────────
        if not profile_complete:
            logger.info(
                "CuratedFeedView: user %s has sparse profile — falling back to date order.",
                request.user.pk,
            )
            qs = JobListing.objects.all()[offset : offset + limit]
            serializer = JobListingSerializer(qs, many=True)
            total = JobListing.objects.count()
            return Response(
                {
                    "count": total,
                    "limit": limit,
                    "offset": offset,
                    "profile_complete": False,
                    "results": serializer.data,
                }
            )

        # ── Encode the profile (with per-user cache) ──────────────────────
        # Cache key includes user pk + profile updated_at timestamp so the
        # vector is automatically invalidated whenever the profile changes.
        model_name: str = getattr(settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
        cache_key = (
            f"profile_vector:{request.user.pk}:"
            f"{profile_updated_at.timestamp() if profile_updated_at else 'none'}:"
            f"{model_name}"
        )

        profile_vector: np.ndarray | None = cache.get(cache_key)
        if profile_vector is None:
            try:
                profile_vector = encode(profile_text)
                cache.set(cache_key, profile_vector, self._PROFILE_VECTOR_TTL)
            except Exception as exc:  # noqa: BLE001
                logger.exception("CuratedFeedView: profile encoding failed: %s", exc)
                return Response(
                    {"detail": "Semantic scoring temporarily unavailable. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        # ── Load cached job embeddings (bounded query) ────────────────────
        # Filter by active model_name so we never compare vectors produced
        # by different models, and cap at MAX_JOB_VECTORS to bound memory.
        embeddings_qs = (
            JobEmbedding.objects
            .filter(model_name=model_name)
            .values_list("job_listing_id", "vector")[: self._MAX_JOB_VECTORS]
        )
        job_vectors: dict[int, np.ndarray] = {
            jid: np.array(vec, dtype=np.float32)
            for jid, vec in embeddings_qs
            if vec  # skip corrupted / empty rows
        }

        if not job_vectors:
            # No embeddings yet — queue a back-fill and return empty.
            from jobs.tasks import recompute_all_embeddings
            recompute_all_embeddings.delay()
            logger.info("CuratedFeedView: no embeddings found — back-fill queued.")
            return Response(
                {
                    "count": 0,
                    "limit": limit,
                    "offset": offset,
                    "profile_complete": True,
                    "detail": "Job index is being built. Please try again in a moment.",
                    "results": [],
                }
            )

        # ── Rank by cosine similarity ─────────────────────────────────────
        ranked = rank_jobs_by_similarity(profile_vector, job_vectors)

        # Apply minimum score threshold.
        if min_score > 0.0:
            ranked = [(jid, score) for jid, score in ranked if score >= min_score]

        total_ranked = len(ranked)

        # Paginate the ranked list before hitting the DB for full listing data.
        page_ranked = ranked[offset : offset + limit]
        page_ids = [jid for jid, _ in page_ranked]
        score_map: dict[int, float] = {jid: score for jid, score in page_ranked}

        if not page_ids:
            return Response(
                {
                    "count": total_ranked,
                    "limit": limit,
                    "offset": offset,
                    "profile_complete": True,
                    "results": [],
                }
            )

        # Fetch full listing objects for this page — preserve ranked order.
        listings_by_id = {
            job.pk: job
            for job in JobListing.objects.filter(pk__in=page_ids)
        }
        ordered_listings = [listings_by_id[jid] for jid in page_ids if jid in listings_by_id]

        # ── Annotate with similarity score ────────────────────────────────
        # Attach the score as a transient attribute; SerializerMethodField in
        # JobListingSerializer reads it via getattr(obj, 'similarity_score', None).
        for listing in ordered_listings:
            listing.similarity_score = round(score_map[listing.pk], 4)

        serializer = JobListingSerializer(ordered_listings, many=True)

        return Response(
            {
                "count": total_ranked,
                "limit": limit,
                "offset": offset,
                "profile_complete": True,
                "results": serializer.data,
            }
        )


# ---------------------------------------------------------------------------
# Embedding Status — admin health-check for the semantic pipeline
# ---------------------------------------------------------------------------

class EmbeddingStatusView(APIView):
    """
    GET /api/jobs/embedding-status/

    Returns coverage statistics for the JobEmbedding cache so operators
    can verify the semantic pipeline is healthy.  Staff-only endpoint.

    Response 200:
    {
        "total_listings":   150,
        "embedded":         142,   // vectors matching the active model
        "missing":            8,   // no embedding row at all
        "stale":              3,   // embedding exists but for a different model
        "needs_update":      11,   // missing + stale
        "coverage_pct":    94.7,
        "model_name": "all-MiniLM-L6-v2"
    }

    POST /api/jobs/embedding-status/
    Queues a Celery task to backfill missing + stale embeddings.
    Response 202: { "queued": true, "missing": 8, "stale": 3, "total": 11 }
    """

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        model_name: str = getattr(settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

        total = JobListing.objects.count()

        # Only count vectors produced by the *active* model as healthy.
        current_embedded = JobEmbedding.objects.filter(model_name=model_name).count()

        # Vectors from an older/different model — still in DB but excluded from ranking.
        stale = JobEmbedding.objects.exclude(model_name=model_name).count()

        # Listings with no embedding row at all.
        all_embedded_ids = JobEmbedding.objects.values_list("job_listing_id", flat=True)
        missing = JobListing.objects.exclude(pk__in=all_embedded_ids).count()

        needs_update = missing + stale
        coverage = round((current_embedded / total * 100), 1) if total else 0.0

        return Response(
            {
                "total_listings": total,
                "embedded": current_embedded,
                "missing": missing,
                "stale": stale,
                "needs_update": needs_update,
                "coverage_pct": coverage,
                "model_name": model_name,
            }
        )

    def post(self, request):
        from jobs.tasks import recompute_all_embeddings

        model_name: str = getattr(settings, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

        all_embedded_ids = JobEmbedding.objects.values_list("job_listing_id", flat=True)
        missing = JobListing.objects.exclude(pk__in=all_embedded_ids).count()
        stale = JobEmbedding.objects.exclude(model_name=model_name).count()
        total = missing + stale

        if total == 0:
            return Response(
                {
                    "queued": False,
                    "missing": 0,
                    "stale": 0,
                    "total": 0,
                    "detail": "All listings are already embedded with the current model.",
                },
                status=status.HTTP_200_OK,
            )

        recompute_all_embeddings.delay()
        logger.info(
            "EmbeddingStatusView: backfill queued — %d missing, %d stale.", missing, stale
        )

        return Response(
            {"queued": True, "missing": missing, "stale": stale, "total": total},
            status=status.HTTP_202_ACCEPTED,
        )

# ---------------------------------------------------------------------------
# AI Generation - Tailored Application Content
# ---------------------------------------------------------------------------

from django.shortcuts import get_object_or_404
from .services.ai_generator import generate_application_content

class ApplicationAIGenerateView(APIView):
    """
    POST /api/jobs/applications/generate/
    
    Generates tailored resume bullet points and a cover letter for a specific job listing
    using the authenticated user's profile and the Google AI Studio (Gemini) API.
    
    Request body:
        job_listing_id (int): The ID of the JobListing to tailor the application for.
        
    Response 200:
        {
            "tailored_bullets": "...",
            "cover_letter": "...",
            "error": null
        }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        job_listing_id = request.data.get("job_listing_id")
        if not job_listing_id:
            return Response(
                {"detail": "job_listing_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        job_listing = get_object_or_404(JobListing, pk=job_listing_id)
        
        try:
            profile = request.user.profile
        except Exception:
            return Response(
                {"detail": "User profile not found. Please complete your profile first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate content using Gemini
        generated_content = generate_application_content(profile, job_listing)
        
        if "error" in generated_content:
            return Response(
                {"detail": "Failed to generate application content.", "error": generated_content["error"]},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(generated_content, status=status.HTTP_200_OK)

