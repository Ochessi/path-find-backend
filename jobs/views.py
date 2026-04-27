from __future__ import annotations

import logging

from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import JobListing, Document, Application
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



