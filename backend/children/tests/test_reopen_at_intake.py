"""A social worker reopens a closed case found at intake (owner, 30 Sep 2026).

A returning child arrives at whoever runs intake that day, not at the worker
who closed the case. Add Record's duplicate check finds the old record; if it
is TERMINATED the worker may reopen it and it becomes theirs, without asking
the ISA. Only by the name typed at intake, though - by id alone it is still
out of reach, so nobody can collect other workers' closed cases - and an
ACTIVE case held by someone else still goes to the ISA.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child, TerminationRecord

User = get_user_model()


class ReopenAtIntakeTest(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        self.editha = make("editha@t.ph", "Editha", "Pascua", Role.STAFF)
        self.rosa = make("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.born = timezone.localdate() - timedelta(days=10 * 366)
        self.ben = self._closed("Ben", "Lim", self.rosa)

    def _closed(self, first, last, worker):
        child = Child.objects.create(
            first_name=first, last_name=last, birth_date=self.born, social_worker=worker,
            assigned_psychologist=self.psy, status=Child.INACTIVE,
            case_status=Child.STAGE_TERMINATED)
        TerminationRecord.objects.create(child=child, terminated_by=self.psy,
                                         reason_category="Services completed", note="Done.")
        return child

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _reopen(self, user, child, **typed):
        return self._as(user).post(f"/api/children/{child.id}/reopen/", typed, format="json")

    # --- Found at intake --------------------------------------------------------

    def test_the_duplicate_check_offers_another_workers_closed_case(self):
        r = self._as(self.editha).get("/api/children/check-duplicate/",
                                      {"first_name": "ben", "last_name": "LIM"})
        (match,) = r.data["matches"]
        self.assertEqual({"yours": False, "held_by": "Rosa Santos", "id": self.ben.id,
                          "fullname": self.ben.fullname, "status": Child.INACTIVE,
                          "reopenable": True}, match)
        self.assertNotIn(str(self.born), str(r.data))

    def test_an_active_case_is_still_only_who_holds_it(self):
        Child.objects.filter(pk=self.ben.pk).update(status=Child.ACTIVE)
        r = self._as(self.editha).get("/api/children/check-duplicate/",
                                      {"first_name": "Ben", "last_name": "Lim"})
        self.assertEqual([{"yours": False, "held_by": "Rosa Santos"}], r.data["matches"])

    # --- Reopening it -------------------------------------------------------------

    def test_reopened_by_the_typed_name_it_becomes_theirs(self):
        r = self._reopen(self.editha, self.ben, first_name="Ben", last_name="Lim")
        self.assertEqual(200, r.status_code, r.data)
        self.ben.refresh_from_db()
        self.assertEqual(Child.ACTIVE, self.ben.status)
        self.assertEqual(Child.STAGE_PRE_ASSESSMENT, self.ben.case_status)
        self.assertEqual(self.editha, self.ben.social_worker)
        self.assertIsNone(self.ben.assigned_psychologist)
        self.assertEqual(1, self.ben.terminations.count())          # history kept
        # Now in Editha's records, and out of Rosa's - who is told.
        self.assertEqual(200, self._as(self.editha).get(f"/api/children/{self.ben.id}/").status_code)
        self.assertEqual(404, self._as(self.rosa).get(f"/api/children/{self.ben.id}/").status_code)
        told = ActivityLog.objects.filter(entity_id=self.ben.id, recipient=self.rosa)
        self.assertEqual(1, told.count())
        self.assertEqual(self.editha, told.get().actor)

    def test_the_last_name_and_birth_date_will_do(self):
        r = self._reopen(self.editha, self.ben, last_name="Lim", birth_date=str(self.born))
        self.assertEqual(200, r.status_code, r.data)
        self.ben.refresh_from_db()
        self.assertEqual(self.editha, self.ben.social_worker)

    def test_by_id_alone_it_is_out_of_reach(self):
        for typed in ({}, {"last_name": "Lim"}, {"first_name": "Bea", "last_name": "Lim"},
                      {"last_name": "Lim", "birth_date": str(self.born - timedelta(days=1))}):
            r = self._reopen(self.editha, self.ben, **typed)
            self.assertEqual(404, r.status_code, typed)
        self.ben.refresh_from_db()
        self.assertEqual((Child.INACTIVE, self.rosa), (self.ben.status, self.ben.social_worker))

    def test_an_active_case_cannot_be_taken_over(self):
        Child.objects.filter(pk=self.ben.pk).update(status=Child.ACTIVE)
        r = self._reopen(self.editha, self.ben, first_name="Ben", last_name="Lim")
        self.assertEqual(404, r.status_code)
        self.ben.refresh_from_db()
        self.assertEqual(self.rosa, self.ben.social_worker)

    def test_a_closed_case_held_by_nobody_becomes_theirs(self):
        cara = self._closed("Cara", "Diaz", None)
        r = self._reopen(self.editha, cara, first_name="Cara", last_name="Diaz")
        self.assertEqual(200, r.status_code, r.data)
        cara.refresh_from_db()
        self.assertEqual((Child.ACTIVE, self.editha), (cara.status, cara.social_worker))

    def test_their_own_closed_case_reopens_as_before(self):
        mine = self._closed("Ana", "Cruz", self.editha)
        r = self._reopen(self.editha, mine)
        self.assertEqual(200, r.status_code, r.data)
        mine.refresh_from_db()
        self.assertEqual((Child.ACTIVE, self.editha), (mine.status, mine.social_worker))

    def test_the_isa_reopening_moves_nobody(self):
        r = self._reopen(self.admin, self.ben)
        self.assertEqual(200, r.status_code, r.data)
        self.ben.refresh_from_db()
        self.assertEqual((Child.ACTIVE, self.rosa), (self.ben.status, self.ben.social_worker))

    def test_a_psychologist_still_cannot(self):
        for child in (self.ben, self._closed("Dan", "Reyes", self.rosa)):
            Child.objects.filter(pk=child.pk).update(
                assigned_psychologist=self.psy if child is self.ben else None)
            r = self._reopen(self.psy, child, first_name=child.first_name, last_name=child.last_name)
            self.assertIn(r.status_code, (403, 404))
            child.refresh_from_db()
            self.assertEqual(Child.INACTIVE, child.status)
