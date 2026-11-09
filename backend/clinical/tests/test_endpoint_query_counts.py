"""Per-request query budgets for the endpoints every screen leans on.

On the hosted deployment each query is a round trip to another region, so what
matters is that the count does not grow with the data. Each test builds a small
set, counts, adds more of everything, counts again and requires the same
number, then holds a ceiling a little above today's figure so an accidental
duplicate read also fails.
"""
from datetime import datetime, time, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import (CaseReferral, PreAssessment, ProblemEntry,
                             RemarkNote)
from scheduling.models import Appointment, AvailabilityBlock

User = get_user_model()

# A date no clock reaches, so "from now on" holds whenever the suite runs.
# 02:00 UTC is 10:00 in Manila, on a Monday: inside the windows below.
FAR = datetime(2099, 1, 5, 2, 0, tzinfo=dt_timezone.utc)


class _World(APITestCase):
    def setUp(self):
        roles = {n: Role.objects.create(role_name=n) for n in
                 (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST)}
        self.admin = User.objects.create_user(
            email="a@x.ph", username="a@x.ph", password="pw12345678",
            role=roles[Role.ADMINISTRATOR])
        self.staff = User.objects.create_user(
            email="s@x.ph", username="s@x.ph", password="pw12345678",
            role=roles[Role.STAFF])
        self.psy_role = roles[Role.PSYCHOLOGIST]
        self.psy = self.make_psychologist("p0")
        self.made = 0

    def make_psychologist(self, tag):
        return User.objects.create_user(
            email=f"{tag}@x.ph", username=f"{tag}@x.ph", password="pw12345678",
            role=self.psy_role)

    def add_children(self, n):
        """n more children, each with its own psychologist, records, a window
        and a session, so a per-row read has something to multiply."""
        for _ in range(n):
            self.made += 1
            i = self.made
            psy = self.make_psychologist(f"q{i}")
            child = Child.objects.create(
                fullname=f"Kid {i}", first_name="Kid", last_name=str(i),
                case_type="Adoption", assigned_psychologist=psy,
                social_worker=self.staff)
            PreAssessment.objects.create(child=child, psychologist=psy,
                                         status=PreAssessment.COMPLETED)
            PreAssessment.objects.create(child=child, psychologist=psy,
                                         status=PreAssessment.IN_PROGRESS)
            RemarkNote.objects.create(child=child, author=psy, date="2026-01-05",
                                      text="note")
            ProblemEntry.objects.create(child=child, logged_by=psy, description="p")
            CaseReferral.objects.create(child=child, uploaded_by=self.staff,
                                        original_filename="r.pdf")
            AvailabilityBlock.objects.create(
                psychologist=psy, weekday=0, start_time=time(8), end_time=time(12),
                capacity=2)
            Appointment.objects.create(child=child, psychologist=psy, start=FAR,
                                       duration_minutes=60)

    def count(self, user, url):
        self.client.force_authenticate(user)
        self.client.get(url)    # warm per-process caches (content types etc.)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content[:200])
        return len(queries), response


class DashboardQueryCountTest(_World):
    CEILING = {"admin": 14, "staff": 17, "psychologist": 16}

    def test_the_census_costs_the_same_for_three_children_and_nine(self):
        self.add_children(3)
        url = "/api/reports/dashboard/"
        who = {"admin": self.admin, "staff": self.staff,
               "psychologist": Child.objects.first().assigned_psychologist}
        few = {k: self.count(u, url)[0] for k, u in who.items()}
        self.add_children(6)
        for role, user in who.items():
            many, response = self.count(user, url)
            self.assertEqual(few[role], many, f"{role}: grew with the children")
            self.assertLessEqual(many, self.CEILING[role], role)
        # And it is still the census: the ISA counts every active child.
        _, response = self.count(self.admin, url)
        self.assertEqual(response.data["census"]["active"], 9)


class ChildReportQueryCountTest(_World):
    def test_the_child_page_does_not_grow_with_its_records(self):
        self.add_children(1)
        child = Child.objects.get()
        url = f"/api/reports/child/{child.id}/"
        few, _ = self.count(self.admin, url)
        for n in range(8):
            psy = self.make_psychologist(f"extra{n}")
            PreAssessment.objects.create(child=child, psychologist=psy,
                                         status=PreAssessment.COMPLETED)
            RemarkNote.objects.create(child=child, author=psy, date="2026-01-06",
                                      text="more")
            ProblemEntry.objects.create(child=child, logged_by=psy, description="q")
            CaseReferral.objects.create(child=child, uploaded_by=psy,
                                        original_filename="s.pdf")
        many, response = self.count(self.admin, url)
        self.assertEqual(few, many)
        self.assertLessEqual(many, 18)
        self.assertEqual(len(response.data["problems"]), 9)
        self.assertEqual(len(response.data["case_referrals"]), 9)
        self.assertEqual(len(response.data["pre_assessments"]), 10)

    def test_the_pre_assessments_keep_the_page_order(self):
        self.add_children(1)
        child = Child.objects.get()
        older = child.pre_assessments.order_by("id").first()
        newer = child.pre_assessments.order_by("id").last()
        # Edit the older one last: it must come first.
        older.notes = "touched"
        older.save()
        _, response = self.count(self.admin, f"/api/reports/child/{child.id}/")
        self.assertEqual([p["id"] for p in response.data["pre_assessments"]][0],
                         older.id)
        self.assertNotEqual(older.id, newer.id)


class AvailabilityQueryCountTest(_World):
    def test_booked_ahead_comes_from_one_query_for_the_whole_list(self):
        self.add_children(3)
        url = "/api/availability/"
        few, _ = self.count(self.admin, url)
        self.add_children(9)
        many, response = self.count(self.admin, url)
        self.assertEqual(few, many)
        self.assertLessEqual(many, 4)
        self.assertEqual(len(response.data), 12)
        # Each window holds the one session booked on its psychologist's
        # Monday, inside its hours.
        self.assertEqual({row["booked_ahead"] for row in response.data}, {1})


class PendingCountQueryTest(_World):
    def test_the_badge_count_is_one_count_and_admin_only(self):
        for i in range(4):
            User.objects.create_user(email=f"w{i}@x.ph", username=f"w{i}@x.ph",
                                     password="pw12345678", status=User.PENDING)
        number, response = self.count(self.admin, "/api/users/pending-count/")
        self.assertEqual(response.data, {"pending": 4})
        self.assertLessEqual(number, 2)
        for user in (self.staff, self.psy):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get("/api/users/pending-count/").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/users/pending-count/").status_code, 401)


class ChildListQueryCountTest(_World):
    """GET /api/children/: the most-fetched endpoint. One query for the rows
    and one per prefetched relation, however many children there are."""
    CEILING = {"admin": 10, "staff": 10, "psychologist": 10}

    def test_the_list_costs_the_same_for_three_children_and_nine(self):
        self.add_children(3)
        url = "/api/children/"
        who = {"admin": self.admin, "staff": self.staff,
               "psychologist": Child.objects.first().assigned_psychologist}
        few = {k: self.count(u, url)[0] for k, u in who.items()}
        self.add_children(6)
        for role, user in who.items():
            many, response = self.count(user, url)
            self.assertEqual(few[role], many, f"{role}: grew with the children")
            self.assertLessEqual(many, self.CEILING[role], role)
        _, response = self.count(self.admin, url)
        self.assertEqual(len(response.data), 9)


class SignedInRequestQueryCountTest(_World):
    """A real token costs one query for the user, with the role joined in; the
    role used to be a second query on every authenticated request."""

    def test_the_token_lookup_reads_the_role_with_the_user(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        from accounts.token_claims import CLAIM
        refresh = RefreshToken.for_user(self.admin)
        refresh[CLAIM] = self.admin.get_session_auth_hash()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        client.get("/api/auth/me/")
        with CaptureQueriesContext(connection) as queries:
            response = client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200, response.content[:200])
        roles = [q["sql"] for q in queries if 'FROM "tbl_role"' in q["sql"]
                 and 'JOIN' not in q["sql"]]
        self.assertEqual(roles, [], "the role was read in a query of its own")


class AdminOverviewQueryCountTest(_World):
    """GET /api/admin/overview/: the security log is one query, whatever the
    kinds and numbers of lines in it, and no queue costs a count of its own."""
    CEILING = 26

    def fill_the_log(self, n):
        from datetime import timedelta
        from django.utils import timezone
        from activity.models import ActivityLog as Log
        from assistant.models import AssistantJob
        now = timezone.now()
        for i in range(n):
            Log.objects.create(actor=self.staff, action=Log.EXPORTED, category=Log.RECORD,
                               entity_type="Child", entity_label="Exported rows")
            Log.objects.create(actor=None, action=Log.UPDATED, category=Log.SECURITY,
                               entity_type="User",
                               entity_label=f"Login locked for p{i}@x.ph after repeated failures")
            Log.objects.create(actor=None, action=Log.CREATED, category=Log.SECURITY,
                               entity_type="User",
                               entity_label=f"Name{i} — access request (asked for: Staff)")
            Log.objects.create(actor=self.admin, action=Log.UPDATED, category=Log.USER,
                               entity_type="User", entity_id=self.staff.pk,
                               kind=Log.KIND_RESET,
                               entity_label=f"Person {i} — password reset")
            User.objects.create(username=f"pend{self.made}{i}@x.ph",
                                email=f"pend{self.made}{i}@x.ph", status=User.PENDING,
                                is_active=False, first_name=f"Pen{i}", last_name="Ding")
        self.made += n
        Log.objects.filter(category__in=[Log.SECURITY, Log.USER, Log.RECORD]).update(
            created_at=now - timedelta(hours=1))
        for child in Child.objects.all():
            AssistantJob.objects.create(job_type="brief", input_ref=f"child:{child.pk}",
                                        child=child, created_by=self.admin, ok=True)

    def test_the_console_does_not_grow_with_the_log_or_the_queues(self):
        self.add_children(2)
        self.fill_the_log(2)
        url = "/api/admin/overview/"
        few, _ = self.count(self.admin, url)
        self.add_children(4)
        self.fill_the_log(8)
        many, response = self.count(self.admin, url)
        self.assertEqual(few, many, "grew with the rows")
        self.assertLessEqual(many, self.CEILING)
        # The caps still hold with the log full: five of a kind, the rest told.
        log = response.data["security_log"]
        self.assertEqual(sum(1 for e in log if e["kind"] == "lockout"), 5)
        more = {m["kind"]: m["count"] for m in response.data["security_log_more"]}
        self.assertEqual(more["lockout"], 5)
        self.assertEqual(more["request"], 5)
