from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Profile


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_active")
    search_fields = ("email", "full_name")
    ordering = ("-date_joined",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name", "avatar_url", "google_id")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("date_joined", "updated_at")}),
    )
    readonly_fields = ("date_joined", "updated_at")

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2", "full_name"),
        }),
    )


class ProfileInline(admin.StackedInline):
    """Embed the Profile editor directly inside the User change page."""

    model = Profile
    can_delete = False
    verbose_name_plural = "Profile"
    fields = (
        "headline",
        "location",
        "phone",
        "linkedin_url",
        "portfolio_url",
        "bio",
        "experience",
        "education",
        "skills",
        "job_preferences",
    )


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "headline", "location", "updated_at")
    search_fields = ("user__email", "headline", "location")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("user", "headline", "location", "phone", "bio")}),
        ("Links", {"fields": ("linkedin_url", "portfolio_url")}),
        ("JSON Data", {"fields": ("experience", "education", "skills", "job_preferences")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
