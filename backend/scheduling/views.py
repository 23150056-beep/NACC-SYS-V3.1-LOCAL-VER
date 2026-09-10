from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Role
from accounts.scoping import role_of as _role
from activity.models import ActivityLog
from activity.services import log_activity
from children.models import Child
from scheduling import booking
from scheduling.availability import free_windows
from scheduling.models import AvailabilityBlock, Appointment
from scheduling.serializers import AvailabilityBlockSerializer, AppointmentSerializer




class AvailabilityBlockViewSet(viewsets.ModelViewSet):
    """Psychologists manage their own availability; admin manages all;
    staff read (to book against)."""
    permission_classes = [IsAuthenticated]
    pagination_class = None
    serializer_class = AvailabilityBlockSerializer

    def get_queryset(self):
        qs = AvailabilityBlock.objects.select_related("psychologist")
        if self.request.query_params.get("include_inactive") != "true":
            qs = qs.filter(active=True)
        psy = self.request.query_params.get("psychologist")
        if psy and str(psy).isdigit():
            qs = qs.filter(psychologist_id=psy)
        return qs

    def _assert_can_write(self, psychologist_id):
        role = _role(self.request)
        if role == Role.ADMINISTRATOR:
            return
        if role == Role.PSYCHOLOGIST and psychologist_id == self.request.user.id:
            return
        raise PermissionDenied("You can only manage your own availability.")

    def perform_create(self, serializer):
        role = _role(self.request)
        if role == Role.PSYCHOLOGIST:
            serializer.save(psychologist=self.request.user)
        else:
            psy = serializer.validated_data.get("psychologist")
            if psy is None:
                raise ValidationError({"psychologist": "Select the psychologist."})
            self._assert_can_write(psy.id)
            serializer.save()

    def perform_update(self, serializer):
        self._assert_can_write(serializer.instance.psychologist_id)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_can_write(instance.psychologist_id)
        instance.delete()

    @action(detail=False, methods=["get"], url_path="slots")
    def slots(self, request):
        """The start times somebody can actually pick, for one psychologist
        on one day.

        The booking form used to be two blank boxes and a row of chips showing
        the START of each window, so everybody clicked 09:00 and the second
        person to try was told it was taken. This answers the question the
        form is really asking.

        `reason` matters as much as `slots`: "they do not work Wednesdays",
        "the day is full" and "a 3-hour session does not fit" are three
        different next actions, and an empty list renders all three as a blank
        panel that looks broken.
        """
        psy_id = request.query_params.get("psychologist")
        if not (psy_id or "").isdigit():
            return Response({"detail": "Which psychologist?"}, status=400)
        psy_id = int(psy_id)
        # A psychologist sees their own day and nobody else's, and an attempt
        # on a colleague 404s rather than 403s - the same "hidden, not
        # disclosed" convention next_slots and the clinical viewsets use.
        if _role(request) == Role.PSYCHOLOGIST and psy_id != request.user.id:
            return Response({"detail": "Not found."}, status=404)
        psych = get_user_model().objects.filter(pk=psy_id).first()
        if psych is None:
            return Response({"detail": "Not found."}, status=404)

        try:
            day = date.fromisoformat(request.query_params.get("date", ""))
        except ValueError:
            return Response({"detail": "A date in YYYY-MM-DD, please."}, status=400)

        child = None
        child_id = request.query_params.get("child")
        if child_id and str(child_id).isdigit():
            child = Child.objects.filter(pk=child_id).first()
        try:
            duration = int(request.query_params.get("duration") or 60)
        except ValueError:
            duration = 60
        duration = max(15, min(duration, 8 * 60))

        # `exclude` is the appointment being moved, so its own time still shows
        # on the grid offering to move it.
        exclude = request.query_params.get("exclude")
        exclude = int(exclude) if (exclude or "").isdigit() else None
        found = booking.bookable_slots(psych, child, day,
                                       duration_minutes=duration,
                                       exclude_id=exclude)
        return Response({
            "psychologist": getattr(psych, "fullname", "") or psych.get_username(),
            "date": day.isoformat(),
            "duration": duration,
            "slots": found,
            "reason": "" if found else booking.why_empty(psych, day, duration),
        })

    @action(detail=False, methods=["get"], url_path="next-slots")
    def next_slots(self, request):
        """Upcoming bookable windows for a child's assigned psychologist —
        staff/psychologist see at a glance when the child can be counseled.
        Capacity counting mirrors AppointmentViewSet._validate_booking: every
        non-cancelled appointment inside the block's time window occupies a
        slot, so this never contradicts what the booking endpoint accepts."""
        child_id = request.query_params.get("child")
        try:
            child = Child.objects.get(pk=child_id)
        except (Child.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Unknown child."}, status=400)
        # Admin/Staff may query any child, unrestricted. A psychologist may
        # only query a child assigned to them - matching ChildViewSet's
        # get_queryset() scoping. Access outside that scope 404s rather than
        # 403ing, the same "hidden, not disclosed" convention used elsewhere
        # for a psychologist's access to a child outside their assignment.
        if (_role(request) == Role.PSYCHOLOGIST
                and child.assigned_psychologist_id != request.user.id):
            return Response({"detail": "Not found."}, status=404)
        psych = child.assigned_psychologist
        if psych is None:
            return Response({"detail": "This child has no assigned psychologist yet."}, status=400)
        # The capacity arithmetic lives in scheduling.availability, so this
        # endpoint, the booking validator and the chatbot cannot drift apart
        # about whether a slot exists.
        today = timezone.localdate()
        slots = free_windows(psych, today, today + timedelta(days=14))
        return Response({
            "psychologist": getattr(psych, "fullname", "") or psych.get_username(),
            "slots": slots[:6],
        })


class AppointmentViewSet(viewsets.ModelViewSet):
    """Calendar appointments. Staff/admin book against a psychologist's
    availability; a psychologist may book freely on their own schedule.
    Status transitions via actions (completed / no_show / cancelled)."""
    permission_classes = [IsAuthenticated]
    pagination_class = None
    serializer_class = AppointmentSerializer

    def get_queryset(self):
        qs = Appointment.objects.select_related("child", "psychologist", "booked_by")
        role = _role(self.request)
        if role == Role.PSYCHOLOGIST:
            qs = qs.filter(psychologist=self.request.user)
        frm = self.request.query_params.get("from")
        to = self.request.query_params.get("to")
        if frm:
            qs = qs.filter(start__date__gte=frm)
        if to:
            qs = qs.filter(start__date__lte=to)
        child = self.request.query_params.get("child")
        if child and str(child).isdigit():
            qs = qs.filter(child_id=child)
        return qs

    def _validate_booking(self, psychologist, child, start, duration_minutes,
                          exclude_id=None):
        """Every rule about whether this booking can exist - see booking.py.

        A psychologist working outside their own posted window is their call,
        which is what own_calendar waives. It never waives the overlap rules:
        the availability window is a preference and being in one place at a
        time is not.
        """
        own = (_role(self.request) == Role.PSYCHOLOGIST
               and psychologist.id == self.request.user.id)
        errors = booking.errors_for(
            psychologist, child, start, duration_minutes,
            own_calendar=own, exclude_id=exclude_id)
        if errors:
            raise ValidationError(errors)

    def perform_create(self, serializer):
        role = _role(self.request)
        if role == Role.PSYCHOLOGIST:
            psychologist = self.request.user
        else:
            psychologist = serializer.validated_data.get("psychologist")
            if psychologist is None:
                raise ValidationError({"psychologist": "Select the psychologist."})
        self._validate_booking(psychologist,
                               serializer.validated_data.get("child"),
                               serializer.validated_data["start"],
                               serializer.validated_data.get("duration_minutes", 60))
        obj = serializer.save(psychologist=psychologist, booked_by=self.request.user)
        log_activity(self.request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="Appointment", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.psychologist)

    def perform_update(self, serializer):
        """Moving an appointment is booking it again, and checked as such.

        This method did not exist, so every rule above was reachable simply by
        creating a valid appointment and then PATCHing it somewhere else.
        """
        instance = serializer.instance
        data = serializer.validated_data
        psychologist = data.get("psychologist") or instance.psychologist
        if _role(self.request) == Role.PSYCHOLOGIST:
            psychologist = instance.psychologist
        self._validate_booking(
            psychologist,
            data.get("child", instance.child),
            data.get("start", instance.start),
            data.get("duration_minutes", instance.duration_minutes),
            exclude_id=instance.pk,
        )
        obj = serializer.save(psychologist=psychologist)
        log_activity(self.request.user, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="Appointment", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.psychologist)

    def _set_status(self, request, pk, new_status):
        obj = self.get_object()
        role = _role(request)
        allowed = role == Role.ADMINISTRATOR or obj.psychologist_id == request.user.id \
            or (role == Role.STAFF and new_status == Appointment.CANCELLED)
        if not allowed:
            return Response({"detail": "You cannot update this appointment."},
                            status=status.HTTP_403_FORBIDDEN)
        # Cancelling stays available at any time - a session called off on the
        # day, or a no-show written up late, are both normal. Recording an
        # OUTCOME is different: it is a claim about something that happened.
        if new_status in (Appointment.COMPLETED, Appointment.NO_SHOW):
            if obj.status == Appointment.CANCELLED:
                return Response(
                    {"detail": "This appointment was cancelled. Book a new one rather "
                               "than recording an outcome against it."},
                    status=status.HTTP_400_BAD_REQUEST)
            if obj.start > timezone.now():
                return Response(
                    {"detail": "This appointment has not happened yet."},
                    status=status.HTTP_400_BAD_REQUEST)
        obj.status = new_status
        obj.save(update_fields=["status", "updated_at"])
        log_activity(request.user, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="Appointment", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.psychologist)
        return Response(AppointmentSerializer(obj).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._set_status(request, pk, Appointment.COMPLETED)

    @action(detail=True, methods=["post"])
    def no_show(self, request, pk=None):
        return self._set_status(request, pk, Appointment.NO_SHOW)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._set_status(request, pk, Appointment.CANCELLED)
