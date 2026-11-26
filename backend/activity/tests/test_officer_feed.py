"""What the RACC Officer's bell and audit trail show (WP3 of the Officer plan).

Every line in the RECORD category, on any child and any entity type there - and
NEVER the USER or SECURITY categories, which are IT's, whatever `?category=` is
asked for: the category filter is applied after the scope, so it can only narrow
it. Like the ISA's feed it leaves out the second copy of a move's sentence
(`move_copy`) and the email-limit line; the trail alone adds the per-record
transfer lines (`move_detail`).

A sentinel line of each kind is planted and every response is searched for them.
"""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child

User = get_user_model()
FEED = "/api/activity/"
TRAIL = "/api/activity/trail/"


class OfficerFeedTest(TestCase):
    def setUp(self):
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last,
            role=Role.objects.get_or_create(role_name=role)[0])
        self.officer = make("officer@t.ph", "Corazon", "Dizon", Role.OFFICER)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.rosa = make("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.ana = Child.objects.create(first_name="Ana", last_name="Cruz",
                                        birth_date=date(2015, 3, 2), social_worker=self.rosa)
        self.orphan = Child.objects.create(first_name="Cara", last_name="Diaz",
                                           birth_date=date(2015, 3, 2))
        mk = lambda label, **kw: ActivityLog.objects.create(  # noqa: E731
            actor_label="Ada Admin", action=kw.pop("action", ActivityLog.UPDATED),
            entity_label=label, **kw)
        R, U, S = ActivityLog.RECORD, ActivityLog.USER, ActivityLog.SECURITY
        mk("RECORD-ANA", category=R, entity_type="Child", entity_id=self.ana.id)
        mk("RECORD-UNHELD", category=R, entity_type="Child", entity_id=self.orphan.id)
        mk("RECORD-ASSIGNMENT", category=R, entity_type="Assignment", entity_id=self.ana.id)
        mk("RECORD-APPOINTMENT", category=R, entity_type="Appointment", entity_id=4242)
        mk("RECORD-REFERRAL", category=R, entity_type="CaseReferral", entity_id=77)
        mk("RECORD-ADDRESSED-ELSEWHERE", category=R, entity_type="Child",
           entity_id=self.ana.id, recipient=self.psy)
        mk("USER-SENTINEL", category=U, entity_type="User", entity_id=self.rosa.id,
           action=ActivityLog.CREATED)
        mk("SECURITY-SENTINEL", category=S, entity_type="User")
        mk("SECURITY-EXPORT-SENTINEL", category=S, entity_type="Child",
           action=ActivityLog.EXPORTED)
        mk("COPY-SENTINEL", category=R, entity_type="Child", entity_id=self.ana.id,
           kind=ActivityLog.KIND_MOVE_COPY)
        mk("LIMIT-SENTINEL", category=S, kind=ActivityLog.KIND_EMAIL_LIMIT)
        mk("DETAIL-SENTINEL", category=R, entity_type="Child", entity_id=self.ana.id,
           kind=ActivityLog.KIND_MOVE_DETAIL)

    def labels(self, response, key=None):
        self.assertEqual(200, response.status_code, response.data)
        rows = response.data if key is None else response.data[key]
        return {row["entity_label"] for row in rows}

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    RECORD_LINES = {"RECORD-ANA", "RECORD-UNHELD", "RECORD-ASSIGNMENT", "RECORD-APPOINTMENT",
                    "RECORD-REFERRAL", "RECORD-ADDRESSED-ELSEWHERE"}

    def test_the_feed_is_every_record_line_and_nothing_else(self):
        seen = self.labels(self.as_(self.officer).get(FEED))
        self.assertEqual(self.RECORD_LINES, seen)

    def test_no_category_asked_for_widens_it(self):
        client = self.as_(self.officer)
        for asked in ("security", "user", "record", "nonsense"):
            seen = self.labels(client.get(FEED, {"category": asked}))
            self.assertFalse({x for x in seen if x.endswith("SENTINEL")}, asked)
            self.assertLessEqual(seen, self.RECORD_LINES, asked)
        self.assertEqual(set(), self.labels(client.get(FEED, {"category": "security"})))
        self.assertEqual(set(), self.labels(client.get(FEED, {"category": "user"})))

    def test_the_trail_adds_only_the_per_record_transfer_lines(self):
        client = self.as_(self.officer)
        seen = self.labels(client.get(TRAIL), "results")
        self.assertEqual(self.RECORD_LINES | {"DETAIL-SENTINEL"}, seen)
        for asked in ("security", "user"):
            res = client.get(TRAIL, {"category": asked})
            self.assertEqual(0, res.data["count"], asked)
            self.assertEqual([], res.data["results"])
        # Nothing in any response carries a USER or SECURITY sentinel.
        for url in (FEED, TRAIL):
            for params in ({}, {"category": "security"}, {"q": "SENTINEL"},
                           {"action": "exported"}):
                body = json.dumps(client.get(url, params).data, default=str)
                for planted in ("USER-SENTINEL", "SECURITY-SENTINEL", "SECURITY-EXPORT-SENTINEL",
                                "LIMIT-SENTINEL", "COPY-SENTINEL"):
                    self.assertNotIn(planted, body, (url, params))

    def test_the_trail_filters_by_child_for_the_officer(self):
        res = self.as_(self.officer).get(TRAIL, {"child": self.orphan.id})
        self.assertEqual({"RECORD-UNHELD"}, {r["entity_label"] for r in res.data["results"]})

    def test_the_isa_still_sees_the_other_categories_and_a_social_worker_only_their_own(self):
        isa = self.labels(self.as_(self.admin).get(FEED))
        self.assertIn("USER-SENTINEL", isa)
        self.assertIn("SECURITY-SENTINEL", isa)
        self.assertNotIn("COPY-SENTINEL", isa)
        sw = self.labels(self.as_(self.rosa).get(FEED))
        self.assertIn("RECORD-ANA", sw)
        self.assertNotIn("RECORD-UNHELD", sw)
        self.assertFalse({x for x in sw if x in ("USER-SENTINEL", "SECURITY-SENTINEL")})
