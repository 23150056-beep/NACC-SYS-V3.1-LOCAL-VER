from rest_framework.routers import DefaultRouter
from children.assignment_api import AssignmentRequestViewSet
from django.urls import path
from children.views import ChildViewSet, CustodianContactCodeView

router = DefaultRouter()
router.register("children", ChildViewSet, basename="child")
router.register("assignment-requests", AssignmentRequestViewSet,
                basename="assignment-request")

urlpatterns = [
    path("custodian-contact/code/", CustodianContactCodeView.as_view(),
         name="custodian-contact-code"),
] + router.urls
