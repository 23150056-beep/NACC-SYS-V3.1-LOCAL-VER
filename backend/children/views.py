import time
from django.core.cache import cache
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from accounts.models import Role
from accounts.permissions import RecordsAccess, ChildRecordAccess
from accounts.scoping import role_of, scope_to_visible
from activity.models import ActivityLog
from activity.services import log_activity
from children.models import Child, TerminationRecord
from children.notifications import send_assignment_notification
from accounts.sms_notifications import notify_new_assignment
from children.serializers import ChildSerializer


class _ArchivableViewSet(viewsets.ModelViewSet):
    permission_classes = [RecordsAccess]
    pagination_class = None
    model = None

    def get_queryset(self):
        qs = self.model.objects.all().order_by("fullname")
        if self.request.query_params.get("include_archived") != "true":
            qs = qs.exclude(status=self.model.ARCHIVED)
        return qs

    def _log(self, obj, action_name):
        log_activity(
            self.request.user, action_name, ActivityLog.RECORD,
            entity_type=self.model.__name__,
            entity_label=getattr(obj, "fullname", ""),
            entity_id=obj.id)

    def perform_create(self, serializer):
        obj = serializer.save()
        self._log(obj, ActivityLog.CREATED)

    def perform_update(self, serializer):
        obj = serializer.save()
        self._log(obj, ActivityLog.UPDATED)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        obj = self.get_object()
        obj.status = self.model.ARCHIVED
        obj.save(update_fields=["status", "updated_at"])
        self._log(obj, ActivityLog.ARCHIVED)
        return Response({"status": "archived"}, status=status.HTTP_200_OK)


class ChildViewSet(_ArchivableViewSet):
    model = Child
    serializer_class = ChildSerializer
    permission_classes = [ChildRecordAccess]

    def get_permissions(self):
        # Terminate/advance have their own rule (admin OR the child's assigned
        # psychologist), enforced in the action body - RecordsAccess would
        # block psychologists.
        if self.action in ("terminate", "advance_status", "presence", "reopen"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def perform_create(self, serializer):
        obj = serializer.save()
        self._log(obj, ActivityLog.CREATED)
        if getattr(obj, "assigned_psychologist", None) is not None:
            send_assignment_notification(obj)
            notify_new_assignment(obj)

    def perform_update(self, serializer):
        # Read the old assignee before save() overwrites it: the email is for a
        # *change* of psychologist, not for every edit to an assigned case.
        old = serializer.instance.assigned_psychologist_id if serializer.instance else None
        obj = serializer.save()
        self._log(obj, ActivityLog.UPDATED)
        if obj.assigned_psychologist_id and obj.assigned_psychologist_id != old:
            send_assignment_notification(obj)
            notify_new_assignment(obj)

    def get_queryset(self):
        # Inactive (terminated) cases stay reachable by id - the profile view
        # shows the termination details, and terminate itself must be able to
        # report "already inactive" rather than 404. Reopen also needs access
        # to inactive children by id.
        if self.action in ("retrieve", "terminate", "reopen"):
            qs = self.model.objects.all().order_by("fullname")
        else:
            qs = super().get_queryset()
        # consents feed the derived pre_assessment_status (No Consent Yet, …).
        qs = qs.prefetch_related("pre_assessments__instruments", "terminations", "consents")
        # psychologist_name is rendered on every row, so without this the list
        # costs an extra query per child: 47 for 40 children, against 7 with
        # it. The guardian join went with guardian_name — nothing reads it.
        qs = qs.select_related("assigned_psychologist")
        return scope_to_visible(qs, self.request, path=None)

    def update(self, request, *args, **kwargs):
        expected = request.data.get("expected_updated_at")
        if expected:
            instance = self.get_object()
            # Serialize the current instance to get the updated_at in the same format as the client sees it
            serialized = self.get_serializer(instance).data
            actual = serialized.get("updated_at")
            if actual != expected:
                return Response(
                    {"detail": "This record was updated by someone else while you were editing.",
                     "current": serialized},
                    status=status.HTTP_409_CONFLICT)
        return super().update(request, *args, **kwargs)

    PRESENCE_TTL = 30  # seconds a heartbeat stays visible

    @action(detail=True, methods=["get", "post"])
    def presence(self, request, pk=None):
        child = self.get_object()
        key = f"child-presence:{child.id}"
        now = time.time()
        entries = {k: v for k, v in (cache.get(key) or {}).items()
                   if now - v["ts"] < self.PRESENCE_TTL}
        if request.method == "POST":
            entries[str(request.user.id)] = {
                "name": getattr(request.user, "fullname", "") or request.user.get_username(),
                "role": role_of(request) or "",
                "ts": now,
            }
            cache.set(key, entries, self.PRESENCE_TTL * 2)
        others = [{"name": v["name"], "role": v["role"]}
                  for k, v in entries.items() if k != str(request.user.id)]
        return Response({"others": others})

    def _log(self, obj, action_name):
        # Direct child-record notifications at the child's assigned psychologist.
        log_activity(
            self.request.user, action_name, ActivityLog.RECORD,
            entity_type="Child", entity_label=getattr(obj, "fullname", ""),
            entity_id=obj.id, recipient=obj.assigned_psychologist)

    @action(detail=True, methods=["post"], url_path="advance-status")
    def advance_status(self, request, pk=None):
        """Move the case tracker between pre_assessment and counseling.
        Terminated is only reachable through the terminate action."""
        child = self.get_object()
        role = role_of(request)
        allowed = (role == Role.ADMINISTRATOR) or (
            role == Role.PSYCHOLOGIST and child.assigned_psychologist_id == request.user.id)
        if not allowed:
            return Response({"detail": "Only the assigned psychologist or an administrator can update the case status."},
                            status=status.HTTP_403_FORBIDDEN)
        if child.status == Child.INACTIVE:
            return Response({"detail": "This case is terminated; an administrator can reopen it from the child's record."},
                            status=status.HTTP_400_BAD_REQUEST)
        new_status = request.data.get("case_status")
        if new_status not in (Child.STAGE_PRE_ASSESSMENT, Child.STAGE_COUNSELING):
            return Response({"case_status": "Choose pre_assessment or counseling."},
                            status=status.HTTP_400_BAD_REQUEST)
        child.case_status = new_status
        child.save(update_fields=["case_status", "updated_at"])
        self._log(child, ActivityLog.UPDATED)
        return Response({"case_status": child.case_status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def terminate(self, request, pk=None):
        """Archive a case with a required reason (V2). Sets the child inactive
        and writes a TerminationRecord. Admin or assigned psychologist only."""
        child = self.get_object()
        role = role_of(request)
        allowed = (role == Role.ADMINISTRATOR) or (
            role == Role.PSYCHOLOGIST and child.assigned_psychologist_id == request.user.id)
        if not allowed:
            return Response({"detail": "Only the assigned psychologist or an administrator can terminate this case."},
                            status=status.HTTP_403_FORBIDDEN)
        if child.status == Child.INACTIVE:
            return Response({"detail": "This case is already inactive."},
                            status=status.HTTP_400_BAD_REQUEST)
        reason = request.data.get("reason_category", "")
        note = (request.data.get("note") or "").strip()
        valid_reasons = {c[0] for c in TerminationRecord.REASON_CHOICES}
        if reason not in valid_reasons:
            return Response({"reason_category": "Select a termination reason."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not note:
            return Response({"note": "A reason note is required to terminate a case."},
                            status=status.HTTP_400_BAD_REQUEST)
        record = TerminationRecord.objects.create(
            child=child, terminated_by=request.user,
            reason_category=reason, note=note)
        child.status = Child.INACTIVE
        child.case_status = Child.STAGE_TERMINATED
        child.save(update_fields=["status", "case_status", "updated_at"])
        self._log(child, ActivityLog.ARCHIVED)
        return Response({
            "status": "inactive",
            "termination": {
                "date": record.date, "reason_category": record.reason_category,
                "note": record.note,
            },
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        """Admin-only: a terminated child returned to the clinic. Reactivate
        the case on top of the archived record — history is retained, but the
        psychologist assignment is cleared: a reopened case returns to the
        pool for staff/admin to assign fresh."""
        child = self.get_object()
        role = role_of(request)
        if role != Role.ADMINISTRATOR:
            return Response({"detail": "Only an administrator can reopen a terminated case."},
                            status=status.HTTP_403_FORBIDDEN)
        if child.status != Child.INACTIVE:
            return Response({"detail": "This case is already active."},
                            status=status.HTTP_400_BAD_REQUEST)
        child.status = Child.ACTIVE
        child.case_status = Child.STAGE_PRE_ASSESSMENT
        child.assigned_psychologist = None
        child.save(update_fields=["status", "case_status", "assigned_psychologist", "updated_at"])
        self._log(child, ActivityLog.UPDATED)
        return Response({"status": child.status, "case_status": child.case_status})

    @action(detail=False, methods=["get"], url_path="check-duplicate")
    def check_duplicate(self, request):
        """Intake helper: does a record (active OR archived) already exist for
        this child? Staff/Admin only — powers the 'reopen instead of
        duplicating' warning on the Add Record form."""
        role = role_of(request)
        if role not in (Role.ADMINISTRATOR, Role.STAFF):
            return Response({"detail": "Staff or administrators only."},
                            status=status.HTTP_403_FORBIDDEN)
        first = (request.query_params.get("first_name") or "").strip()
        last = (request.query_params.get("last_name") or "").strip()
        birth = (request.query_params.get("birth_date") or "").strip()
        if not last:
            return Response({"matches": []})
        q = Q(last_name__iexact=last) if not first else \
            Q(last_name__iexact=last, first_name__iexact=first)
        if birth and not first:
            q &= Q(birth_date=birth)
        elif not first:
            return Response({"matches": []})  # last name alone is too broad
        matches = Child.objects.filter(q).order_by("-updated_at")[:5]
        return Response({"matches": [{
            "id": c.id, "fullname": c.fullname, "status": c.status,
            "birth_date": c.birth_date,
            "psychologist_name": getattr(c.assigned_psychologist, "fullname", None),
        } for c in matches]})
