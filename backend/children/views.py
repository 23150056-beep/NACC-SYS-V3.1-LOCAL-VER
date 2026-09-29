import time
from django.core.cache import cache
from django.db.models import Prefetch, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.display import display_name
from accounts.phone import as_typed as phone_as_typed
from accounts.models import Role
from accounts.permissions import (ChildRecordAccess,
                                  is_admin_or_assignee)
from accounts.scoping import role_of, scope_to_visible, visible_pre_assessments
from activity.models import ActivityLog
from activity.services import log_activity
from children import assignment, custodian, termination
from children.models import AssignmentRequest, Child, TerminationRecord
from children.serializers import ChildSerializer


# There used to be an _ArchivableViewSet above this, with one subclass and an
# `archive` action that set a child inactive. Nothing in the frontend ever
# called it - `terminate` is the path, and it demands a reason category and a
# note and writes a TerminationRecord. Worse, `archive` ran under
# RecordsAccess, whose write rule is Admin OR STAFF, so it was a
# staff-reachable way to end a case with none of that recorded - while both
# the terminate endpoint and the button in Children.jsx agree that staff do
# not end cases. A generalisation with one instance was not the problem; the
# second door was.
class ChildViewSet(viewsets.ModelViewSet):
    model = Child
    serializer_class = ChildSerializer
    permission_classes = [ChildRecordAccess]
    pagination_class = None
    # No DELETE, for the reason written above it. RecordsAccess's write rule is
    # Admin OR STAFF, so DELETE was the archive action all over again: a
    # staff-reachable way to end a case with none of it recorded - except worse,
    # because every FK to Child is on_delete=CASCADE. One 204 took the child,
    # their appointments, remarks, referrals, consents, reports and the
    # TerminationRecords meant to outlive the case. Nothing was logged.
    #
    # `terminate` is the path: it demands a reason category and a note, writes
    # a TerminationRecord and keeps the history. It is not reachable by staff,
    # which is the rule DELETE walked around. `reopen` undoes it and erases
    # nothing, and since 24 Sep 2026 staff may reopen (see `reopen`).
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_permissions(self):
        # Terminate/advance have their own rule (admin OR the child's assigned
        # psychologist), enforced in the action body - RecordsAccess would
        # block psychologists.
        if self.action in ("terminate", "advance_status", "presence", "reopen",
                           "closure_reasons"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def perform_create(self, serializer):
        # A record a social worker adds is theirs (accounts/scoping.py), and
        # nothing they send can make it someone else's. An administrator may
        # name a social worker, or leave it for later.
        #
        # The psychologist picked is ASKED, not assigned (children/
        # assignment.py): the record is saved with nobody, and the child joins
        # their records when they accept.
        asked = serializer.validated_data.pop("assigned_psychologist", None)
        if role_of(self.request) == Role.STAFF:
            obj = serializer.save(social_worker=self.request.user)
        else:
            obj = serializer.save()
        self._log(obj, ActivityLog.CREATED)
        if asked is not None:
            assignment.request_assignment(obj, asked, by=self.request.user)

    def perform_update(self, serializer):
        """`psychologist` in an edit says who the child SHOULD be with, and
        the server gets there by asking (children/assignment.py):

        - the psychologist already asked: nothing changes;
        - the one who holds the child: any open request is withdrawn;
        - nobody ("Leave unassigned"): the request is withdrawn and the
          assignment cleared, as it always was - no one needs asking to stop;
        - anyone else: they are asked, and the child stays where it is until
          they accept. The carry-history choice travels with the request.

        A psychologist cannot change it at all (the serializer refuses), so
        what a psychologist's edit resends is left alone.
        """
        data = serializer.validated_data
        instance = serializer.instance
        if (role_of(self.request) not in (Role.ADMINISTRATOR, Role.STAFF)
                or "assigned_psychologist" not in data):
            obj = serializer.save()
            self._log(obj, ActivityLog.UPDATED)
            return
        wanted = data.pop("assigned_psychologist")
        asking = wanted is not None and wanted.pk != instance.assigned_psychologist_id
        # Popped only when asking: applied now, it would change what the
        # CURRENT psychologist sees before anybody has agreed to anything.
        # Absent means unchanged (request_assignment), not "carry it".
        carry = data.pop("assignee_sees_history", None) if asking else None
        if wanted is None:
            data["assigned_psychologist"] = None
        obj = serializer.save()
        self._log(obj, ActivityLog.UPDATED)
        if wanted is None:
            assignment.withdraw_pending(obj, by=self.request.user)
        else:
            assignment.request_assignment(obj, wanted, by=self.request.user,
                                          carry_history=carry)

    def get_queryset(self):
        # Inactive (terminated) cases stay reachable by id - the profile view
        # shows the termination details, and terminate itself must be able to
        # report "already inactive" rather than 404. Reopen also needs access
        # to inactive children by id, and so does presence: the drawer of an
        # archived record is where it is reopened from, and two people there
        # should see each other, not a silent 404 every ten seconds.
        qs = Child.objects.all().order_by("fullname")
        if self.action not in ("retrieve", "terminate", "reopen", "presence"):
            # The parameter is still called include_archived because the
            # frontend sends that name; the state it means is INACTIVE.
            if self.request.query_params.get("include_archived") != "true":
                qs = qs.exclude(status=Child.INACTIVE)
        # consents feed the derived pre_assessment_status (No Consent Yet, …).
        # case_referrals joins the prefetch so has_case_referral costs one
        # query for the page rather than one per child — the list returns
        # the whole caseload on several screens.
        # Pre-assessments under the carry-history control, because the
        # status and the instruments used are worked out from them.
        # assignment_requests for the Awaiting / Declined chips on the same
        # rows (ChildSerializer.pending_assignment).
        qs = qs.prefetch_related(
            visible_pre_assessments(self.request), "terminations", "consents",
            "case_referrals",
            Prefetch("assignment_requests",
                     queryset=AssignmentRequest.objects.select_related(
                         "psychologist", "requested_by")))
        # psychologist_name is rendered on every row, so without this the list
        # costs an extra query per child: 47 for 40 children, against 7 with
        # it. The guardian join went with guardian_name — nothing reads it.
        qs = qs.select_related("assigned_psychologist", "social_worker",
                               "custodian_sms_consent_by")
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
                "name": display_name(request.user),
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
        if not is_admin_or_assignee(request, child):
            return Response({"detail": "Only the assigned psychologist or an administrator can update the case status."},
                            status=status.HTTP_403_FORBIDDEN)
        if child.status == Child.INACTIVE:
            return Response({"detail": "This case is terminated; staff or an administrator can reopen it from the child's record."},
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
        if not is_admin_or_assignee(request, child):
            return Response({"detail": "Only the assigned psychologist or an administrator can terminate this case."},
                            status=status.HTTP_403_FORBIDDEN)
        if child.status == Child.INACTIVE:
            return Response({"detail": "This case is already inactive."},
                            status=status.HTTP_400_BAD_REQUEST)
        reason = request.data.get("reason_category", "")
        note = (request.data.get("note") or "").strip()
        # The ISA's list, or the psychologist's - and a psychologist's reason
        # only when the record bears it out (children/termination.py).
        refused = termination.refusal(role_of(request), child, reason)
        if refused:
            return Response({"reason_category": refused},
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
        # A closed case has nobody left to ask.
        assignment.withdraw_pending(child, by=request.user)
        return Response({
            "status": "inactive",
            "termination": {
                "date": record.date, "reason_category": record.reason_category,
                "note": record.note,
            },
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="closure-reasons")
    def closure_reasons(self, request, pk=None):
        """The reasons the terminate dialog offers this person for this child,
        and - for a psychologist - what the record shows, so each reason can
        say why it is or is not open. The terminate endpoint refuses by the
        same function (children/termination.py)."""
        child = self.get_object()
        if not is_admin_or_assignee(request, child):
            return Response({"detail": "Only the assigned psychologist or an administrator can terminate this case."},
                            status=status.HTTP_403_FORBIDDEN)
        reasons, facts = termination.reasons_for(role_of(request), child)
        return Response({"reasons": reasons, "facts": facts})

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        """Staff or an administrator: a terminated child returned to the
        clinic. Reactivate the case on top of the archived record — history is
        retained, but the psychologist assignment is cleared: a reopened case
        returns to the pool for staff/admin to assign fresh.

        Administrator-only until 24 Sep 2026, when the owner opened it to
        staff: they run intake, a returning child arrives at intake, and
        reopening restores rather than erases. Terminating stays with the
        assigned psychologist or an administrator."""
        child = self.get_object()
        role = role_of(request)
        if role not in (Role.ADMINISTRATOR, Role.STAFF):
            return Response({"detail": "Only staff or an administrator can reopen a terminated case."},
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
        duplicating' warning on the Add Record form.

        It searches every record, not only the caller's: a child held by
        another social worker must not get a second record. Such a match says
        that it exists and who holds it - the owner's decision, 24 Sep 2026 -
        and nothing from the record itself: no id to open, no birth date, no
        psychologist. The searcher typed the name; the holder is who to ask.
        """
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
        # select_related: every row below renders the psychologist's name, and
        # without the join that is an extra query per match - on an endpoint the
        # intake form calls while somebody is still typing a name.
        matches = (Child.objects.filter(q)
                   .select_related("assigned_psychologist", "social_worker")
                   .order_by("-updated_at")[:5])
        mine = set(scope_to_visible(Child.objects.filter(pk__in=[c.pk for c in matches]),
                                    request, path=None).values_list("pk", flat=True))

        def row(c):
            if c.pk in mine:
                return {"id": c.id, "fullname": c.fullname, "status": c.status,
                        "birth_date": c.birth_date,
                        "psychologist_name": display_name(c.assigned_psychologist) or None,
                        "social_worker_name": display_name(c.social_worker) or None,
                        "yours": True}
            return {"yours": False,
                    "held_by": display_name(c.social_worker) or None}
        return Response({"matches": [row(c) for c in matches]})


class CustodianContactCodeView(APIView):
    """The one-time code that confirms a custodian's number (children/custodian.py).

    POST {number}: text a code to it. PUT {number, code}: check what the
    custodian read back. Confirming changes no record by itself - the record
    form's save puts the confirmed number on the child, and only for whoever
    confirmed it, within the hour.

    The ISA and social workers only: they are the ones with the custodian in
    front of them, and every code is a paid text to a number somebody typed.
    """
    permission_classes = [IsAuthenticated]

    def _refuse(self, request):
        if role_of(request) not in (Role.ADMINISTRATOR, Role.STAFF):
            return Response({"detail": "Only the social worker or the ISA confirms a custodian's number."},
                            status=status.HTTP_403_FORBIDDEN)
        return None

    def _number(self, request):
        try:
            number = custodian.normalise(request.data.get("number"))
        except ValueError as exc:
            return None, Response({"number": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not number:
            return None, Response({"number": "Enter the custodian's mobile number."},
                                  status=status.HTTP_400_BAD_REQUEST)
        return number, None

    def post(self, request):
        refused = self._refuse(request)
        if refused:
            return refused
        number, bad = self._number(request)
        if bad:
            return bad
        try:
            result = custodian.send_code(request.user, number)
        except custodian.TooManyCodes as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not result.ok:
            # The gateway's own words: a code that never arrives with no
            # explanation is the failure that costs an afternoon.
            return Response({"detail": result.detail}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"detail": f"A code was sent to {phone_as_typed(number)}. "
                                   f"Ask the custodian to read it back. It expires in 10 minutes.",
                         "number": number})

    def put(self, request):
        refused = self._refuse(request)
        if refused:
            return refused
        number, bad = self._number(request)
        if bad:
            return bad
        ok, message = custodian.confirm_code(request.user, number, request.data.get("code"))
        if not ok:
            return Response({"code": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": message, "number": number, "verified": True})
