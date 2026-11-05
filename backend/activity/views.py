import datetime
import re

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import mixins, viewsets, permissions
from accounts import email_notifications
from accounts.models import Role
from activity.models import ActivityLog
from activity.serializers import ActivityLogSerializer
from accounts.scoping import role_of, scope_to_visible
from children.models import Child


MAX_PAGE_SIZE = 200


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

    def _scoped(self, include_move_detail=False):
        """The role scope, shared by the feed and the audit trail.

        One rule, so the trail can never show an account more than its feed
        would, except the per-record transfer lines the feed leaves out.
        """
        # What a move of records writes for each record is its audit trail, not
        # news: see ActivityLog.KIND_MOVE_DETAIL. Left out of every feed, or one
        # leaver's caseload fills the 50 lines below. The trail keeps them.
        qs = ActivityLog.objects.all()
        if not include_move_detail:
            qs = qs.exclude(kind=ActivityLog.KIND_MOVE_DETAIL)
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
            # records up (they already have the receiving worker's), and the
            # line that an account reached the email limit. That one is written
            # as often as once an hour for as long as the account goes on, which
            # would fill the 50 lines below; the console's security log shows it
            # (newest five and a count) and the account's own drawer lists it.
            # The audit trail leaves it out as well: nothing it is sent says
            # what the line is but its words, and those are not read back.
            qs = qs.exclude(kind__in=[ActivityLog.KIND_MOVE_COPY,
                                      ActivityLog.KIND_EMAIL_LIMIT])
        return qs

    def get_queryset(self):
        qs = self._scoped()
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs[:50]

    @action(detail=False, methods=["get"], url_path="trail")
    def trail(self, request):
        """The full audit trail: filtered, paged, move_detail lines included."""
        params = request.query_params

        def bad(field, msg):
            return Response({field: msg}, status=400)

        def whole(field, default, minimum=1):
            raw = params.get(field)
            if raw in (None, ""):
                return default, None
            if not re.fullmatch(r"\d{1,9}", raw.strip()) or int(raw) < minimum:
                return None, bad(field, "Must be a whole number of at least %d." % minimum)
            return int(raw), None

        category = params.get("category") or ""
        if category and category not in dict(ActivityLog.CATEGORY_CHOICES):
            return bad("category", "Must be one of: record, user, security.")
        act = params.get("action") or ""
        if act and act not in dict(ActivityLog.ACTION_CHOICES):
            return bad("action", "Not a known action.")
        child, err = whole("child", None)
        if err:
            return err
        page, err = whole("page", 1)
        if err:
            return err
        size, err = whole("page_size", 50)
        if err:
            return err
        if size > MAX_PAGE_SIZE:
            return bad("page_size", "At most %d." % MAX_PAGE_SIZE)
        dates = {}
        for field in ("date_from", "date_to"):
            raw = (params.get(field) or "").strip()
            if raw:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                        raise ValueError
                    dates[field] = datetime.date.fromisoformat(raw)
                except ValueError:
                    return bad(field, "Use YYYY-MM-DD.")
        if len(dates) == 2 and dates["date_from"] > dates["date_to"]:
            return bad("date_from", "The start date is after the end date.")
        q = (params.get("q") or "").strip()
        if len(q) > 100:
            return bad("q", "At most 100 characters.")

        # Scope first, filters after: a filter can only narrow what the role
        # may already see.
        qs = self._scoped(include_move_detail=True)
        if category:
            qs = qs.filter(category=category)
        if act:
            qs = qs.filter(action=act)
        if q:
            qs = qs.filter(Q(entity_label__icontains=q) | Q(actor_label__icontains=q))
        if child is not None:
            qs = qs.filter(entity_type__in=["Child", "Assignment", "CaseStudy"], entity_id=child)
        tz = timezone.get_current_timezone()
        out_of_range = "That date is out of range."
        if "date_from" in dates:
            try:
                start = timezone.make_aware(
                    datetime.datetime.combine(dates["date_from"], datetime.time.min), tz)
            except (OverflowError, ValueError):
                return bad("date_from", out_of_range)
            qs = qs.filter(created_at__gte=start)
        if "date_to" in dates:
            try:
                end = timezone.make_aware(
                    datetime.datetime.combine(dates["date_to"] + datetime.timedelta(days=1),
                                              datetime.time.min), tz)
            except (OverflowError, ValueError):
                return bad("date_to", out_of_range)
            qs = qs.filter(created_at__lt=end)
        qs = qs.order_by("-created_at", "-id")
        count = qs.count()
        offset = (page - 1) * size
        rows = qs[offset:offset + size]
        return Response({
            "count": count, "page": page, "page_size": size,
            "results": ActivityLogSerializer(rows, many=True).data,
        })
