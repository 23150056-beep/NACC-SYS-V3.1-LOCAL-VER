from django.db.models import Q
from rest_framework import mixins, viewsets, permissions
from accounts import email_notifications
from accounts.models import Role
from activity.models import ActivityLog
from activity.serializers import ActivityLogSerializer
from accounts.scoping import role_of, scope_to_visible
from children.models import Child


class ActivityLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None
    serializer_class = ActivityLogSerializer

    def list(self, request, *args, **kwargs):
        # Every screen reads this feed, after every save and at sign-in, which
        # makes it the one request that is certain to come while the app is in
        # use and there is no scheduler (Render's free plan runs none). So it
        # also mails the acceptances that nothing else has: a psychologist who
        # accepted and then closed the browser before the email went. At most
        # once a minute per process, and one cache call the rest of the time.
        email_notifications.maybe_sweep()
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        # What a move of records writes for each record is its audit trail, not
        # news: see ActivityLog.KIND_MOVE_DETAIL. Left out of every feed, or one
        # leaver's caseload fills the 50 lines below.
        qs = ActivityLog.objects.exclude(kind=ActivityLog.KIND_MOVE_DETAIL)
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
            #
            # "Assignment" rows carry the child's id too (children/
            # assignment.py): who was asked to take one of their children,
            # and the answer.
            #
            # So do "CaseStudy" rows (case_study/views.py): a case study is
            # started on one child's record and the event is keyed by that
            # child, so it reaches only the social worker who holds it. The
            # event names the child and never the case study's text.
            own = scope_to_visible(Child.objects.all(), self.request, path=None)
            qs = qs.filter(
                Q(entity_type__in=["Child", "Assignment", "CaseStudy"],
                  entity_id__in=own.values("pk"))
                | Q(recipient=self.request.user),
                category=ActivityLog.RECORD,
            )
        else:
            # Administrator: the full audit stream, except the second copy of a
            # move's sentence, which is addressed to the worker who gave the
            # records up (they already have the receiving worker's).
            qs = qs.exclude(kind=ActivityLog.KIND_MOVE_COPY)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs[:50]
