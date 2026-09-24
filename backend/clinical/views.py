import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Role
from accounts.scoping import role_of as _role, scope_to_visible
from accounts.permissions import CanManageInstruments, ProgressRecordAccess
from activity.models import ActivityLog
from activity.services import log_activity
from clinical.models import (
    InstrumentCatalog, AgencyFormTemplate, ConsentRecord,
    ClinicalInterviewRecord, ProblemEntry, PreAssessment,
    PsychologicalReport, RemarkNote, TreatmentPlan, ResultEntry, CaseReferral,
    OpinionnaireInvite, SelfReportFlag,
)
# The extensions a consent scan may be, and the exact type each is served
# as. Keyed off the stored name rather than anything the browser guesses.
_CONSENT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "webp": "image/webp",
    "heic": "image/heic", "heif": "image/heif",
}

from clinical.serializers import (
    ALLOWED_CONSENT_EXTENSIONS,
    InstrumentCatalogSerializer, AgencyFormTemplateSerializer,
    ConsentRecordSerializer, ClinicalInterviewRecordSerializer,
    ProblemEntrySerializer, PreAssessmentSerializer,
    PsychologicalReportSerializer, RemarkNoteSerializer,
    TreatmentPlanSerializer, ResultEntrySerializer, CaseReferralSerializer,
    OpinionnaireInviteSerializer, SelfReportFlagSerializer,
)
from clinical.self_report_detection import detect_concerns
from clinical.self_report_model_check import start_model_check
from clinical.services import extract_text

logger = logging.getLogger(__name__)

# The one answer to "may this record move to another child?" - no, for anyone:
# the owner's decision, 24 Sep 2026. Shared by the clinical records and the
# case referral, which have separate viewsets and must not drift apart.
_STAYS_WITH_CHILD = {"child": "A record stays with the child it was filed for. "
                              "File it again for the right child."}


def _refuse_a_move(serializer):
    """Refuse an update that changes the record's child. Naming the same
    child again is not a move, so a client that sends the whole row back
    still saves."""
    moving_to = serializer.validated_data.get("child")
    if moving_to is not None and moving_to != serializer.instance.child:
        raise ValidationError(_STAYS_WITH_CHILD)


def _serve_attachment(obj):
    """Authenticated download of an uploaded file. MEDIA_URL is never exposed
    directly, so the viewset's queryset scoping is what decides who gets the
    bytes - by the time this runs, that decision is already made.

    The consent scan deliberately does NOT come through here. It is served
    INLINE so the frontend can preview it in a blob viewer, and inline is
    precisely what makes an uploaded .html dangerous - so it carries its own
    content-type allowlist and nosniff header. That is a security decision,
    not a duplication waiting to be folded in.
    """
    from django.http import FileResponse
    try:
        handle = obj.file.open("rb")
    except (FileNotFoundError, ValueError):
        return Response({"detail": "File is missing from storage."},
                        status=status.HTTP_404_NOT_FOUND)
    return FileResponse(
        handle, as_attachment=True,
        filename=obj.original_filename or obj.file.name.rsplit("/", 1)[-1])




class _OwnedCatalogViewSet(viewsets.ModelViewSet):
    """The two owner-scoped catalogs: instrument titles, and agency forms.

    These were two classes of sixty-odd lines that differed in four strings and
    one default. Everything else - the include_inactive filter, the
    psychologist visibility clause, the ownership check, the forced owner on
    update, deactivate - was duplicated verbatim, comments included. That
    matters more than the line count, because `perform_update`'s comment
    describes a SECURITY control: without the forced owner a psychologist can
    hand their own row into the admin-only shared pool by sending owner=null.
    A control with two implementations is a control that can be fixed in one of
    them.

    The one real difference is kept as a flag rather than smoothed away. When
    an administrator creates a row with no owner, an INSTRUMENT becomes
    agency-wide shared (owner stays null) while a FORM TEMPLATE becomes the
    administrator's own. That asymmetry looks like an oversight and is not:
    the instrument catalog is governance, the form list is authorship.
    """

    permission_classes = [CanManageInstruments]
    pagination_class = None

    model = None
    entity_type = ""            # what the activity log calls one of these
    denied_message = ""         # what a psychologist is told when refused
    owner_defaults_to_creator = False

    def get_queryset(self):
        qs = self.model.objects.all()
        if self.request.query_params.get("include_inactive") != "true":
            qs = qs.filter(active=True)
        if _role(self.request) == Role.PSYCHOLOGIST:
            qs = qs.filter(Q(owner=self.request.user) | Q(owner__isnull=True))
        return qs

    def _log(self, obj, action_name):
        log_activity(self.request.user, action_name, ActivityLog.RECORD,
                     entity_type=self.entity_type, entity_label=obj.title,
                     entity_id=obj.id)

    def _assert_can_write(self, obj):
        # Shared (owner=None) rows are admin-managed; a psychologist may only
        # modify what they own.
        if _role(self.request) == Role.PSYCHOLOGIST and obj.owner_id != self.request.user.id:
            raise PermissionDenied(self.denied_message)

    def perform_create(self, serializer):
        if _role(self.request) == Role.PSYCHOLOGIST:
            obj = serializer.save(owner=self.request.user)
        else:
            owner = serializer.validated_data.get("owner")
            if owner is None and self.owner_defaults_to_creator:
                owner = self.request.user
            obj = serializer.save(owner=owner)
        self._log(obj, ActivityLog.CREATED)

    def perform_update(self, serializer):
        self._assert_can_write(serializer.instance)
        if _role(self.request) == Role.PSYCHOLOGIST:
            # Force the owner so a psychologist cannot self-promote their own
            # row into the admin-only shared pool via owner=null, or hand it to
            # another user, by supplying "owner" in the body.
            obj = serializer.save(owner=self.request.user)
        else:
            obj = serializer.save()
        self._log(obj, ActivityLog.UPDATED)

    def perform_destroy(self, instance):
        self._assert_can_write(instance)
        instance.delete()

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        obj = self.get_object()
        self._assert_can_write(obj)
        obj.active = False
        obj.save(update_fields=["active", "updated_at"])
        self._log(obj, ActivityLog.ARCHIVED)
        return Response({"active": False}, status=status.HTTP_200_OK)


class InstrumentCatalogViewSet(_OwnedCatalogViewSet):
    """Title-only instrument catalog. Psychologists manage their own entries,
    administrators manage all of them (catalog governance).

    There is no `activate` counterpart to deactivate, and there was not really
    one before either: the endpoint existed, no screen called it and no test
    covered it. If reactivating an instrument is wanted, it wants a button.
    """

    model = InstrumentCatalog
    serializer_class = InstrumentCatalogSerializer
    entity_type = "Instrument"
    denied_message = "Shared instruments are managed by the administrator."
    # owner_defaults_to_creator stays False: an administrator creating one with
    # no owner is creating an agency-wide shared instrument, deliberately.


class AgencyFormTemplateViewSet(_OwnedCatalogViewSet):
    """Agency-authored form templates (consent, clinical interview, ...).
    Same ownership rules as the catalog; attestation enforced by the serializer."""

    model = AgencyFormTemplate
    serializer_class = AgencyFormTemplateSerializer
    entity_type = "AgencyForm"
    denied_message = "Official agency forms can only be edited by an administrator."
    # Unlike the catalog: an ownerless form an administrator creates is theirs.
    owner_defaults_to_creator = True

    def get_queryset(self):
        qs = super().get_queryset()
        form_type = self.request.query_params.get("type")
        return qs.filter(form_type=form_type) if form_type else qs


class _ChildScopedClinicalViewSet(viewsets.ModelViewSet):
    """Shared behavior for per-child clinical records (consent, interview,
    problems): read admin/staff/psychologist (psychologist scoped to assigned
    children); write admin or the child's assigned psychologist."""
    permission_classes = [ProgressRecordAccess]
    pagination_class = None
    model = None
    author_field = None  # set by subclass: who recorded the row

    def get_queryset(self):
        # The author comes along with the child, because every serializer in
        # this family renders `author.fullname`. Without it the list runs one
        # extra query per row: /api/remarks/ measured 306 queries for 303
        # rows, and 3 with this line. Locally that is cheap SQLite; in
        # production it is one network round-trip each, to another region.
        qs = self.model.objects.select_related("child")
        if self.author_field:
            qs = qs.select_related(self.author_field)
        child_id = self.request.query_params.get("child")
        if child_id:
            if not str(child_id).isdigit():
                return qs.none()
            qs = qs.filter(child_id=child_id)
        qs = scope_to_visible(qs, self.request)
        return qs

    def _assert_can_write(self, child):
        role = _role(self.request)
        if role == Role.ADMINISTRATOR:
            return
        if role == Role.PSYCHOLOGIST and child.assigned_psychologist_id == self.request.user.id:
            return
        raise PermissionDenied("You can only manage clinical records for your assigned children.")

    def perform_create(self, serializer):
        self._assert_can_write(serializer.validated_data["child"])
        obj = serializer.save(**{self.author_field: self.request.user})
        log_activity(self.request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type=self.model.__name__, entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)

    def perform_update(self, serializer):
        self._assert_can_write(serializer.instance.child)
        # A record stays with the child it was filed for, whoever asks - the
        # owner's decision, 24 Sep 2026. `child` is writable on update, and
        # checking only where a record WAS once let one PATCH put a report on a
        # child who was not the editor's; even between an editor's own children
        # a move would leave one child's record in another's file, logged only
        # as "updated".
        _refuse_a_move(serializer)
        obj = serializer.save()
        log_activity(self.request.user, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type=self.model.__name__, entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)


class PreAssessmentViewSet(_ChildScopedClinicalViewSet):
    model = PreAssessment
    serializer_class = PreAssessmentSerializer
    author_field = "psychologist"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("instruments").select_related("consent", "interview")

    def create(self, request, *args, **kwargs):
        """Resume the psychologist's own in-progress pre-assessment for this
        child instead of starting a duplicate — the wizard calls this every
        time it's (re)opened for a child, including after navigating away."""
        child_id = request.data.get("child")
        if child_id and str(child_id).isdigit():
            # Through get_queryset() so assignment scoping still applies if the
            # child has since been reassigned away from this psychologist.
            existing = self.get_queryset().filter(
                child_id=child_id, psychologist=request.user,
                status=PreAssessment.IN_PROGRESS,
            ).first()
            if existing:
                return Response(self.get_serializer(existing).data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        self._assert_can_write(serializer.validated_data["child"])
        obj = serializer.save(psychologist=self.request.user, status=PreAssessment.IN_PROGRESS)
        log_activity(self.request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="PreAssessment", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Mark the pre-assessment completed. Requires a SIGNED consent and at
        least one instrument title selected."""
        obj = self.get_object()
        self._assert_can_write(obj.child)
        if obj.status == PreAssessment.COMPLETED:
            return Response({"detail": "Already completed."}, status=status.HTTP_400_BAD_REQUEST)
        if not obj.consent or obj.consent.status != ConsentRecord.SIGNED:
            return Response({"consent": "A signed consent is required before completing the pre-assessment."},
                            status=status.HTTP_400_BAD_REQUEST)
        if obj.instruments.count() == 0:
            return Response({"instruments": "Select at least one instrument title."},
                            status=status.HTTP_400_BAD_REQUEST)
        from django.utils import timezone as tz
        obj.status = PreAssessment.COMPLETED
        obj.completed_at = tz.now()
        obj.save(update_fields=["status", "completed_at", "updated_at"])
        log_activity(request.user, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="PreAssessment", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)
        return Response(PreAssessmentSerializer(obj).data)


class ConsentRecordViewSet(_ChildScopedClinicalViewSet):
    model = ConsentRecord
    serializer_class = ConsentRecordSerializer
    author_field = "recorded_by"
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Authenticated serving of the signed-consent scan. Served inline
        (not as an attachment) so the frontend can preview it in a blob
        viewer/iframe instead of triggering a file download."""
        from django.http import FileResponse
        obj = self.get_object()
        if not obj.scan:
            return Response({"detail": "No scan attached to this consent."},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            handle = obj.scan.open("rb")
        except (FileNotFoundError, ValueError):
            return Response({"detail": "File is missing from storage."},
                            status=status.HTTP_404_NOT_FOUND)

        # Inline is what makes the preview work, and it is also what makes an
        # uploaded .html dangerous: the frontend renders this response in an
        # iframe from a blob: URL, and a blob inherits the app's origin. The
        # upload is now restricted (ConsentRecordSerializer.validate_scan), but
        # rows predating that restriction are still in storage, so the decision
        # is made again here on the way out rather than trusted from the way in.
        name = obj.scan.name.rsplit("/", 1)[-1]
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
        previewable = ext in ALLOWED_CONSENT_EXTENSIONS
        response = FileResponse(
            handle,
            as_attachment=not previewable,
            filename=name,
            # Never let the browser pick: a guessed text/html renders, and
            # nosniff only stops the guess when the header is already right.
            content_type=(_CONSENT_CONTENT_TYPES.get(ext) if previewable
                          else "application/octet-stream"),
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ClinicalInterviewRecordViewSet(_ChildScopedClinicalViewSet):
    model = ClinicalInterviewRecord
    serializer_class = ClinicalInterviewRecordSerializer
    author_field = "interviewer"


class ProblemEntryViewSet(_ChildScopedClinicalViewSet):
    model = ProblemEntry
    serializer_class = ProblemEntrySerializer
    author_field = "logged_by"


class PsychologicalReportViewSet(_ChildScopedClinicalViewSet):
    model = PsychologicalReport
    serializer_class = PsychologicalReportSerializer
    author_field = "author"
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _findings(self, text, child):
        """clinical.report_check against the children this caller can see -
        never wider, or the check would answer "is there a record for this
        name?" for children the caller has no access to."""
        from clinical.report_check import check_for
        return check_for(self.request, text, child)

    def perform_create(self, serializer):
        child = serializer.validated_data["child"]
        self._assert_can_write(child)
        upload = serializer.validated_data["file"]
        extracted = extract_text(upload)
        obj = serializer.save(author=self.request.user,
                              original_filename=upload.name,
                              extracted_text=extracted,
                              check_findings=self._findings(extracted, child))
        log_activity(self.request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="PsychologicalReport", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)

    @action(detail=False, methods=["post"])
    def check(self, request):
        """What the check says about a file before it is filed. Saves nothing.

        The upload form asks this first, so a report carrying another child's
        name is caught while it can still be swapped for the right file,
        rather than after it is in the case file.
        """
        from children.models import Child
        child_id = request.data.get("child")
        child = (scope_to_visible(Child.objects.all(), request, path=None)
                 .filter(pk=child_id).first() if str(child_id).isdigit() else None)
        if child is None:
            return Response({"child": "Choose a child."}, status=status.HTTP_400_BAD_REQUEST)
        self._assert_can_write(child)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": "Choose a file."}, status=status.HTTP_400_BAD_REQUEST)
        self.get_serializer().validate_file(upload)
        text = extract_text(upload)
        return Response({"readable": bool(text),
                         "findings": self._findings(text, child) if text else []})

    @action(detail=True, methods=["post"], url_path="review-check")
    def review_check(self, request, pk=None):
        """Somebody who may edit this report has looked at what the check
        found. The findings stay on the row; only the flag goes."""
        obj = self.get_object()
        self._assert_can_write(obj.child)
        obj.check_reviewed = True
        obj.save(update_fields=["check_reviewed"])
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        return _serve_attachment(self.get_object())


class CaseReferralViewSet(viewsets.ModelViewSet):
    """The social worker's side of the split-view document area:
    write admin/staff, read all three roles (psychologist scoped to
    assigned children)."""
    permission_classes = [IsAuthenticated]
    pagination_class = None
    serializer_class = CaseReferralSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        qs = CaseReferral.objects.select_related("child", "uploaded_by")
        child_id = self.request.query_params.get("child")
        if child_id:
            if not str(child_id).isdigit():
                return qs.none()
            qs = qs.filter(child_id=child_id)
        qs = scope_to_visible(qs, self.request)
        return qs

    def _assert_can_write(self):
        if _role(self.request) not in (Role.ADMINISTRATOR, Role.STAFF):
            raise PermissionDenied("Only social workers or administrators upload case referrals.")

    def perform_create(self, serializer):
        self._assert_can_write()
        upload = serializer.validated_data["file"]
        extracted = extract_text(upload)
        obj = serializer.save(uploaded_by=self.request.user,
                              original_filename=upload.name,
                              extracted_text=extracted)
        log_activity(self.request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="CaseReferral", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)

    def perform_update(self, serializer):
        self._assert_can_write()
        # Fixed to its child like every clinical record. A referral is also
        # what lets a child's sessions be booked, so a moved one would quietly
        # unlock one child's calendar and lock another's.
        _refuse_a_move(serializer)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_can_write()
        instance.delete()

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        return _serve_attachment(self.get_object())


class RemarkNoteViewSet(_ChildScopedClinicalViewSet):
    model = RemarkNote
    serializer_class = RemarkNoteSerializer
    author_field = "author"


class TreatmentPlanViewSet(_ChildScopedClinicalViewSet):
    model = TreatmentPlan
    serializer_class = TreatmentPlanSerializer
    author_field = "author"


class ResultEntryViewSet(_ChildScopedClinicalViewSet):
    model = ResultEntry
    serializer_class = ResultEntrySerializer
    author_field = "entered_by"

    def get_queryset(self):
        return super().get_queryset().select_related("instrument", "entered_by", "child")


class OpinionnaireInviteViewSet(viewsets.ModelViewSet):
    """QR survey invites. Created by staff/admin (intake) or the assigned
    psychologist; answers arrive through the public token endpoints."""
    permission_classes = [IsAuthenticated]
    pagination_class = None
    serializer_class = OpinionnaireInviteSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = OpinionnaireInvite.objects.select_related("child", "template")
        child_id = self.request.query_params.get("child")
        if child_id:
            if not str(child_id).isdigit():
                return qs.none()
            qs = qs.filter(child_id=child_id)
        qs = scope_to_visible(qs, self.request)
        return qs

    def _assert_can_write(self, child):
        role = _role(self.request)
        if role in (Role.ADMINISTRATOR, Role.STAFF):
            return
        if role == Role.PSYCHOLOGIST and child.assigned_psychologist_id == self.request.user.id:
            return
        raise PermissionDenied("You cannot create survey invites for this child.")

    def perform_create(self, serializer):
        from datetime import timedelta
        from django.utils import timezone
        self._assert_can_write(serializer.validated_data["child"])
        obj = serializer.save(created_by=self.request.user,
                              expires_at=timezone.now() + timedelta(days=7))
        log_activity(self.request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="OpinionnaireInvite", entity_label=obj.child.fullname,
                     entity_id=obj.id, recipient=obj.child.assigned_psychologist)

    def perform_destroy(self, instance):
        self._assert_can_write(instance.child)
        instance.delete()


def _flag_self_report(invite, answers):
    """Create a lexicon flag for every (question, answer) pair that fires.

    One flag per question: `matched` names the first phrase that fired, which
    is what a reviewer needs to see why. The unique constraint makes this safe
    to call again over the same submission.
    """
    for question, answer in (answers or {}).items():
        for hit in detect_concerns(question, answer):
            SelfReportFlag.objects.get_or_create(
                invite=invite, question=question,
                source=SelfReportFlag.LEXICON,
                defaults={"child": invite.child, "answer": answer,
                          "matched": hit["phrase"]})
            break


class PublicOpinionnaireView(viewsets.ViewSet):
    """Unauthenticated, token-gated survey endpoints for the child's device.
    Exposes the agency form fields and the child's FIRST NAME only."""
    permission_classes = []
    authentication_classes = []

    def _get_invite(self, token):
        try:
            return OpinionnaireInvite.objects.select_related("child", "template").get(token=token)
        except OpinionnaireInvite.DoesNotExist:
            return None

    def retrieve(self, request, pk=None):
        invite = self._get_invite(pk)
        if invite is None:
            return Response({"detail": "This survey link is not valid."},
                            status=status.HTTP_404_NOT_FOUND)
        if not invite.is_open:
            return Response({"detail": "This survey link has expired or was already answered."},
                            status=status.HTTP_410_GONE)
        return Response({
            "first_name": (invite.child.fullname or "").split(" ")[0],
            "title": invite.template.title,
            "fields": invite.template.fields,
        })

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        from django.utils import timezone
        invite = self._get_invite(pk)
        if invite is None:
            return Response({"detail": "This survey link is not valid."},
                            status=status.HTTP_404_NOT_FOUND)
        if not invite.is_open:
            return Response({"detail": "This survey link has expired or was already answered."},
                            status=status.HTTP_410_GONE)
        answers = request.data.get("answers")
        if not isinstance(answers, dict) or not answers:
            return Response({"answers": "Provide the survey answers."},
                            status=status.HTTP_400_BAD_REQUEST)
        # Keep only answers for fields defined on the template; cap length.
        labels = {f.get("label") for f in (invite.template.fields or [])}
        cleaned = {k: str(v)[:2000] for k, v in answers.items() if k in labels}
        invite.answers = cleaned
        invite.status = OpinionnaireInvite.SUBMITTED
        invite.submitted_at = timezone.now()
        invite.save(update_fields=["answers", "status", "submitted_at"])

        # The lexicon runs here: deterministic, sub-millisecond, and the floor
        # that holds when the model is unavailable. Nothing it does may fail
        # the request — losing a child's answers to a detector bug would be far
        # worse than missing a flag.
        try:
            _flag_self_report(invite, cleaned)
        except Exception:                                    # noqa: BLE001
            logger.exception("Self-report flagging failed for invite %s", invite.pk)

        # The model runs out of band. A child on her own device does not wait.
        try:
            start_model_check(invite.pk)
        except Exception:                                    # noqa: BLE001
            logger.exception("Self-report model check could not start")

        return Response({"status": "submitted"})


class SelfReportFlagViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """A child's own words, flagged as worth reading.

    Deliberately NOT a `_ChildScopedClinicalViewSet`: self-reports are exempt
    from the carry-history control. That control spares a newly assigned
    psychologist a colleague's prior opinions; it must not hide the child from
    the person now responsible for her.
    """
    serializer_class = SelfReportFlagSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        qs = (SelfReportFlag.objects
              .select_related("child", "reviewed_by").order_by("-created_at"))
        child_id = self.request.query_params.get("child")
        if child_id:
            if not str(child_id).isdigit():
                return qs.none()
            qs = qs.filter(child_id=child_id)
        # Scope from the caller, never from a parameter.
        qs = scope_to_visible(qs, self.request)
        return qs

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        flag = self.get_queryset().filter(pk=pk).first()
        if flag is None:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        # First acknowledgement wins. The record of who actually read it first
        # must not be overwritten by whoever clicked next.
        if flag.reviewed_at is None:
            flag.reviewed_by = request.user
            flag.reviewed_at = timezone.now()
            flag.review_note = str(request.data.get("note") or "")[:2000]
            flag.save(update_fields=["reviewed_by", "reviewed_at", "review_note"])
        return Response(SelfReportFlagSerializer(flag).data)
