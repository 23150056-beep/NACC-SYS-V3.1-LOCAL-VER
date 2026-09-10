from django.urls import path
from rest_framework.routers import DefaultRouter

from scheduling.tasks_views import SessionReminderTaskView
from scheduling.views import (
    AvailabilityBlockViewSet, AppointmentViewSet, UnavailabilityViewSet,
)

router = DefaultRouter()
router.register("availability", AvailabilityBlockViewSet, basename="availability")
router.register("appointments", AppointmentViewSet, basename="appointment")
router.register("unavailability", UnavailabilityViewSet, basename="unavailability")

urlpatterns = router.urls + [
    # Called by whatever runs on a schedule, not by the app. Guarded by a
    # shared token and 404s until one is configured — see the view.
    path("tasks/session-reminders/", SessionReminderTaskView.as_view(),
         name="task-session-reminders"),
]
