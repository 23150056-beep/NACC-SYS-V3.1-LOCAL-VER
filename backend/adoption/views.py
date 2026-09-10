"""The adoption tracker's HTTP surface.

Views are thin on purpose. Every statutory rule lives in `pipeline.py` and
`docket.py`; these functions translate an exception into a status code and
nothing more. The moment a rule gets re-checked here it has two homes, and the
two will disagree the first time one of them is corrected.
"""
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet, ViewSet

from accounts.models import Role
from accounts.scoping import role_of
from activity.models import ActivityLog
from activity.services import log_activity
from adoption import docket, pipeline, worklist
from adoption.models import AdoptionCase, AdoptionStage, PAP, Requirement
from adoption.serializers import (
    CaseCardSerializer, CaseDetailSerializer, HandoffSerializer, PAPSerializer,
    RequirementSerializer, StageSerializer,
)
from children.models import Child


class CaseworkAccess(BasePermission):
    """The board and everything that changes a case: administrators and staff.

    Psychologists are excluded by design, not by omission. Spec section 6 gives
    them the timeline of children they assessed; the office's whole adoption
    caseload is not theirs to browse.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and role_of(request) in (Role.ADMINISTRATOR, Role.STAFF))



def _cases_for(request):
    """Every case this account may see.

    Administrators and staff see the office. A psychologist sees only the
    children assigned to them - the same rule the clinical viewsets use, so a
    psychologist cannot reach a colleague's case by guessing an id.
    """
    qs = (AdoptionCase.objects
          .select_related("child", "current_stage", "owner", "pap")
          .prefetch_related("requirements", "requirements__stage", "clocks",
                            "events", "events__from_stage", "events__to_stage",
                            "events__actor"))
    if role_of(request) == Role.PSYCHOLOGIST:
        qs = qs.filter(child__assigned_psychologist=request.user)
    return qs


class BoardView(APIView):
    """Everything the tracker screen needs, in one request.

    One round trip rather than six: the stage strip, the cards, the KPI tiles,
    the handoff banner and the worklist are all views of the same set of cases,
    and fetching them separately is how they end up disagreeing on screen.
    """

    permission_classes = [CaseworkAccess]

    def get(self, request):
        cases = list(_cases_for(request).filter(closed_at__isnull=True))

        counts = dict(AdoptionCase.objects
                      .filter(closed_at__isnull=True)
                      .values_list("current_stage__number")
                      .annotate(n=Count("id")))
        stages = []
        for stage in AdoptionStage.objects.all():
            stages.append({**StageSerializer(stage).data,
                           "case_count": counts.get(stage.number, 0)})

        return Response({
            "stages": stages,
            "cases": CaseCardSerializer(cases, many=True).data,
            "kpis": worklist.kpis(),
            "handoffs": HandoffSerializer(pipeline.pending_handoffs(), many=True).data,
            "needs_attention": worklist.needs_attention(),
            "closure_reasons": [r for r, _ in AdoptionCase.CLOSURE_CHOICES],
        })


class AdoptionCaseViewSet(ReadOnlyModelViewSet):
    """Read a case, and move it.

    Read is open to the child's psychologist; every write is casework.
    """

    serializer_class = CaseDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = _cases_for(self.request)
        # `?child=` is how a child's own chart asks "is there an adoption case
        # here?". It is the only adoption view a psychologist's role can reach,
        # and the scoping above still decides whether they see anything.
        child = self.request.query_params.get("child")
        return qs.filter(child_id=child) if child else qs

    def get_serializer_class(self):
        return CaseCardSerializer if self.action == "list" else CaseDetailSerializer

    def _casework_or_403(self):
        if role_of(self.request) not in (Role.ADMINISTRATOR, Role.STAFF):
            return Response({"detail": "Your role cannot change an adoption case."},
                            status=http.HTTP_403_FORBIDDEN)
        return None

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        denied = self._casework_or_403()
        if denied:
            return denied
        case = self.get_object()
        try:
            pipeline.advance(case, actor=request.user, note=request.data.get("note", ""))
        except pipeline.StageBlocked as exc:
            return Response(
                {"detail": str(exc),
                 "blockers": RequirementSerializer(exc.blockers, many=True).data},
                status=http.HTTP_400_BAD_REQUEST)
        log_activity(request.user, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="AdoptionCase", entity_label=case.child.fullname,
                     entity_id=case.child_id)
        return Response(CaseDetailSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def revert(self, request, pk=None):
        denied = self._casework_or_403()
        if denied:
            return denied
        case = self.get_object()
        try:
            pipeline.revert(case, actor=request.user, note=request.data.get("note", ""))
        except pipeline.StageBlocked as exc:
            return Response({"detail": str(exc)}, status=http.HTTP_400_BAD_REQUEST)
        return Response(CaseDetailSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        # Casework, not an administrator's signature. A case ends for five
        # reasons and four of them are just what happened to the child; the
        # person who worked it is the person who knows which.
        denied = self._casework_or_403()
        if denied:
            return denied
        case = self.get_object()
        try:
            pipeline.close(case, actor=request.user,
                           reason=request.data.get("reason", ""),
                           note=request.data.get("note", ""))
        except pipeline.StageBlocked as exc:
            return Response({"detail": str(exc)}, status=http.HTTP_400_BAD_REQUEST)
        return Response(CaseDetailSerializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        """Link the prospective adoptive family chosen at the matching conference."""
        denied = self._casework_or_403()
        if denied:
            return denied
        case = self.get_object()
        pap = get_object_or_404(PAP, pk=request.data.get("pap"))
        case.pap = pap
        case.save(update_fields=["pap", "updated_at"])
        PAP.objects.filter(pk=pap.pk).update(status=PAP.MATCHED)
        return Response(CaseDetailSerializer(self.get_object()).data)


class RequirementViewSet(ViewSet):
    """Submit, verify and waive one docket line.

    Not a ModelViewSet: a requirement is never created or deleted through the
    API - the rows are seeded from the stage templates when a case is admitted,
    and the only thing that ever changes is its state.
    """

    permission_classes = [CaseworkAccess]

    def _get(self, request, pk):
        return get_object_or_404(
            Requirement.objects.select_related("case", "case__child", "stage"), pk=pk)

    def _run(self, fn, *args, **kwargs):
        try:
            requirement = fn(*args, **kwargs)
        except docket.NotPermitted as exc:
            # A role refusal is a 403; a state refusal - nothing submitted yet,
            # no reason given, your own upload - is a 400. The client shows them
            # differently, and the difference is the exception's type rather
            # than a word in its message.
            code = (http.HTTP_403_FORBIDDEN
                    if isinstance(exc, docket.RoleNotPermitted)
                    else http.HTTP_400_BAD_REQUEST)
            return Response({"detail": str(exc)}, status=code)
        return Response(RequirementSerializer(requirement).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        requirement = self._get(request, pk)
        upload = request.FILES.get("document")
        return self._run(
            docket.submit, requirement, actor=request.user, document=upload,
            original_filename=getattr(upload, "name", "") or "",
            due_date=request.data.get("due_date") or None)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return self._run(docket.verify, self._get(request, pk), actor=request.user)

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        return self._run(docket.waive, self._get(request, pk), actor=request.user,
                         reason=request.data.get("reason", ""))


class AdmitView(APIView):
    """Open a case for a released child. Staff and administrators only."""

    permission_classes = [CaseworkAccess]

    def post(self, request):
        child = get_object_or_404(Child, pk=request.data.get("child"))
        owner_id = request.data.get("owner") or request.user.id
        from django.contrib.auth import get_user_model
        owner = get_object_or_404(get_user_model(), pk=owner_id)
        try:
            case = pipeline.admit(child, owner=owner, actor=request.user,
                                  note=request.data.get("note", ""))
        except pipeline.NotAdmissible as exc:
            return Response({"detail": str(exc)}, status=http.HTTP_400_BAD_REQUEST)
        log_activity(request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="AdoptionCase", entity_label=child.fullname,
                     entity_id=child.id)
        return Response(CaseDetailSerializer(case).data, status=http.HTTP_201_CREATED)


class PAPViewSet(ReadOnlyModelViewSet):
    """The roster matching draws from."""

    serializer_class = PAPSerializer
    permission_classes = [CaseworkAccess]

    def get_queryset(self):
        qs = PAP.objects.all()
        if self.request.query_params.get("eligible") == "1":
            qs = qs.filter(status=PAP.ELIGIBLE)
        return qs
