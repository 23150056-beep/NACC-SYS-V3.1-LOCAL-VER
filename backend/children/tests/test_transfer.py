"""The ISA moves a social worker's records to another social worker, many at a
time (children/transfer.py; `POST /api/children/transfer/`).

Each social worker keeps their own records, and archiving an account moves
nothing, so this is how a leaver's caseload gets to somebody who can open it.
The refusals matter as much as the move: a request that names one record the
first worker does not actively hold moves NOTHING.
"""
from datetime import date, datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, connection
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from activity.services import log_activity
from case_study.models import CaseStudy
from children.models import AssignmentRequest, Child
from children.transfer import MAX_ID, MAX_RECORDS
from scheduling.models import Appointment

User = get_user_model()

URL = "/api/children/transfer/"
LONG_AGO = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)


class TransferCase(TestCase):
    """Rosa holds Ana, Ben and Cara (and Dino, whose case is closed); Editha
    holds Eli; Tomas's account is archived and holds Fe and Gil."""

    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role, **extra: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role], **extra)
        self.editha = make("editha@t.ph", "Editha", "Pascua", Role.STAFF)
        self.rosa = make("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.tomas = make("tomas@t.ph", "Tomas", "Gone", Role.STAFF, status=User.ARCHIVED)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)

        self.ana = self.child("Ana", "Cruz", self.rosa)
        self.ben = self.child("Ben", "Lim", self.rosa)
        self.cara = self.child("Cara", "Diaz", self.rosa)
        self.dino = self.child("Dino", "Reyes", self.rosa, status=Child.INACTIVE)
        self.eli = self.child("Eli", "Tan", self.editha)
        self.fe = self.child("Fe", "Go", self.tomas)
        self.gil = self.child("Gil", "Ko", self.tomas)

    def child(self, first, last, worker, **fields):
        return Child.objects.create(
            first_name=first, last_name=last, birth_date=date(2015, 3, 2),
            social_worker=worker, assigned_psychologist=self.psy, **fields)

    def as_user(self, user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user)
        return client

    def transfer(self, records, source=None, target=None, user=None, **body):
        payload = {"from_social_worker": (source or self.rosa).pk,
                   "to_social_worker": (target or self.editha).pk,
                   "children": [c.pk if isinstance(c, Child) else c for c in records]}
        payload.update(body)
        return self.as_user(user or self.admin).post(URL, payload, format="json")

    def holders(self):
        return dict(Child.objects.values_list("pk", "social_worker_id"))

    def assertNothingMoved(self, before):
        self.assertEqual(before, self.holders())
        self.assertFalse(ActivityLog.objects.filter(action=ActivityLog.TRANSFERRED).exists())


class WhoMayTest(TransferCase):
    def test_social_workers_and_psychologists_are_refused(self):
        before = self.holders()
        for user in (self.rosa, self.editha, self.psy):
            r = self.transfer([self.ana], user=user)
            self.assertEqual(403, r.status_code, user.email)
            self.assertIn("ISA", r.data["detail"])
        self.assertNothingMoved(before)

    def test_the_role_is_checked_before_the_body_is_read(self):
        """Told no whatever they send - not told what is wrong with the body."""
        r = self.as_user(self.rosa).post(URL, {"children": "garbage"}, format="json")
        self.assertEqual(403, r.status_code)

    def test_anonymous_gets_401(self):
        r = self.as_user(None).post(URL, {}, format="json")
        self.assertEqual(401, r.status_code)

    def test_only_post(self):
        self.assertEqual(405, self.as_user(self.admin).get(URL).status_code)

    def test_the_isa_may(self):
        r = self.transfer([self.ana])
        self.assertEqual(200, r.status_code, r.data)


class AccountRulesTest(TransferCase):
    def refused(self, r, field):
        self.assertEqual(400, r.status_code, r.data)
        self.assertIn(field, r.data)
        return r.data[field]

    def test_the_receiving_account_must_be_active(self):
        before = self.holders()
        self.refused(self.transfer([self.ana], target=self.tomas), "to_social_worker")
        self.assertNothingMoved(before)

    def test_a_pending_account_is_not_active_either(self):
        pending = User.objects.create_user(
            email="new@t.ph", username="new", password="x", first_name="New", last_name="Hire",
            role=self.rosa.role, status=User.PENDING)
        before = self.holders()
        self.refused(self.transfer([self.ana], target=pending), "to_social_worker")
        self.assertNothingMoved(before)

    def test_both_must_be_social_workers(self):
        before = self.holders()
        for other in (self.psy, self.admin):
            self.assertEqual("Choose a social worker (SW) account.",
                             self.refused(self.transfer([self.ana], target=other), "to_social_worker"))
            self.assertEqual("Choose a social worker (SW) account.",
                             self.refused(self.transfer([self.ana], source=other), "from_social_worker"))
        self.assertNothingMoved(before)

    def test_an_account_that_does_not_exist(self):
        before = self.holders()
        self.refused(self.transfer([self.ana], target=SimpleNamespace(pk=99999)), "to_social_worker")
        self.refused(self.transfer([self.ana], source=SimpleNamespace(pk=99999)), "from_social_worker")
        self.assertNothingMoved(before)

    def test_not_to_themselves(self):
        before = self.holders()
        self.refused(self.transfer([self.ana], target=self.rosa), "to_social_worker")
        self.assertNothingMoved(before)

    def test_the_account_the_records_leave_may_be_archived(self):
        """The point of the whole thing."""
        r = self.transfer([self.fe, self.gil], source=self.tomas, target=self.rosa)
        self.assertEqual(200, r.status_code, r.data)

    def test_account_ids_must_be_whole_numbers(self):
        before = self.holders()
        for bad in (None, "", "5", 5.0, True, [1], 0, -3, MAX_ID + 1, 10**40):
            self.refused(self.transfer([self.ana], to_social_worker=bad), "to_social_worker")
            self.refused(self.transfer([self.ana], from_social_worker=bad), "from_social_worker")
        self.assertNothingMoved(before)

    def test_a_missing_account_id(self):
        before = self.holders()
        r = self.as_user(self.admin).post(URL, {"children": [self.ana.pk]}, format="json")
        self.refused(r, "from_social_worker")
        r = self.as_user(self.admin).post(
            URL, {"from_social_worker": self.rosa.pk, "children": [self.ana.pk]}, format="json")
        self.refused(r, "to_social_worker")
        self.assertNothingMoved(before)


class ListRulesTest(TransferCase):
    """A request whose list is malformed: 400, nothing moved."""

    def refused(self, r):
        self.assertEqual(400, r.status_code, r.data)
        self.assertIn("children", r.data)
        return r.data["children"]

    def test_the_list_is_required_and_not_empty(self):
        before = self.holders()
        self.refused(self.transfer([]))
        r = self.as_user(self.admin).post(
            URL, {"from_social_worker": self.rosa.pk, "to_social_worker": self.editha.pk},
            format="json")
        self.refused(r)
        for bad in (None, "1,2,3", {"1": 1}, 7, "all"):
            self.refused(self.transfer([], children=bad))
        self.assertNothingMoved(before)

    def test_at_most_a_thousand(self):
        before = self.holders()
        said = self.refused(self.transfer(list(range(1, MAX_RECORDS + 2))))
        self.assertEqual("Move at most 1,000 records at a time.", said)
        self.assertNothingMoved(before)

    def test_exactly_a_thousand_is_not_too_many(self):
        """Ids that do not exist are a 409, which proves the count was let through."""
        r = self.transfer(list(range(5000, 5000 + MAX_RECORDS)))
        self.assertEqual(409, r.status_code)
        self.assertEqual(MAX_RECORDS, len(r.data["problems"]))

    def test_ids_must_be_integers(self):
        before = self.holders()
        for bad in ("5", "abc", 5.0, 5.5, None, True, False, [5], {"id": 5}, 0, -1,
                    MAX_ID + 1, 10**40):
            self.refused(self.transfer([self.ana, bad]))
        self.assertNothingMoved(before)

    def test_a_body_that_is_not_an_object(self):
        before = self.holders()
        for body in ([1, 2], "move them", 5, None):
            r = self.as_user(self.admin).post(URL, body, format="json")
            self.assertEqual(400, r.status_code, body)
        self.assertNothingMoved(before)

    def test_the_same_record_twice_is_refused_not_quietly_merged(self):
        before = self.holders()
        said = self.refused(self.transfer([self.ana, self.ben, self.ana]))
        self.assertEqual(f"C-{self.ana.pk:04d} is listed more than once.", said)
        self.assertNothingMoved(before)


class StaleListTest(TransferCase):
    """A list that was right when the screen drew it and is not now: 409, which
    records, nothing moved."""

    def problems(self, r):
        self.assertEqual(409, r.status_code, r.data)
        return {p["id"]: p for p in r.data["problems"]}

    def test_a_closed_record(self):
        before = self.holders()
        problems = self.problems(self.transfer([self.ana, self.dino]))
        self.assertEqual({self.dino.pk}, set(problems))
        self.assertEqual({"id": self.dino.pk, "ref": f"C-{self.dino.pk:04d}", "why": "closed"},
                         problems[self.dino.pk])
        self.assertNothingMoved(before)

    def test_a_record_the_receiver_already_holds(self):
        before = self.holders()
        problems = self.problems(self.transfer([self.ana, self.eli]))
        self.assertEqual({self.eli.pk}, set(problems))
        self.assertEqual("already with Editha Pascua", problems[self.eli.pk]["why"])
        self.assertNothingMoved(before)

    def test_a_record_held_by_somebody_else_says_who(self):
        before = self.holders()
        problems = self.problems(self.transfer([self.ana, self.fe]))
        self.assertEqual({self.fe.pk}, set(problems))
        self.assertEqual("held by Tomas Gone", problems[self.fe.pk]["why"])
        self.assertNothingMoved(before)

    def test_a_record_nobody_holds(self):
        orphan = Child.objects.create(first_name="Ola", last_name="Nobody",
                                      birth_date=date(2015, 3, 2))
        before = self.holders()
        problems = self.problems(self.transfer([self.ana, orphan]))
        self.assertEqual("has no social worker", problems[orphan.pk]["why"])
        self.assertNothingMoved(before)

    def test_a_record_that_no_longer_exists(self):
        before = self.holders()
        problems = self.problems(self.transfer([self.ana, 424242]))
        self.assertEqual({"id": 424242, "ref": "C-424242", "why": "no longer exists"},
                         problems[424242])
        self.assertNothingMoved(before)

    def test_a_record_already_moved_to_the_receiver(self):
        """A retry after a lost response, or a second tab: told, not doubled."""
        self.assertEqual(200, self.transfer([self.ana, self.ben]).status_code)
        again = self.transfer([self.ana, self.ben])
        problems = self.problems(again)
        self.assertEqual({"already with Editha Pascua"}, {p["why"] for p in problems.values()})
        self.assertEqual(2, ActivityLog.objects.filter(
            action=ActivityLog.TRANSFERRED, entity_type="Child", entity_id__isnull=False).count())

    def test_the_sentence_names_the_case_numbers_and_why(self):
        r = self.transfer([self.dino, self.fe, 424242])
        said = r.data["detail"]
        self.assertEqual(409, r.status_code)
        for needle in (f"C-{self.dino.pk:04d} (closed)", f"C-{self.fe.pk:04d} (held by Tomas Gone)",
                       "C-424242 (no longer exists)", "Nothing was moved"):
            self.assertIn(needle, said)

    def test_the_sentence_does_not_tell_the_screen_to_reload_what_it_already_has(self):
        """The drawer reads the list again on a 409 before it shows this, so
        "reload the list" would send the ISA to do what has been done."""
        r = self.transfer([self.dino])
        self.assertNotIn("reload", r.data["detail"].lower())
        self.assertIn("check the list", r.data["detail"])

    def test_the_sentence_stops_naming_after_ten_and_the_list_has_them_all(self):
        r = self.transfer(list(range(900, 920)))
        self.assertEqual(409, r.status_code)
        self.assertEqual(20, len(r.data["problems"]))
        self.assertIn("and 10 more", r.data["detail"])
        self.assertNotIn("C-0919", r.data["detail"])

    def test_the_whole_list_is_checked_not_the_first_bad_id(self):
        problems = self.problems(self.transfer([self.dino, self.fe, self.ana]))
        self.assertEqual({self.dino.pk, self.fe.pk}, set(problems))

    def test_a_change_between_the_check_and_the_write_undoes_the_move(self):
        """The count of rows updated is compared with the list. If a concurrent
        change made it short, the update already made is rolled back too."""
        before = self.holders()
        real = QuerySet.update

        def one_short(qs, **kwargs):
            return real(qs, **kwargs) - 1

        with patch.object(QuerySet, "update", one_short):
            r = self.transfer([self.ana, self.ben])
        self.assertEqual(409, r.status_code, r.data)
        self.assertIn("Nothing was moved", r.data["detail"])
        self.assertNothingMoved(before)

    def test_lines_that_cannot_be_written_stop_the_move(self):
        """A move with no line in the trail is not made."""
        before = self.holders()
        client = self.as_user(self.admin)
        client.raise_request_exception = False
        with (patch("children.transfer.ActivityLog.objects.bulk_create", side_effect=RuntimeError("down")),
              self.assertLogs("django.request", level="ERROR")):
            r = client.post(URL, {"from_social_worker": self.rosa.pk,
                                  "to_social_worker": self.editha.pk,
                                  "children": [self.ana.pk]}, format="json")
        self.assertEqual(500, r.status_code)
        self.assertNothingMoved(before)


class LostRaceTest(TransferCase):
    """Two overlapping moves at once. PostgreSQL makes the second wait on the
    row locks and then answer 409 (the record is the first's now); SQLite, which
    has no row locks and starts a transaction with a read, gives the second one
    up with "database is locked" the moment it tries to write. Both are the
    same answer for the same reason, and neither moves anything."""

    def lock_error(self, message, sqlstate=None):
        error = OperationalError(message)
        if sqlstate:
            cause = type("PgError", (Exception,), {"sqlstate": sqlstate})()
            error.__cause__ = cause
        return error

    def test_a_locked_database_is_a_409_not_a_500(self):
        before = self.holders()
        with patch.object(QuerySet, "update", side_effect=self.lock_error("database is locked")):
            r = self.transfer([self.ana, self.ben])
        self.assertEqual(409, r.status_code, r.data)
        self.assertIn("Nothing was moved", r.data["detail"])
        self.assertNothingMoved(before)

    def test_a_lock_on_a_table_is_the_same(self):
        before = self.holders()
        with patch.object(QuerySet, "update", side_effect=self.lock_error("database table is locked")):
            r = self.transfer([self.ana])
        self.assertEqual(409, r.status_code, r.data)
        self.assertNothingMoved(before)

    def test_a_deadlock_or_a_serialization_failure_on_postgresql_is_the_same(self):
        for state in ("40P01", "40001"):
            before = self.holders()
            with patch.object(QuerySet, "update",
                              side_effect=self.lock_error("deadlock detected", state)):
                r = self.transfer([self.ana])
            self.assertEqual(409, r.status_code, (state, r.data))
            self.assertNothingMoved(before)

    def test_a_database_that_is_simply_broken_is_still_an_error(self):
        """Not every OperationalError is somebody else's move: a lost connection
        must not read as "the list changed"."""
        before = self.holders()
        client = self.as_user(self.admin)
        client.raise_request_exception = False
        with (patch.object(QuerySet, "update", side_effect=self.lock_error("disk I/O error")),
              self.assertLogs("django.request", level="ERROR")):
            r = client.post(URL, {"from_social_worker": self.rosa.pk,
                                  "to_social_worker": self.editha.pk,
                                  "children": [self.ana.pk]}, format="json")
        self.assertEqual(500, r.status_code)
        self.assertNothingMoved(before)

    def test_a_lock_after_the_update_undoes_it_too(self):
        """The log lines are written after the UPDATE; if they are the ones that
        lose the race the records must not stay moved."""
        before = self.holders()
        with patch("children.transfer.ActivityLog.objects.bulk_create",
                   side_effect=self.lock_error("database is locked")):
            r = self.transfer([self.ana])
        self.assertEqual(409, r.status_code, r.data)
        self.assertNothingMoved(before)

    def test_the_rows_are_locked_in_id_order(self):
        """Two overlapping moves that take their locks in different orders can
        deadlock on PostgreSQL; the same order makes the second one wait."""
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(200, self.transfer([self.cara, self.ana, self.ben]).status_code)
        locking = [q["sql"] for q in ctx if 'FROM "tbl_child"' in q["sql"] and q["sql"].startswith("SELECT")
                   and "social_worker_id" in q["sql"] and "IN (" in q["sql"]]
        self.assertTrue(locking, [q["sql"] for q in ctx])
        self.assertTrue(all('ORDER BY "tbl_child"."id"' in sql for sql in locking), locking)


class MoveTest(TransferCase):
    def test_it_moves_exactly_the_records_listed(self):
        r = self.transfer([self.ana, self.cara])
        self.assertEqual(200, r.status_code, r.data)
        self.assertEqual(
            {"moved": 2, "to": {"id": self.editha.pk, "name": "Editha Pascua"},
             "children": [{"id": self.ana.pk, "ref": f"C-{self.ana.pk:04d}"},
                          {"id": self.cara.pk, "ref": f"C-{self.cara.pk:04d}"}]},
            r.data)
        held = self.holders()
        self.assertEqual(self.editha.pk, held[self.ana.pk])
        self.assertEqual(self.editha.pk, held[self.cara.pk])
        # What was not listed stays with the one who held it - including a
        # closed record, which is the first worker's still.
        self.assertEqual(self.rosa.pk, held[self.ben.pk])
        self.assertEqual(self.rosa.pk, held[self.dino.pk])
        self.assertEqual(self.editha.pk, held[self.eli.pk])
        self.assertEqual(self.tomas.pk, held[self.fe.pk])

    def test_the_answer_lists_them_in_id_order_whatever_the_request_said(self):
        r = self.transfer([self.cara, self.ana, self.ben])
        self.assertEqual([self.ana.pk, self.ben.pk, self.cara.pk], [c["id"] for c in r.data["children"]])

    def test_an_archived_workers_whole_caseload(self):
        r = self.transfer([self.fe, self.gil], source=self.tomas, target=self.rosa)
        self.assertEqual(2, r.data["moved"])
        self.assertEqual({self.rosa.pk}, {self.holders()[self.fe.pk], self.holders()[self.gil.pk]})

    def test_nothing_else_about_the_record_changes(self):
        """Assigned psychologist, an open request to one, the case study and
        the appointments stay exactly as they are."""
        asked = User.objects.create_user(
            email="psy2@t.ph", username="psy2", password="x", first_name="Jose", last_name="Rizal",
            role=self.psy.role)
        request = AssignmentRequest.objects.create(
            child=self.ana, psychologist=asked, requested_by=self.rosa)
        study = CaseStudy.objects.create(child=self.ana, created_by=self.rosa)
        session = Appointment.objects.create(
            child=self.ana, psychologist=self.psy,
            start=datetime(2026, 11, 3, 1, 0, tzinfo=dt_timezone.utc))
        before = Child.objects.values().get(pk=self.ana.pk)

        self.assertEqual(200, self.transfer([self.ana]).status_code)

        after = Child.objects.values().get(pk=self.ana.pk)
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual({"social_worker_id", "updated_at"}, changed)
        self.assertEqual(self.psy.pk, after["assigned_psychologist_id"])
        request.refresh_from_db()
        self.assertEqual((AssignmentRequest.PENDING, self.rosa.pk, asked.pk),
                         (request.status, request.requested_by_id, request.psychologist_id))
        study.refresh_from_db()
        self.assertEqual(self.rosa.pk, study.created_by_id)
        session.refresh_from_db()
        self.assertEqual((self.ana.pk, self.psy.pk), (session.child_id, session.psychologist_id))

    def test_updated_at_moves_so_an_open_edit_form_cannot_save_over_it(self):
        Child.objects.filter(pk=self.ana.pk).update(updated_at=LONG_AGO)
        stale = self.as_user(self.admin).get(f"/api/children/{self.ana.pk}/").data["updated_at"]
        self.assertEqual(200, self.transfer([self.ana]).status_code)
        self.ana.refresh_from_db()
        self.assertGreater(self.ana.updated_at, LONG_AGO)
        r = self.as_user(self.admin).patch(
            f"/api/children/{self.ana.pk}/", {"expected_updated_at": stale}, format="json")
        self.assertEqual(409, r.status_code)


class WhoCanReadItTest(TransferCase):
    """Case study access, the calendar's names and Records all follow
    `social_worker`, so they move with it."""

    def test_the_new_worker_can_read_the_child_and_the_old_one_cannot(self):
        old, new = self.as_user(self.rosa), self.as_user(self.editha)
        url = f"/api/children/{self.ana.pk}/"
        self.assertEqual((200, 404), (old.get(url).status_code, new.get(url).status_code))

        self.assertEqual(200, self.transfer([self.ana]).status_code)

        self.assertEqual((404, 200), (old.get(url).status_code, new.get(url).status_code))
        self.assertNotIn(self.ana.pk, [c["id"] for c in old.get("/api/children/").data])
        self.assertIn(self.ana.pk, [c["id"] for c in new.get("/api/children/").data])
        # Left alone, still the old worker's.
        self.assertEqual(200, old.get(f"/api/children/{self.ben.pk}/").status_code)

    def test_the_case_study_follows_the_record(self):
        CaseStudy.objects.create(child=self.ana, created_by=self.rosa)
        url = f"/api/case-studies/child/{self.ana.pk}/"
        self.assertEqual(200, self.as_user(self.rosa).get(url).status_code)
        self.assertEqual(404, self.as_user(self.editha).get(url).status_code)

        self.assertEqual(200, self.transfer([self.ana]).status_code)

        self.assertEqual(404, self.as_user(self.rosa).get(url).status_code)
        self.assertEqual(200, self.as_user(self.editha).get(url).status_code)


class ActivityTest(TransferCase):
    """The trail has two layers. A line for each record is the audit trail and is
    in no feed (a leaver's caseload is the commonest move there is, and the
    feeds hold 50 lines). A sentence for each worker told is what the feeds
    carry: one to the worker receiving the records and, if that account can
    still sign in, one to the worker who gave them up."""

    def lines(self):
        return list(ActivityLog.objects.filter(action=ActivityLog.TRANSFERRED).order_by("id"))

    def said(self, user):
        feed = self.as_user(user).get("/api/activity/").data
        return [e["entity_label"] for e in feed if e["action"] == "transferred"]

    def test_a_line_per_record_then_a_sentence_for_each_worker_told(self):
        self.transfer([self.ana, self.ben, self.cara])
        lines = self.lines()
        self.assertEqual(5, len(lines))
        per_child, receiving, giving = lines[:3], lines[3], lines[4]
        for line, child in zip(per_child, (self.ana, self.ben, self.cara)):
            self.assertEqual(
                (self.admin, "Ada Admin", ActivityLog.RECORD, "Child", child.pk, None,
                 ActivityLog.KIND_MOVE_DETAIL),
                (line.actor, line.actor_label, line.category, line.entity_type, line.entity_id,
                 line.recipient, line.kind))
            self.assertEqual(
                f"Moved C-{child.pk:04d} · {child.fullname} from Rosa Santos to Editha Pascua",
                line.entity_label)
        sentence = "Moved 3 records from Rosa Santos to Editha Pascua"
        self.assertEqual(
            (self.admin, ActivityLog.RECORD, "Child", None, self.editha, "", sentence),
            (receiving.actor, receiving.category, receiving.entity_type, receiving.entity_id,
             receiving.recipient, receiving.kind, receiving.entity_label))
        self.assertEqual(
            (self.admin, "Child", None, self.rosa, ActivityLog.KIND_MOVE_COPY, sentence),
            (giving.actor, giving.entity_type, giving.entity_id, giving.recipient,
             giving.kind, giving.entity_label))

    def test_one_record_reads_as_one_record(self):
        self.transfer([self.ana])
        self.assertEqual("Moved 1 record from Rosa Santos to Editha Pascua", self.lines()[-1].entity_label)

    def test_an_account_that_cannot_sign_in_is_not_addressed(self):
        self.transfer([self.fe, self.gil], source=self.tomas, target=self.rosa)
        lines = self.lines()
        self.assertEqual([None, None, self.rosa], [line.recipient for line in lines])
        self.assertEqual(0, ActivityLog.objects.filter(recipient=self.tomas).count())

    def test_nothing_is_logged_for_a_refusal(self):
        self.transfer([self.ana, self.dino])
        self.transfer([self.ana], target=self.tomas)
        self.assertEqual([], self.lines())

    def test_a_long_name_still_fits_the_column(self):
        child = Child.objects.create(first_name="Z" * 100, last_name="Y" * 49,
                                     birth_date=date(2015, 3, 2), social_worker=self.rosa)
        User.objects.filter(pk=self.editha.pk).update(first_name="E" * 140, last_name="P" * 140)
        User.objects.filter(pk=self.rosa.pk).update(first_name="R" * 140, last_name="S" * 140)
        self.assertEqual(200, self.transfer([child]).status_code)
        for line in self.lines():
            self.assertLessEqual(len(line.entity_label), 255)

    def test_the_actor_label_is_cut_to_its_column_too(self):
        """SQLite does not enforce a length; PostgreSQL refuses the insert, and
        the move would roll back for the sake of a log line. The ISA's own name
        can be that long (the Users form takes 150 characters of each part)."""
        User.objects.filter(pk=self.admin.pk).update(first_name="A" * 150, last_name="B" * 150)
        self.admin.refresh_from_db()
        self.assertGreater(len(self.admin.fullname), 150)
        self.assertEqual(200, self.transfer([self.ana]).status_code)
        limit = ActivityLog._meta.get_field("actor_label").max_length
        lines = self.lines()
        self.assertEqual(3, len(lines))       # the record, and a sentence for each worker
        for line in lines:
            self.assertLessEqual(len(line.actor_label), limit)
            self.assertTrue(line.actor_label.startswith("AAAA"))

    def test_the_worker_receiving_hears_of_it_once(self):
        self.transfer([self.ana, self.ben])
        self.assertEqual(["Moved 2 records from Rosa Santos to Editha Pascua"], self.said(self.editha))

    def test_the_worker_who_lost_them_hears_of_it_once(self):
        self.transfer([self.ana, self.ben])
        self.assertEqual(["Moved 2 records from Rosa Santos to Editha Pascua"], self.said(self.rosa))

    def test_nobody_else_hears_of_it(self):
        self.transfer([self.ana])
        self.assertEqual([], self.said(self.psy))
        self.assertEqual([], self.said(self.tomas))
        self.assertEqual(0, ActivityLog.objects.filter(recipient=self.tomas).count())

    def test_the_isa_reads_one_sentence_not_the_same_one_twice(self):
        self.transfer([self.ana, self.ben])
        self.assertEqual(["Moved 2 records from Rosa Santos to Editha Pascua"], self.said(self.admin))

    def test_a_big_move_does_not_push_everything_else_out_of_a_feed(self):
        """The commonest move is a leaver's whole caseload. A line a record in
        the feeds would fill the newest 50 with it - for the ISA, for the worker
        receiving, and for the worker leaving - and the assignment answers and
        everything else would be gone from the bell."""
        kids = [self.child(f"K{i}", "Many", self.rosa) for i in range(60)]
        # Something else that has already happened, addressed to each of them.
        for who in (self.admin, self.rosa, self.editha):
            log_activity(self.psy, ActivityLog.ACCEPTED, ActivityLog.RECORD,
                         entity_type="Assignment", entity_label="C-0001 Somebody", recipient=who)
        self.assertEqual(200, self.transfer(kids + [self.ana]).status_code)
        for who in (self.admin, self.rosa, self.editha):
            feed = self.as_user(who).get("/api/activity/").data
            moves = [e for e in feed if e["action"] == "transferred"]
            self.assertEqual(1, len(moves), who.email)
            self.assertTrue(any(e["action"] == "accepted" for e in feed), who.email)
        # The audit trail itself is whole: a line for each of the 61.
        self.assertEqual(61, ActivityLog.objects.filter(
            kind=ActivityLog.KIND_MOVE_DETAIL, entity_id__isnull=False).count())

    def test_the_isas_drawer_keeps_one_sentence_per_move_not_the_whole_trail(self):
        """The Users drawer shows an account's last 25 actions: a move of three
        records would be its first five lines, two of them the same words."""
        self.transfer([self.ana, self.ben, self.cara])
        drawer = self.as_user(self.admin).get(f"/api/users/{self.admin.pk}/activity/").data
        moves = [e["entity_label"] for e in drawer if e["action"] == "transferred"]
        self.assertEqual(["Moved 3 records from Rosa Santos to Editha Pascua"], moves)

    def test_the_trail_for_each_record_is_in_the_database_for_whoever_asks(self):
        """The feeds leave these lines out; they are not deleted."""
        self.transfer([self.ana, self.ben])
        rows = ActivityLog.objects.filter(entity_type="Child", entity_id=self.ana.pk,
                                          action=ActivityLog.TRANSFERRED)
        self.assertEqual(1, rows.count())
        self.assertEqual(f"Moved C-{self.ana.pk:04d} · Ana Cruz from Rosa Santos to Editha Pascua",
                         rows.get().entity_label)


class ConsoleTest(TransferCase):
    def queue(self, key):
        data = self.as_user(self.admin).get("/api/admin/overview/").data
        return next(n for n in data["needs"] if n["key"] == key), data

    def test_moving_an_archived_workers_records_empties_the_queue(self):
        queue, data = self.queue("archived_holder")
        self.assertEqual(2, queue["count"])
        self.assertIn(self.tomas.pk, [w["id"] for w in data["workload"]["social_workers"]])

        self.assertEqual(200, self.transfer([self.fe, self.gil], source=self.tomas,
                                            target=self.rosa).status_code)

        queue, data = self.queue("archived_holder")
        self.assertEqual(0, queue["count"])
        self.assertEqual("No record is held by a deactivated account", queue["hint"])
        # With nothing left to hold, the archived account drops off the workload.
        rows = {w["id"]: w for w in data["workload"]["social_workers"]}
        self.assertNotIn(self.tomas.pk, rows)
        self.assertEqual(5, rows[self.rosa.pk]["active"])      # her three, and these two

    def test_a_partial_move_leaves_the_rest_in_the_queue(self):
        self.transfer([self.fe], source=self.tomas, target=self.rosa)
        queue, _ = self.queue("archived_holder")
        self.assertEqual(1, queue["count"])


class QueryCountTest(TransferCase):
    def count_queries(self, n):
        worker = User.objects.create_user(
            email=f"w{n}@t.ph", username=f"w{n}", password="x", first_name="W", last_name=str(n),
            role=self.rosa.role)
        kids = [self.child(f"K{n}x{i}", "Many", worker) for i in range(n)]
        with CaptureQueriesContext(connection) as ctx:
            r = self.transfer(kids, source=worker, target=self.editha)
        self.assertEqual(200, r.status_code, r.data)
        self.assertEqual(n, r.data["moved"])
        return len(ctx)

    def test_it_does_not_cost_a_query_a_record(self):
        """The two accounts, the lock, one UPDATE and the savepoints around
        them are the same for any number of records. Only the log lines grow,
        and they go in by bulk_create: SQLite splits a large batch to stay under
        its variable limit (about 100 lines a statement), PostgreSQL does not."""
        few, many = self.count_queries(2), self.count_queries(200)
        self.assertLess(few, 12)
        self.assertLessEqual(many, few + 3)

    def test_a_full_thousand_is_a_bounded_number_of_queries(self):
        self.assertLess(self.count_queries(MAX_RECORDS), 30)
