from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import JobListingViewSet, DocumentViewSet, ApplicationViewSet

router = DefaultRouter()
router.register(r"listings", JobListingViewSet, basename="job-listing")
router.register(r"documents", DocumentViewSet, basename="document")
router.register(r"applications", ApplicationViewSet, basename="application")

urlpatterns = [
    path("", include(router.urls)),
]
