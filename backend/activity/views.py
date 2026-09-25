from django.db.models import Q
from rest_framework import mixins, viewsets, permissions
from accounts.models import Role
from activity.models import ActivityLog
from activity.serializers import ActivityLogSerializer
from accounts.scoping import role_of, scope_to_visible
from children.models import Child


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
            # Staff see what happened to their OWN records, plus anything
            # addressed to them personally.
            #
            # This was every child-record event in the office, when staff
            # worked one shared caseload. Since 24 Sep 2026 each social worker
            # holds their own records (accounts/scoping.py), and a feed naming
            # every child in the agency was the one screen still showing the
            # others. The legacy "Guardian" and "Assessment" rows went with
            # it: their ids are not children's, so there is no way to tell
            # whose they were. Administrators still see all of them.
            #
            # It stays inside the RECORD category, so widening the filter does
            # not quietly hand staff the security audit trail as well.
            own = scope_to_visible(Child.objects.all(), self.request, path=None)
            qs = qs.filter(
                Q(entity_type="Child", entity_id__in=own.values("pk"))
                | Q(recipient=self.request.user),
                category=ActivityLog.RECORD,
            )
        # Administrator: full audit stream (unchanged).
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs[:50]
