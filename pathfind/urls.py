import os
from django.contrib import admin
from django.urls import path, include

from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

admin_url = os.getenv("ADMIN_URL", "admin/")
if not admin_url.endswith("/"):
    admin_url += "/"

from django.http import HttpResponse

def health_check(request):
    return HttpResponse("OK")

urlpatterns = [
    path("", health_check, name="health_check"),
    path(admin_url, admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/jobs/", include("jobs.urls")),
    
    # OpenAPI Schema and Swagger UI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
