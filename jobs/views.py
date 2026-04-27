from rest_framework import viewsets, permissions, filters
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import JobListing, Document, Application
from .serializers import (
    JobListingSerializer,
    DocumentSerializer,
    ApplicationSerializer,
    ApplicationStatusUpdateSerializer,
)


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
