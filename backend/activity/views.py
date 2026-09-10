from django.db.models import Q
from rest_framework import mixins, viewsets, permissions
from accounts.models import Role
from activity.models import ActivityLog
from activity.serializers import ActivityLogSerializer
from accounts.scoping import role_of


class ActivityLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None
    serializer_class = ActivityLogSerializer

    def get_queryset(self):
        qs = ActivityLog.objects.all()
        role = role_of(self.request)
        if role == Role.PSYCHOLOGIST:
            # Psychologists only see notifications targeted at them.
            qs = qs.filter(recipient=self.request.user)
        elif role == Role.STAFF:
            # Staff see the case-coordination stream (records + assessments),
            # PLUS anything addressed to them personally.
            #
            # "Guardian" stays in this list although the model is gone: these
            # are log rows, and the ones written before July still say it.
            # Dropping it here would hide history, not tidy it.
            #
            # The recipient half is what stops this being a firehose. Every
            # `recipient=` in the codebase used to point at a psychologist, so
            # a staff member could not be told anything personally even in
            # principle; the adoption module now addresses a case to its owner.
            # It stays inside the RECORD category, so widening the filter does
            # not quietly hand staff the security audit trail as well.
            qs = qs.filter(
                Q(entity_type__in=["Child", "Guardian", "Assessment"])
                | Q(recipient=self.request.user),
                category=ActivityLog.RECORD,
            )
        # Administrator: full audit stream (unchanged).
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs[:50]
