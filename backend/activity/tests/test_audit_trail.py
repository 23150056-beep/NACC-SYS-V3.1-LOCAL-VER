"""GET /api/activity/trail/ - the audit trail behind "View full audit trail".

It shares the feed's role scope (`_scoped`) and differs in one way: the
per-record transfer lines (kind move_detail) are in it. Dates are never read
from the wall clock: created_at is auto_now_add, so tests set it with update().
"""
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child

User = get_user_model()
TRAIL = "/api/activity/trail/"
FEED = "/api/activity/"


def manila(y, m, d, hh=12, mm=0, ss=0):
    return timezone.make_aware(datetime(y, m, d, hh, mm, ss))


class TrailCase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.rosa = make("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.editha = make("editha@t.ph", "Editha", "Pascua", Role.STAFF)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.ana = Child.objects.create(first_name="Ana", last_name="Cruz",
                                        birth_date=date(2015, 3, 2), social_worker=self.rosa)
        self.eli = Child.objects.create(first_name="Eli", last_name="Tan",
                                        birth_date=date(2015, 3, 2), social_worker=self.editha)

    def log(self, **kw):
        kw.setdefault("actor_label", "Ada Admin")
        kw.setdefault("action", ActivityLog.UPDATED)
        kw.setdefault("category", ActivityLog.RECORD)
        return ActivityLog.objects.create(**kw)

    def at(self, row, when):
        ActivityLog.objects.filter(pk=row.pk).update(created_at=when)
        return row

    def get(self, user, url=TRAIL, **params):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user)
        return client.get(url, params)

    def ids(self, r):
        return [x["id"] for x in r.data["results"]]


class AccessTest(TrailCase):
    def test_requires_authentication(self):
        self.assertEqual(self.get(None).status_code, 401)

    def test_response_shape_and_no_recipient(self):
        self.log(entity_type="Child", entity_id=self.ana.pk, entity_label="C-1")
        r = self.get(self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.data), {"count", "page", "page_size", "results"})
        self.assertNotIn("recipient", r.data["results"][0])
        self.assertNotIn("kind", r.data["results"][0])


class FeedUnchangedTest(TrailCase):
    def test_feed_still_leaves_out_move_detail(self):
        keep = self.log(entity_type="Child", entity_id=self.ana.pk)
        self.log(entity_type="Child", entity_id=self.ana.pk, kind=ActivityLog.KIND_MOVE_DETAIL)
        r = self.get(self.admin, FEED)
        self.assertEqual([x["id"] for x in r.data], [keep.pk])

    def test_feed_still_caps_at_50_and_is_a_plain_list(self):
        for _ in range(55):
            self.log(entity_type="Child", entity_id=self.ana.pk)
        r = self.get(self.admin, FEED)
        self.assertEqual(len(r.data), 50)
        self.assertIsInstance(r.data, list)


class ScopeTest(TrailCase):
    def test_admin_gets_move_detail_but_not_move_copy(self):
        detail = self.log(entity_type="Child", entity_id=self.ana.pk, kind=ActivityLog.KIND_MOVE_DETAIL)
        copy = self.log(kind=ActivityLog.KIND_MOVE_COPY, action=ActivityLog.TRANSFERRED)
        plain = self.log(entity_type="Child", entity_id=self.ana.pk)
        ids = self.ids(self.get(self.admin))
        self.assertIn(detail.pk, ids)
        self.assertIn(plain.pk, ids)
        self.assertNotIn(copy.pk, ids)

    def test_admin_sees_security_and_user_lines(self):
        s = self.log(category=ActivityLog.SECURITY, action=ActivityLog.LOGIN)
        u = self.log(category=ActivityLog.USER)
        self.assertEqual(set(self.ids(self.get(self.admin))), {s.pk, u.pk})

    def test_staff_sees_own_child_lines_including_move_detail(self):
        mine = self.log(entity_type="Child", entity_id=self.ana.pk)
        detail = self.log(entity_type="Child", entity_id=self.ana.pk, kind=ActivityLog.KIND_MOVE_DETAIL)
        case = self.log(entity_type="CaseStudy", entity_id=self.ana.pk)
        asg = self.log(entity_type="Assignment", entity_id=self.ana.pk)
        theirs = self.log(entity_type="Child", entity_id=self.eli.pk)
        theirs_detail = self.log(entity_type="Child", entity_id=self.eli.pk,
                                 kind=ActivityLog.KIND_MOVE_DETAIL)
        ids = self.ids(self.get(self.rosa))
        self.assertEqual(set(ids), {mine.pk, detail.pk, case.pk, asg.pk})
        self.assertNotIn(theirs.pk, ids)
        self.assertNotIn(theirs_detail.pk, ids)

    def test_staff_gets_lines_addressed_to_them(self):
        n = self.log(entity_type="Child", entity_id=self.eli.pk, recipient=self.rosa)
        self.assertIn(n.pk, self.ids(self.get(self.rosa)))

    def test_staff_never_get_security_or_user_lines(self):
        self.log(category=ActivityLog.SECURITY, recipient=self.rosa)
        self.log(category=ActivityLog.USER, recipient=self.rosa)
        for cat in ("security", "user"):
            r = self.get(self.rosa, category=cat)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data["count"], 0)
        self.assertEqual(self.get(self.rosa).data["count"], 0)

    def test_psychologist_sees_only_recipient_lines(self):
        mine = self.log(entity_type="Assignment", entity_id=self.ana.pk, recipient=self.psy)
        self.log(entity_type="Child", entity_id=self.ana.pk)
        self.log(entity_type="Child", entity_id=self.ana.pk, recipient=self.rosa)
        self.assertEqual(self.ids(self.get(self.psy)), [mine.pk])

    def test_child_filter_cannot_widen_staff_scope(self):
        self.log(entity_type="Child", entity_id=self.eli.pk)
        self.assertEqual(self.get(self.rosa, child=self.eli.pk).data["count"], 0)


class FilterTest(TrailCase):
    def test_category(self):
        r1 = self.log()
        self.log(category=ActivityLog.SECURITY)
        self.assertEqual(self.ids(self.get(self.admin, category="record")), [r1.pk])

    def test_action(self):
        a = self.log(action=ActivityLog.EXPORTED)
        self.log(action=ActivityLog.UPDATED)
        self.assertEqual(self.ids(self.get(self.admin, action="exported")), [a.pk])

    def test_q_matches_entity_label_and_actor_label_case_insensitively(self):
        a = self.log(entity_label="Moved C-0042 Ana Cruz")
        b = self.log(actor_label="Rosa SANTOS", entity_label="x")
        self.log(entity_label="other", actor_label="other")
        self.assertEqual(set(self.ids(self.get(self.admin, q="ana cruz"))), {a.pk})
        self.assertEqual(set(self.ids(self.get(self.admin, q="  rosa santos "))), {b.pk})

    def test_child(self):
        a = self.log(entity_type="Child", entity_id=self.ana.pk)
        b = self.log(entity_type="CaseStudy", entity_id=self.ana.pk)
        c = self.log(entity_type="Assignment", entity_id=self.ana.pk)
        self.log(entity_type="User", entity_id=self.ana.pk)
        self.log(entity_type="Child", entity_id=self.eli.pk)
        self.assertEqual(set(self.ids(self.get(self.admin, child=self.ana.pk))), {a.pk, b.pk, c.pk})

    def test_who_moved_this_child(self):
        d = self.log(entity_type="Child", entity_id=self.ana.pk, action=ActivityLog.TRANSFERRED,
                     entity_label="Moved C-1 from A to B", kind=ActivityLog.KIND_MOVE_DETAIL)
        self.assertEqual(self.ids(self.get(self.admin, child=self.ana.pk)), [d.pk])

    def test_date_range_is_inclusive_at_both_ends_in_the_agency_zone(self):
        before = self.at(self.log(), manila(2026, 10, 9, 23, 59, 59))
        first = self.at(self.log(), manila(2026, 10, 10, 0, 0, 0))
        last = self.at(self.log(), manila(2026, 10, 12, 23, 59, 59))
        after = self.at(self.log(), manila(2026, 10, 13, 0, 0, 0))
        r = self.get(self.admin, date_from="2026-10-10", date_to="2026-10-12")
        self.assertEqual(set(self.ids(r)), {first.pk, last.pk})
        self.assertNotIn(before.pk, self.ids(r))
        self.assertNotIn(after.pk, self.ids(r))

    def test_single_ended_ranges(self):
        a = self.at(self.log(), manila(2026, 10, 1))
        b = self.at(self.log(), manila(2026, 10, 20))
        self.assertEqual(self.ids(self.get(self.admin, date_from="2026-10-10")), [b.pk])
        self.assertEqual(self.ids(self.get(self.admin, date_to="2026-10-10")), [a.pk])

    def test_same_day_range(self):
        a = self.at(self.log(), manila(2026, 10, 5, 8))
        self.at(self.log(), manila(2026, 10, 6, 8))
        self.assertEqual(self.ids(self.get(self.admin, date_from="2026-10-05", date_to="2026-10-05")), [a.pk])

    def test_filters_combine(self):
        a = self.log(action=ActivityLog.EXPORTED, entity_label="Records list")
        self.log(action=ActivityLog.UPDATED, entity_label="Records list")
        self.assertEqual(self.ids(self.get(self.admin, action="exported", q="records")), [a.pk])


class RefusalTest(TrailCase):
    def refused(self, field, **params):
        r = self.get(self.admin, **params)
        self.assertEqual(r.status_code, 400, params)
        self.assertIn(field, r.data)

    def test_bad_category(self):
        self.refused("category", category="nope")

    def test_bad_action(self):
        self.refused("action", action="nope")

    def test_bad_child(self):
        self.refused("child", child="abc")
        self.refused("child", child="-1")

    def test_bad_dates(self):
        self.refused("date_from", date_from="10/10/2026")
        self.refused("date_to", date_to="2026-13-40")

    def test_from_after_to(self):
        self.refused("date_from", date_from="2026-10-12", date_to="2026-10-10")

    def test_bad_page_and_size(self):
        self.refused("page", page="0")
        self.refused("page", page="x")
        self.refused("page_size", page_size="0")
        self.refused("page_size", page_size="201")
        self.refused("page_size", page_size="many")

    def test_q_too_long(self):
        self.refused("q", q="x" * 101)

    def test_a_blank_q_or_filter_is_ignored(self):
        self.log()
        self.assertEqual(self.get(self.admin, q="  ", category="").data["count"], 1)


class PagingTest(TrailCase):
    def setUp(self):
        super().setUp()
        base = manila(2026, 10, 1)
        self.rows = []
        for i in range(7):
            self.rows.append(self.at(self.log(), base.replace(minute=i)))

    def newest_first(self):
        return [r.pk for r in reversed(self.rows)]

    def test_defaults(self):
        r = self.get(self.admin)
        self.assertEqual((r.data["count"], r.data["page"], r.data["page_size"]), (7, 1, 50))
        self.assertEqual(self.ids(r), self.newest_first())

    def test_pages(self):
        want = self.newest_first()
        r = self.get(self.admin, page_size=3)
        self.assertEqual(self.ids(r), want[:3])
        self.assertEqual(self.ids(self.get(self.admin, page_size=3, page=2)), want[3:6])
        self.assertEqual(self.ids(self.get(self.admin, page_size=3, page=3)), want[6:])

    def test_page_past_the_end_is_empty_with_the_real_count(self):
        r = self.get(self.admin, page_size=3, page=9)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["results"], [])
        self.assertEqual(r.data["count"], 7)

    def test_page_size_cap_is_allowed_exactly(self):
        self.assertEqual(self.get(self.admin, page_size=200).status_code, 200)

    def test_ties_break_by_id_descending(self):
        t = manila(2026, 11, 1)
        a = self.at(self.log(), t)
        b = self.at(self.log(), t)
        self.assertEqual(self.ids(self.get(self.admin, date_from="2026-11-01"))[:2], [b.pk, a.pk])
