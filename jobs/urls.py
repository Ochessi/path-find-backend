from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    JobListingViewSet,
    DocumentViewSet,
    ApplicationViewSet,
    ResumeParseView,
    CuratedFeedView,
    EmbeddingStatusView,
)

router = DefaultRouter()
router.register(r"listings", JobListingViewSet, basename="job-listing")
router.register(r"documents", DocumentViewSet, basename="document")
router.register(r"applications", ApplicationViewSet, basename="application")

urlpatterns = [
    path("", include(router.urls)),
    # Resume parsing pipeline
    path("resume/parse/", ResumeParseView.as_view(), name="resume-parse"),
    # Semantic curated feed — AI-ranked job recommendations
    path("feed/", CuratedFeedView.as_view(), name="curated-feed"),
    # Admin: embedding pipeline health-check & manual backfill trigger
    path("embedding-status/", EmbeddingStatusView.as_view(), name="embedding-status"),
]
