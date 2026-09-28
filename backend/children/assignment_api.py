"""The request queue: who has been asked to take which child, and the answer.

Read, accept, decline, withdraw - no create, update or delete here. A request
is made by picking a psychologist on the record (ChildViewSet), so there is one
door for asking, and it is the one that already checks the record.

Design: docs/superpowers/specs/2026-09-28-assignment-acceptance-design.md
"""
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.display import display_name
from accounts.models import Role
from accounts.scoping import role_of
from children import assignment
from children.models import AssignmentRequest
from clinical.reports import age_on
from scheduling.visibility import case_ref


class AssignmentRequestSerializer(serializers.ModelSerializer):
    """What a psychologist needs in order to decide, and nothing clinical.

    The child is not theirs until they accept, so no remark, report,
    assessment or self-report is here - only who the child is, what kind of
    case it is, why they were referred and whether a session could even be
    booked yet.
    """
    child_ref = serializers.SerializerMethodField()
    child_name = serializers.CharField(source="child.fullname", read_only=True)
    child_age = serializers.SerializerMethodField()
    child_gender = serializers.CharField(source="child.gender", read_only=True)
    case_type = serializers.CharField(source="child.case_type", read_only=True)
    case_category = serializers.CharField(source="child.case_category", read_only=True)
    case_status = serializers.CharField(source="child.case_status", read_only=True)
    referral_reason = serializers.CharField(source="child.referral_reason", read_only=True)
    has_case_referral = serializers.SerializerMethodField()
    psychologist_name = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()
    previous_psychologist_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentRequest
        fields = [
            "id", "status", "child", "child_ref", "child_name", "child_age",
            "child_gender", "case_type", "case_category", "case_status",
            "referral_reason", "has_case_referral",
            "psychologist", "psychologist_name", "requested_by_name",
            "previous_psychologist_name", "carry_history", "reason",
            "created_at", "decided_at", "decided_by_name",
        ]
        read_only_fields = fields

    def get_child_ref(self, obj):
        return case_ref(obj.child_id)

    def get_child_age(self, obj):
        return age_on(obj.child.birth_date, timezone.localdate())

    def get_has_case_referral(self, obj):
        # Prefetched by the viewset: one query for the page, not one a row.
        return bool(obj.child.case_referrals.all())

    def get_psychologist_name(self, obj):
        return display_name(obj.psychologist) or None

    def get_requested_by_name(self, obj):
        return display_name(obj.requested_by) or None

    def get_previous_psychologist_name(self, obj):
        return display_name(obj.previous_psychologist) or None

    def get_decided_by_name(self, obj):
        return display_name(obj.decided_by) or None


class AssignmentRequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                               viewsets.GenericViewSet):
    """Who sees which requests:

    - a psychologist, the ones addressed to them;
    - a social worker, the ones on their own records (accounts/scoping.py);
    - the ISA, all of them.

    Outside that a request 404s, the "hidden, not disclosed" convention.
    """
    serializer_class = AssignmentRequestSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        qs = (AssignmentRequest.objects
              .select_related("child", "psychologist", "requested_by",
                              "previous_psychologist", "decided_by")
              .prefetch_related("child__case_referrals"))
        role = role_of(self.request)
        if role == Role.PSYCHOLOGIST:
            qs = qs.filter(psychologist=self.request.user)
        elif role == Role.STAFF:
            qs = qs.filter(child__social_worker=self.request.user)
        elif role != Role.ADMINISTRATOR:
            return qs.none()
        if self.action != "list":
            # The filters narrow the queue, never which request an answer
            # is about.
            return qs
        wanted = self.request.query_params.get("status")
        if wanted:
            qs = qs.filter(status=wanted)
        child = self.request.query_params.get("child")
        if child and str(child).isdigit():
            qs = qs.filter(child_id=child)
        return qs

    def _refused(self, err):
        return Response({err.field: err.message}, status=err.status)

    def _answer(self, request, answer, **kwargs):
        req = self.get_object()
        # Only the psychologist asked answers. An administrator does not
        # accept on somebody's behalf: the point is that they agreed.
        if req.psychologist_id != request.user.id:
            return Response({"detail": "Only the psychologist asked can answer this."},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            req = answer(req, by=request.user, **kwargs)
        except assignment.AssignmentError as err:
            return self._refused(err)
        return Response(self.get_serializer(self.get_queryset().get(pk=req.pk)).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._answer(request, assignment.accept)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        return self._answer(request, assignment.decline,
                            reason=request.data.get("reason", ""))

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        req = self.get_object()
        # The ISA, or the social worker whose record it is - get_object has
        # already narrowed a social worker to their own records.
        if role_of(request) not in (Role.ADMINISTRATOR, Role.STAFF):
            return Response({"detail": "Only the ISA or the record's social worker "
                                       "can withdraw a request."},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            req = assignment.withdraw(req, by=request.user)
        except assignment.AssignmentError as err:
            return self._refused(err)
        return Response(self.get_serializer(self.get_queryset().get(pk=req.pk)).data)
