from rest_framework.routers import DefaultRouter
from children.assignment_api import AssignmentRequestViewSet
from children.views import ChildViewSet

router = DefaultRouter()
router.register("children", ChildViewSet, basename="child")
router.register("assignment-requests", AssignmentRequestViewSet,
                basename="assignment-request")

urlpatterns = router.urls
