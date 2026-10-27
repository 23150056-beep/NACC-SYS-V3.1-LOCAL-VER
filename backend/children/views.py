import time
from datetime import date
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Prefetch
from django.http import Http404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
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
from children import assignment, custodian, duplicates, termination, transfer
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
    # The throttle scope, set per action where one is wanted (`exported`). The
    # attribute has to exist here for an action to be allowed to name one.
    throttle_scope = None
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
                           "closure_reasons", "remove_duplicate", "exported", "transfer_records"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        """Add a record, once per submission (children/duplicates.py).

        The form sends the same token with every attempt to save, so a second
        attempt - a double click that got past the button, or a retry after a
        response that never arrived - is told the record was already saved,
        with its id when the requester may open it, rather than adding the
        child again. The token is looked up before anything is validated: the
        first attempt may have changed what the second would be checked
        against."""
        token = duplicates.token_in(request.data)
        if token:
            again = duplicates.already_saved(request, token)
            if again is not None:
                return again
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            # Two attempts arrived together: both found no record for the
            # token, and the unique column let one of them in. The loser gets
            # the same answer as a later retry. Anything else is not ours.
            again = duplicates.already_saved(request, token) if token else None
            if again is None:
                raise
            return again

    def perform_create(self, serializer):
        # A record a social worker adds is theirs (accounts/scoping.py), and
        # nothing they send can make it someone else's. An administrator may
        # name a social worker, or leave it for later.
        #
        # The psychologist picked is ASKED, not assigned (children/
        # assignment.py): the record is saved with nobody, and the child joins
        # their records when they accept.
        asked = serializer.validated_data.pop("assigned_psychologist", None)
        # One record per child, whoever adds it. A new record for a child who
        # already has one is refused here and told where the first is; only
        # when the name parts and the birth date are all given - the
        # fullname-only door has neither (children/duplicates.py).
        data = serializer.validated_data
        refused = duplicates.refusal_for_second_record(
            self.request, data.get("first_name"), data.get("last_name"),
            data.get("birth_date"))
        if refused:
            raise ValidationError({"detail": refused})
        if role_of(self.request) == Role.STAFF:
            obj = duplicates.save_new(serializer, social_worker=self.request.user)
        else:
            obj = duplicates.save_new(serializer)
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
        # The carry-history choice belongs to a request and is applied when it
        # is accepted - never by an edit. Saved here it changed what the
        # CURRENT psychologist sees with nobody asked: pick someone else,
        # untick it, pick the holder back, and the holder lost the history.
        # Absent means unchanged (request_assignment), not "carry it".
        carry = data.pop("assignee_sees_history", None)
        if (role_of(self.request) not in (Role.ADMINISTRATOR, Role.STAFF)
                or "assigned_psychologist" not in data):
            obj = serializer.save()
            self._log(obj, ActivityLog.UPDATED)
            return
        wanted = data.pop("assigned_psychologist")
        asking = wanted is not None and wanted.pk != instance.assigned_psychologist_id
        if not asking:
            carry = None
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
        assigned psychologist or an administrator.

        Since 30 Sep 2026 a social worker may also reopen a terminated case
        held by another worker, or by nobody, and it becomes theirs - but only
        one found at intake: the request carries the name typed into Add
        Record (first_name, last_name AND birth_date), matched by the rule the
        save refusal uses (children/duplicates.py same_child). By id alone it stays a 404, so no
        worker can walk the ids and collect other workers' closed cases. An
        ACTIVE case held by someone else still goes to the ISA."""
        role = role_of(request)
        taking_over, previous = False, None
        try:
            child = self.get_object()
        except Http404:
            child = (Child.objects.filter(pk=pk, status=Child.INACTIVE)
                     .select_related("social_worker").first() if role == Role.STAFF else None)
            if child is None or not _is_same_child(
                    child, request.data.get("first_name"), request.data.get("last_name"),
                    request.data.get("birth_date")):
                raise
            taking_over, previous = True, child.social_worker
        if role not in (Role.ADMINISTRATOR, Role.STAFF):
            return Response({"detail": "Only staff or an administrator can reopen a terminated case."},
                            status=status.HTTP_403_FORBIDDEN)
        if child.status != Child.INACTIVE:
            return Response({"detail": "This case is already active."},
                            status=status.HTTP_400_BAD_REQUEST)
        child.status = Child.ACTIVE
        child.case_status = Child.STAGE_PRE_ASSESSMENT
        child.assigned_psychologist = None
        fields = ["status", "case_status", "assigned_psychologist", "updated_at"]
        if taking_over:
            child.social_worker = request.user
            fields.append("social_worker")
        child.save(update_fields=fields)
        # Taken over: the worker who held it is told, since it has just left
        # their records. Otherwise as before.
        log_activity(request.user, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label=child.fullname, entity_id=child.id,
                     recipient=previous if taking_over else child.assigned_psychologist)
        return Response({"status": child.status, "case_status": child.case_status,
                         "social_worker": child.social_worker_id})

    @action(detail=True, methods=["post"], url_path="remove-duplicate")
    def remove_duplicate(self, request, pk=None):
        """The ISA takes out a record that was made by mistake for a child who
        already has one (children/duplicates.py has the rule).

        Body: duplicate_of, the id of the record this one repeats, and
        case_reference, the case number of THIS record typed out as the
        confirmation. Refused with one sentence unless the two match on first
        name, last name and birth date and this one holds nothing but what Add
        Record makes. Nothing else deletes a child.

        Checked before the record is looked up, so anyone else is told no
        whether or not the id exists."""
        if role_of(request) != Role.ADMINISTRATOR:
            return Response({"detail": "Only the ISA (Administrator) can remove a duplicate record."},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            done = duplicates.remove(pk, request.data.get("duplicate_of"),
                                     request.data.get("case_reference"), by=request.user)
        except duplicates.Refused as refusal:
            return Response({"detail": str(refusal)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(done)

    @action(detail=False, methods=["post"], url_path="transfer")
    def transfer_records(self, request):
        """The ISA moves records from one social worker to another, many at a
        time (children/transfer.py has the rules).

        Body: from_social_worker, to_social_worker (account ids) and children
        (the ids to move, 1 to 1,000). All or nothing: 400 for a request that
        is malformed or names an account that cannot take part, 409 - with the
        case numbers - when a record on the list is no longer the first
        worker's active record. 200 gives the count, the receiving worker and
        the records moved.

        The role is checked before the body is read, so anyone else is told no
        whatever they send."""
        if role_of(request) != Role.ADMINISTRATOR:
            return Response({"detail": "Only the ISA (Administrator) can move records between social workers."},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            done = transfer.move(request.user, *transfer.parse(request.data))
        except transfer.Refused as refusal:
            return Response(refusal.body, status=refusal.status)
        return Response(done)

    # What a screen may say it exported, and how the audit line names it. A
    # closed list rather than free text: the line is written from the client's
    # word, and a caller must not be able to put its own sentence in the trail.
    EXPORT_LISTS = {"records": "the Records list", "results": "the result entries"}
    # Far above any real roster; it only stops a nonsense number being logged.
    EXPORT_MAX_ROWS = 1_000_000
    # A count written as digits is taken only if it is short enough to be one.
    # Python refuses to turn more than 4,300 digits into an integer and raises,
    # which is a 500 for whoever sent them.
    EXPORT_MAX_DIGITS = len(str(EXPORT_MAX_ROWS))

    # Per account, because the line is about the caller and anyone who can read
    # the list can write it: without a ceiling one signed-in account could fill
    # the ISA's security log with its own exports. The rate is `exports` in
    # settings (REST_FRAMEWORK, DEFAULT_THROTTLE_RATES).
    @action(detail=False, methods=["post"], url_path="exported", throttle_scope="exports")
    def exported(self, request):
        """A screen built a CSV of children in the browser; write down that it
        did, who did it and how many rows.

        There is no export endpoint to log (the file is made from the rows
        already on screen, so it can only hold what its maker could see), which
        left nobody able to say who took the Records list out of the system.
        The browser reports it after saving the file. Body: `rows`, and
        optionally `what` - "records" (the default) or "results".

        Any role that can read the list may say so; the line is about the
        caller, so it carries nobody else's name.
        """
        if role_of(request) not in (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        data = request.data if hasattr(request.data, "get") else {}
        rows = data.get("rows")
        if (isinstance(rows, str) and rows.isascii() and rows.isdigit()
                and len(rows) <= self.EXPORT_MAX_DIGITS):
            rows = int(rows)
        if isinstance(rows, bool) or not isinstance(rows, int) or not 0 <= rows <= self.EXPORT_MAX_ROWS:
            return Response({"rows": "Say how many rows the file holds, as a whole number."},
                            status=status.HTTP_400_BAD_REQUEST)
        which = data.get("what") or "records"
        what = self.EXPORT_LISTS.get(which) if isinstance(which, str) else None
        if what is None:
            return Response({"what": "That is not a list that can be exported."},
                            status=status.HTTP_400_BAD_REQUEST)
        log_activity(
            request.user, ActivityLog.EXPORTED, ActivityLog.SECURITY,
            entity_type="Child",
            entity_label=f"Exported {what} (CSV, {rows} {'row' if rows == 1 else 'rows'})")
        return Response(status=status.HTTP_201_CREATED)

    # Throttled per account because a row says whether the typed birth date
    # matches (`blocks_new_record`, `reopenable`): called with no ceiling, a
    # social worker who knows a name could step through birth dates and learn
    # another worker's child's. Normal typing is debounced and stays far
    # under it. The rate is `duplicate_check` in settings.
    @action(detail=False, methods=["get"], url_path="check-duplicate",
            throttle_scope="duplicate_check")
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
        first = request.query_params.get("first_name")
        last = request.query_params.get("last_name")
        birth = request.query_params.get("birth_date")
        # Names are compared as the save refusal compares them (NFC, case
        # folded, spaces collapsed). First AND last name are needed: a last
        # name alone is too broad.
        ids = duplicates.ids_with_name(first, last)
        if not ids:
            return Response({"matches": []})
        # The records the save would refuse a second one for: same names AND
        # the birth date typed, and still active. Said on every row so the form
        # can stop the intake at once; it tells the typer nothing they did not
        # type themselves, so another worker's row discloses no more than before.
        born = _typed_birth(birth)
        same = ({c.pk for c in duplicates.same_child(first, last, born)}
                if born is not None else set())
        # select_related: every row below renders the psychologist's name, and
        # without the join that is an extra query per match - on an endpoint the
        # intake form calls while somebody is still typing a name.
        found = list(Child.objects.filter(pk__in=ids)
                     .select_related("assigned_psychologist", "social_worker")
                     .order_by("-updated_at"))
        # The records the save would refuse come first, then the newest: with
        # a common name more than five records share it, and cutting by date
        # alone could leave out the very one the save refuses - a warning that
        # never appears for a refusal that always does. sorted() is stable, so
        # the order inside each group stays newest first.
        matches = sorted(found, key=lambda c: c.pk not in same)[:5]
        mine = set(scope_to_visible(Child.objects.filter(pk__in=[c.pk for c in matches]),
                                    request, path=None).values_list("pk", flat=True))

        def row(c):
            blocks = c.pk in same and c.status == Child.ACTIVE
            if c.pk in mine:
                return {"id": c.id, "fullname": c.fullname, "status": c.status,
                        "birth_date": c.birth_date,
                        "psychologist_name": display_name(c.assigned_psychologist) or None,
                        "social_worker_name": display_name(c.social_worker) or None,
                        "yours": True, "blocks_new_record": blocks}
            held = {"yours": False, "held_by": display_name(c.social_worker) or None,
                    "blocks_new_record": blocks}
            # A TERMINATED case a social worker finds here they may reopen and
            # take over (owner, 30 Sep 2026): a returning child arrives at
            # whoever runs intake that day, not at the worker who closed it.
            # The id is only good together with the name AND birth date typed
            # (see reopen); still no birth date and no psychologist. A closed
            # case matched by name alone is shown as held, not reopenable, and
            # carries no id.
            if c.status == Child.INACTIVE and role == Role.STAFF:
                if c.pk in same:
                    held.update({"id": c.id, "fullname": c.fullname, "status": c.status,
                                 "reopenable": True})
                else:
                    held["reopenable"] = False
            return held
        return Response({"matches": [row(c) for c in matches]})


def _typed_birth(raw):
    """The birth date typed at intake as a date, or None when blank or not a
    date (a half-typed one must not raise)."""
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None


def _is_same_child(child, first, last, birth):
    """Whether `child` is the child typed at intake by the save refusal's
    rule (children/duplicates.py): first name, last name AND birth date.
    `reopen` authorises a takeover with this, and `check_duplicate` offers the
    button by it, so the offer and the acceptance cannot differ."""
    born = _typed_birth(birth)
    return born is not None and child.pk in {
        c.pk for c in duplicates.same_child(first, last, born)}


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
        return Response({"detail": message, "number": number, "verified": True,
                         **self._standing(request.user, number)})

    @staticmethod
    def _standing(user, number):
        """Whether `user` has a confirmation for `number` that can still go on
        a record, and until when. The form shows "Confirmed" from this, so it
        can drop the claim when the hour is up instead of keeping it in a
        draft."""
        at = custodian.confirmed_at(user, number)
        return {"verified": at is not None,
                "confirmed_until": (at + custodian.CONFIRMED_FOR) if at else None}

    def get(self, request):
        """Is this number still confirmed for the caller? ?number=..."""
        refused = self._refuse(request)
        if refused:
            return refused
        try:
            number = custodian.normalise(request.query_params.get("number"))
        except ValueError as exc:
            return Response({"number": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not number:
            return Response({"number": "Enter the custodian's mobile number."},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"number": number, **self._standing(request.user, number)})
