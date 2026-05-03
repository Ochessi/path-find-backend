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
from .serializers import RegisterSerializer, UserSerializer

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

        # Explicitly create an empty Profile for every new email/password user.
        Profile.objects.create(user=user)

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
            # Explicitly create an empty Profile for every new Google OAuth user.
            Profile.objects.create(user=user)

        return Response({"user": UserSerializer(user).data, **_jwt_pair(user)})


# ---------------------------------------------------------------------------
# GET /api/auth/me/
# ---------------------------------------------------------------------------
class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Current User",
        responses={200: UserSerializer}
    )
    def get(self, request):
        return Response(UserSerializer(request.user).data)
