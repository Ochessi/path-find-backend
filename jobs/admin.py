from django.contrib import admin
from .models import JobListing, Document, Application


# ---------------------------------------------------------------------------
# JobListing Admin
# ---------------------------------------------------------------------------

@admin.register(JobListing)
class JobListingAdmin(admin.ModelAdmin):
    list_display = (
        "title", "company", "location", "employment_type",
        "is_remote", "source", "posted_at", "created_at",
    )
    list_filter = ("source", "employment_type", "is_remote")
    search_fields = ("title", "company", "location", "description")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("title", "company", "location", "description")}),
        ("Classification", {"fields": ("source", "source_url", "employment_type", "is_remote")}),
        ("Compensation", {"fields": ("salary_min", "salary_max")}),
        ("Timestamps", {"fields": ("posted_at", "created_at", "updated_at")}),
    )


# ---------------------------------------------------------------------------
# Document Admin
# ---------------------------------------------------------------------------

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("user", "file_name", "doc_type", "is_ai_generated", "created_at")
    list_filter = ("doc_type", "is_ai_generated")
    search_fields = ("user__email", "file_name")
    readonly_fields = ("created_at",)
    fieldsets = (
        (None, {"fields": ("user", "file_name", "doc_type", "is_ai_generated")}),
        ("Storage", {"fields": ("file_url", "storage_key")}),
        ("Timestamps", {"fields": ("created_at",)}),
    )


# ---------------------------------------------------------------------------
# Application Admin
# ---------------------------------------------------------------------------

class ApplicationDocumentInline(admin.StackedInline):
    """Quick view of attached documents from the Application change page."""

    model = Application
    fields = ("resume", "cover_letter")
    can_delete = False
    verbose_name_plural = "Documents"
    extra = 0


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "user", "job_listing", "status", "applied_at", "updated_at",
    )
    list_filter = ("status",)
    search_fields = ("user__email", "job_listing__title", "job_listing__company")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("job_listing",)
    raw_id_fields = ("resume", "cover_letter")
    fieldsets = (
        (None, {"fields": ("user", "job_listing", "status")}),
        ("Documents", {"fields": ("resume", "cover_letter")}),
        ("Details", {"fields": ("notes", "applied_at")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
