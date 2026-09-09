from django.urls import include, path
from rest_framework.routers import DefaultRouter

from adoption.views import (
    AdmitView, AdoptionCaseViewSet, BoardView, PAPViewSet, RequirementViewSet,
)

router = DefaultRouter()
router.register("cases", AdoptionCaseViewSet, basename="adoption-case")
router.register("requirements", RequirementViewSet, basename="adoption-requirement")
router.register("paps", PAPViewSet, basename="adoption-pap")

urlpatterns = [
    # One request for the whole screen — see the note on BoardView.
    path("board/", BoardView.as_view(), name="adoption-board"),
    path("admit/", AdmitView.as_view(), name="adoption-admit"),
    path("", include(router.urls)),
]
