from rest_framework import serializers
from .models import JobListing, Document, Application


# ---------------------------------------------------------------------------
# JobListing
# ---------------------------------------------------------------------------

class JobListingSerializer(serializers.ModelSerializer):
    """
    Full serializer for a job listing.
    Includes human-readable choice labels as extra read-only fields.
    """

    source_display = serializers.CharField(source="get_source_display", read_only=True)
    employment_type_display = serializers.CharField(
        source="get_employment_type_display", read_only=True
    )

    class Meta:
        model = JobListing
        fields = (
            "id",
            "title",
            "company",
            "location",
            "description",
            "source",
            "source_display",
            "source_url",
            "employment_type",
            "employment_type_display",
            "is_remote",
            "salary_min",
            "salary_max",
            "posted_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class JobListingMiniSerializer(serializers.ModelSerializer):
    """Lightweight serializer for embedding in Application responses."""

    class Meta:
        model = JobListing
        fields = ("id", "title", "company", "location", "employment_type", "is_remote")
        read_only_fields = ("id",)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

class DocumentSerializer(serializers.ModelSerializer):
    """
    Serializer for Document metadata.
    The actual file upload to S3 is handled separately via a presigned-URL
    flow; this serializer deals only with the metadata record.
    """

    doc_type_display = serializers.CharField(source="get_doc_type_display", read_only=True)

    class Meta:
        model = Document
        fields = (
            "id",
            "user",
            "file_url",
            "file_name",
            "storage_key",
            "doc_type",
            "doc_type_display",
            "is_ai_generated",
            "created_at",
        )
        read_only_fields = ("id", "user", "created_at")

    def create(self, validated_data):
        # Automatically bind the document to the requesting user.
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            validated_data["user"] = request.user
        return super().create(validated_data)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class ApplicationSerializer(serializers.ModelSerializer):
    """
    Full serializer for an Application record.

    On reads: embeds a lightweight job listing snapshot and document metadata.
    On writes: accepts FKs for job_listing, resume, and cover_letter.
    The user field is always derived from the authenticated request context.
    """

    job_listing_detail = JobListingMiniSerializer(source="job_listing", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Application
        fields = (
            "id",
            "user",
            "job_listing",
            "job_listing_detail",
            "status",
            "status_display",
            "resume",
            "cover_letter",
            "notes",
            "applied_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "user", "created_at", "updated_at")

    def create(self, validated_data):
        # Automatically bind the application to the requesting user.
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            validated_data["user"] = request.user
        return super().create(validated_data)


class ApplicationStatusUpdateSerializer(serializers.ModelSerializer):
    """
    Minimal serializer for PATCH requests that only update the pipeline status
    (e.g. moving a card on the Kanban board).
    """

    class Meta:
        model = Application
        fields = ("status", "applied_at", "notes")
