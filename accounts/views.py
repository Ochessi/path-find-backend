from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView  # re-exported for URL
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from .models import Profile
from .serializers import RegisterSerializer, UserSerializer, ProfileSerializer

User = get_user_model()


def _jwt_pair(user):
    """Return a dict with access + refresh tokens for the given user."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


# ---------------------------------------------------------------------------
# POST /api/auth/register/
# ---------------------------------------------------------------------------
class RegisterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Register User",
        request=RegisterSerializer,
        responses={
            201: inline_serializer(
                name='RegisterResponse',
                fields={
                    'user': UserSerializer(),
                    'access': serializers.CharField(),
                    'refresh': serializers.CharField(),
                }
            )
        }
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # get_or_create is safe if a post_save signal already created a Profile.
        Profile.objects.get_or_create(user=user)

        return Response(
            {"user": UserSerializer(user).data, **_jwt_pair(user)},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# POST /api/auth/login/
# ---------------------------------------------------------------------------
class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Login User",
        request=inline_serializer(
            name='LoginRequest',
            fields={
                'email': serializers.EmailField(),
                'password': serializers.CharField(),
            }
        ),
        responses={
            200: inline_serializer(
                name='LoginResponse',
                fields={
                    'user': UserSerializer(),
                    'access': serializers.CharField(),
                    'refresh': serializers.CharField(),
                }
            ),
            401: inline_serializer(name='LoginError', fields={'detail': serializers.CharField()}),
        }
    )
    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"detail": "Account is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response({"user": UserSerializer(user).data, **_jwt_pair(user)})


# ---------------------------------------------------------------------------
# POST /api/auth/google/
# Expects: { "id_token": "<google id token from frontend>" }
# ---------------------------------------------------------------------------
class GoogleAuthView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Google OAuth Login",
        request=inline_serializer(
            name='GoogleAuthRequest',
            fields={'id_token': serializers.CharField()}
        ),
        responses={
            200: inline_serializer(
                name='GoogleAuthResponse',
                fields={
                    'user': UserSerializer(),
                    'access': serializers.CharField(),
                    'refresh': serializers.CharField(),
                }
            )
        }
    )
    def post(self, request):
        token = request.data.get("id_token", "")
        if not token:
            return Response(
                {"detail": "id_token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            id_info = id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError as exc:
            return Response(
                {"detail": f"Invalid Google token: {exc}"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        google_id = id_info["sub"]
        email = id_info.get("email", "")
        full_name = id_info.get("name", "")
        avatar_url = id_info.get("picture", "")

        # Get-or-create the user, linking by google_id or falling back to email.
        user = (
            User.objects.filter(google_id=google_id).first()
            or User.objects.filter(email=email).first()
        )

        if user:
            # Update Google fields in case they changed.
            updated = False
            if not user.google_id:
                user.google_id = google_id
                updated = True
            if avatar_url and user.avatar_url != avatar_url:
                user.avatar_url = avatar_url
                updated = True
            if updated:
                user.save(update_fields=["google_id", "avatar_url"])
        else:
            user = User.objects.create_user(
                email=email,
                password=None,
                full_name=full_name,
                avatar_url=avatar_url,
                google_id=google_id,
            )
            # get_or_create is safe if a post_save signal already created a Profile.
            Profile.objects.get_or_create(user=user)

        return Response({"user": UserSerializer(user).data, **_jwt_pair(user)})


# ---------------------------------------------------------------------------
# GET / PATCH /api/auth/me/
# ---------------------------------------------------------------------------
class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Current User",
        responses={200: UserSerializer}
    )
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(
        summary="Update Current User",
        description=(
            "PATCH-update the authenticated user and/or their profile. "
            "Accepts a flat payload; profile-level fields (summary/bio, "
            "linkedin, website, location, preferences, onboarding_complete) "
            "are automatically routed to the Profile model."
        ),
        request=inline_serializer(
            name='PatchMeRequest',
            fields={
                'full_name':          serializers.CharField(required=False),
                'summary':            serializers.CharField(required=False),
                'bio':                serializers.CharField(required=False),
                'headline':           serializers.CharField(required=False),
                'location':           serializers.CharField(required=False),
                'phone':              serializers.CharField(required=False),
                'linkedin':           serializers.CharField(required=False),
                'linkedin_url':       serializers.CharField(required=False),
                'website':            serializers.CharField(required=False),
                'portfolio_url':      serializers.CharField(required=False),
                'skills':             serializers.JSONField(required=False),
                'experience':         serializers.JSONField(required=False),
                'education':          serializers.JSONField(required=False),
                'preferences':        serializers.JSONField(required=False),
                'job_preferences':    serializers.JSONField(required=False),
                'onboarding_complete': serializers.BooleanField(required=False),
            }
        ),
        responses={200: UserSerializer}
    )
    def patch(self, request):
        data = request.data
        user = request.user

        # ── User-level fields ─────────────────────────────────────────────
        user_changed = False
        if 'full_name' in data:
            user.full_name = data['full_name']
            user_changed = True
        if user_changed:
            user.save(update_fields=['full_name'])

        # ── Profile-level fields ──────────────────────────────────────────
        try:
            profile = user.profile
        except Exception:
            profile = Profile.objects.create(user=user)

        profile_fields_changed = []

        # Alias: summary → bio
        bio_val = data.get('summary') or data.get('bio')
        if bio_val is not None:
            profile.bio = bio_val
            profile_fields_changed.append('bio')

        if 'headline' in data:
            profile.headline = data['headline']
            profile_fields_changed.append('headline')

        if 'location' in data:
            profile.location = data['location']
            profile_fields_changed.append('location')

        if 'phone' in data:
            profile.phone = data['phone']
            profile_fields_changed.append('phone')

        # Alias: linkedin → linkedin_url
        linkedin_val = data.get('linkedin') or data.get('linkedin_url')
        if linkedin_val is not None:
            profile.linkedin_url = linkedin_val
            profile_fields_changed.append('linkedin_url')

        # Alias: website → portfolio_url
        website_val = data.get('website') or data.get('portfolio_url')
        if website_val is not None:
            profile.portfolio_url = website_val
            profile_fields_changed.append('portfolio_url')

        if 'skills' in data:
            profile.skills = data['skills']
            profile_fields_changed.append('skills')

        if 'experience' in data:
            profile.experience = data['experience']
            profile_fields_changed.append('experience')

        if 'education' in data:
            profile.education = data['education']
            profile_fields_changed.append('education')

        # Alias: preferences → job_preferences
        prefs_val = data.get('preferences') or data.get('job_preferences')
        if prefs_val is not None:
            profile.job_preferences = prefs_val
            profile_fields_changed.append('job_preferences')

        # onboarding_complete flag — stored inside job_preferences JSON
        if 'onboarding_complete' in data:
            prefs = profile.job_preferences or {}
            prefs['onboarding_complete'] = bool(data['onboarding_complete'])
            profile.job_preferences = prefs
            if 'job_preferences' not in profile_fields_changed:
                profile_fields_changed.append('job_preferences')

        if profile_fields_changed:
            profile_fields_changed.append('updated_at')
            profile.save(update_fields=profile_fields_changed)

        return Response(UserSerializer(user).data)
