from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Profile

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("email", "password", "full_name")

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            full_name=validated_data.get("full_name", ""),
        )


class ProfileSerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for the Profile model.
    The JSON array fields (experience, education, skills, job_preferences)
    are passed through as-is; shape validation is left to the frontend
    and any future dedicated validators.
    """

    class Meta:
        model = Profile
        fields = (
            "id",
            "headline",
            "location",
            "phone",
            "linkedin_url",
            "github_url",
            "portfolio_url",
            "bio",
            "experience",
            "education",
            "skills",
            "job_preferences",
            "career_intelligence",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class UserSerializer(serializers.ModelSerializer):
    """Read-only user representation, optionally includes nested profile."""

    profile = ProfileSerializer(read_only=True)
    onboarding_complete = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "full_name", "avatar_url", "date_joined", "profile", "onboarding_complete")
        read_only_fields = ("id", "date_joined")

    def get_onboarding_complete(self, obj):
        """Expose onboarding_complete from profile.job_preferences as a top-level bool."""
        try:
            prefs = obj.profile.job_preferences or {}
            return bool(prefs.get("onboarding_complete", False))
        except Exception:
            return False
