from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)

    # Populated when the user signs in via Google OAuth
    google_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email


class Profile(models.Model):
    """
    Extended profile information for a User.

    Scalar fields capture structured, searchable data (headline, location, etc.).
    JSONField arrays store flexible, schema-light collections so we avoid
    over-normalised join tables for experience, education, skills, and job
    preferences — all of which are user-specific and change frequently.

    Expected JSON shapes (enforced by the frontend / serializer layer):

    experience (list):
        [{"title": str, "company": str, "start_date": "YYYY-MM",
          "end_date": "YYYY-MM" | null, "current": bool, "description": str}]

    education (list):
        [{"degree": str, "institution": str, "field_of_study": str,
          "start_year": int, "end_year": int | null}]

    skills (list):
        [{"name": str, "level": "beginner"|"intermediate"|"advanced"|"expert"}]

    job_preferences (object):
        {"desired_titles": [str], "desired_locations": [str],
         "remote": "remote"|"hybrid"|"on-site"|"any",
         "salary_min": int | null, "salary_max": int | null,
         "employment_types": ["full_time"|"part_time"|"contract"|"internship"],
         "open_to_relocation": bool}
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # ── Scalar fields ────────────────────────────────────────────────────────
    headline = models.CharField(
        max_length=255,
        blank=True,
        help_text="Short professional headline (e.g. 'Senior Backend Engineer').",
    )
    location = models.CharField(
        max_length=255,
        blank=True,
        help_text="City, Country.",
    )
    phone = models.CharField(max_length=30, blank=True)
    linkedin_url = models.URLField(max_length=500, blank=True)
    portfolio_url = models.URLField(max_length=500, blank=True)
    bio = models.TextField(blank=True)

    # ── Flexible JSON arrays / objects ───────────────────────────────────────
    experience = models.JSONField(
        default=list,
        blank=True,
        help_text="List of work experience objects.",
    )
    education = models.JSONField(
        default=list,
        blank=True,
        help_text="List of education objects.",
    )
    skills = models.JSONField(
        default=list,
        blank=True,
        help_text="List of skill objects with name and level.",
    )
    job_preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text="Desired roles, locations, salary range, remote preference, etc.",
    )

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profile"
        verbose_name_plural = "profiles"

    def __str__(self):
        return f"Profile of {self.user.email}"
