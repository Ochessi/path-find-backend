from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    JobListingViewSet,
    DocumentViewSet,
    ApplicationViewSet,
    ResumeParseView,
    CuratedFeedView,
    EmbeddingStatusView,
    ApplicationAIGenerateView,
    TaskStatusView,
    FetchJobsView,
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
    # AI Content Generation for applications
    path("applications/generate/", ApplicationAIGenerateView.as_view(), name="application-generate"),
    # Task Status checking
    path("tasks/<str:task_id>/", TaskStatusView.as_view(), name="task-status"),
    # Manual Job Fetching
    path("fetch/", FetchJobsView.as_view(), name="fetch-jobs"),
]
