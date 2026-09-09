from django.contrib import admin
from django.urls import path, include

from config.health import healthz

urlpatterns = [
    # Unauthenticated liveness/readiness probe for the cloud platform.
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("api/", include("accounts.urls")),
    path("api/", include("children.urls")),
    path("api/locations/", include("locations.urls")),
    path("api/", include("activity.urls")),
    path("api/", include("clinical.urls")),
    path("api/", include("scheduling.urls")),
    path("api/", include("samd.urls")),
    path("api/", include("assistant.urls")),
    path("api/adoption/", include("adoption.urls")),
]
