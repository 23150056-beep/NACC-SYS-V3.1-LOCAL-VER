"""Each social worker keeps their own records (owner's decision, 24 Sep 2026).

A child record belongs to the social worker who added it (`social_worker`).
A staff account sees only the records it holds - not one shared list - and
only an administrator (the ISA) moves a record to someone else. Psychologists
and administrators are unchanged.

The rule lives in accounts/scoping.py, which most endpoints already go
through. These tests are for the doors that did not: every one of them let a
social worker reach a child they do not hold.
"""
from datetime import datetime, time, timedelta
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from activity.services import log_activity
from children.models import Child
from children.tests.payloads import complete
from clinical.models import CaseReferral, PsychologicalReport
from scheduling.models import Appointment, AvailabilityBlock

User = get_user_model()
MEDIA = "/tmp/own-records-test-media"


@override_settings(MEDIA_ROOT=MEDIA)
class OwnRecordsTest(TestCase):
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
        self.ana = Child.objects.create(first_name="Ana", last_name="Cruz",
                                        birth_date=timezone.localdate() - timedelta(days=9 * 366),
                                        assigned_psychologist=self.psy, social_worker=self.editha)
        self.ben = Child.objects.create(first_name="Ben", last_name="Lim",
                                        birth_date=timezone.localdate() - timedelta(days=10 * 366),
                                        assigned_psychologist=self.psy, social_worker=self.rosa)
        self.orphan = Child.objects.create(first_name="Cara", last_name="Diaz",
                                           assigned_psychologist=self.psy)
        for child in (self.ana, self.ben):
            self._refer(child, child.social_worker)

    def _refer(self, child, by):
        ref = CaseReferral(child=child, uploaded_by=by, original_filename="referral.pdf")
        ref.file.save("referral.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return ref

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    # --- Records ------------------------------------------------------------

    def test_a_social_worker_lists_only_their_own_records(self):
        ids = {c["id"] for c in self._as(self.editha).get("/api/children/").data}
        self.assertEqual({self.ana.id}, ids)
        ids = {c["id"] for c in self._as(self.rosa).get("/api/children/").data}
        self.assertEqual({self.ben.id}, ids)

    def test_the_isa_sees_every_record_including_those_with_no_social_worker(self):
        rows = {c["id"]: c for c in self._as(self.admin).get("/api/children/").data}
        self.assertEqual({self.ana.id, self.ben.id, self.orphan.id}, set(rows))
        self.assertEqual("Rosa Santos", rows[self.ben.id]["social_worker_name"])
        self.assertIsNone(rows[self.orphan.id]["social_worker"])

    def test_psychologists_are_unchanged(self):
        ids = {c["id"] for c in self._as(self.psy).get("/api/children/").data}
        self.assertEqual({self.ana.id, self.ben.id, self.orphan.id}, ids)

    def test_another_workers_record_cannot_be_opened_or_edited(self):
        client = self._as(self.editha)
        self.assertEqual(404, client.get(f"/api/children/{self.ben.id}/").status_code)
        self.assertEqual(404, client.patch(f"/api/children/{self.ben.id}/",
                                           {"medical_notes": "x"}, format="json").status_code)
        self.assertEqual(404, client.get(f"/api/reports/child/{self.ben.id}/").status_code)
        self.assertEqual(200, client.get(f"/api/reports/child/{self.ana.id}/").status_code)

    def test_a_new_record_belongs_to_whoever_added_it(self):
        # Even when the request names somebody else.
        r = self._as(self.editha).post("/api/children/", complete(social_worker=self.rosa.id),
                                       format="json")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual(self.editha.id, Child.objects.get(pk=r.data["id"]).social_worker_id)
        self.assertEqual(ActivityLog.CREATED,
                         ActivityLog.objects.filter(entity_type="Child", entity_id=r.data["id"])
                         .values_list("action", flat=True).first())

    def test_only_the_isa_moves_a_record(self):
        refused = self._as(self.editha).patch(
            f"/api/children/{self.ana.id}/", {"social_worker": self.rosa.id}, format="json")
        self.assertEqual(400, refused.status_code)
        self.assertIn("social_worker", refused.data)
        # Sending the one it already has is fine: the edit form resends everything.
        same = self._as(self.editha).patch(
            f"/api/children/{self.ana.id}/", {"social_worker": self.editha.id,
                                              "medical_notes": "ok"}, format="json")
        self.assertEqual(200, same.status_code, same.data)
        moved = self._as(self.admin).patch(
            f"/api/children/{self.ana.id}/", {"social_worker": self.rosa.id}, format="json")
        self.assertEqual(200, moved.status_code, moved.data)
        self.assertEqual({self.ana.id, self.ben.id},
                         {c["id"] for c in self._as(self.rosa).get("/api/children/").data})
        self.assertEqual([], self._as(self.editha).get("/api/children/").data)

    def test_a_record_goes_only_to_a_social_worker(self):
        r = self._as(self.admin).patch(
            f"/api/children/{self.orphan.id}/", {"social_worker": self.psy.id}, format="json")
        self.assertEqual(400, r.status_code)
        ok = self._as(self.admin).patch(
            f"/api/children/{self.orphan.id}/", {"social_worker": self.editha.id}, format="json")
        self.assertEqual(200, ok.status_code, ok.data)

    # --- The duplicate check --------------------------------------------------

    def test_a_duplicate_held_by_another_worker_says_who_and_nothing_else(self):
        r = self._as(self.editha).get("/api/children/check-duplicate/",
                                      {"first_name": "Ben", "last_name": "Lim"})
        self.assertEqual([{"yours": False, "held_by": "Rosa Santos"}], r.data["matches"])
        self.assertNotIn(str(self.ben.id), str(r.data))
        self.assertNotIn(str(self.ben.birth_date), str(r.data))

    def test_a_duplicate_of_your_own_record_is_the_full_row(self):
        r = self._as(self.editha).get("/api/children/check-duplicate/",
                                      {"first_name": "Ana", "last_name": "Cruz"})
        (match,) = r.data["matches"]
        self.assertTrue(match["yours"])
        self.assertEqual(self.ana.id, match["id"])
        # The ISA sees every match in full.
        r = self._as(self.admin).get("/api/children/check-duplicate/",
                                     {"first_name": "Ben", "last_name": "Lim"})
        self.assertTrue(r.data["matches"][0]["yours"])

    # --- Reports, referrals, surveys ----------------------------------------------

    def test_reports_and_referrals_of_another_workers_child_are_out_of_reach(self):
        report = PsychologicalReport(child=self.ben, author=self.psy, original_filename="r.pdf")
        report.file.save("r.pdf", ContentFile(b"%PDF-1.4"), save=True)
        client = self._as(self.editha)
        self.assertEqual([], client.get("/api/report-files/").data)
        self.assertEqual(404, client.get(f"/api/report-files/{report.id}/download/").status_code)
        self.assertEqual(404, client.get(f"/api/report-files/{report.id}/text/").status_code)
        self.assertEqual({self.ana.id}, {r["child"] for r in client.get("/api/case-referrals/").data})
        self.assertEqual(200, self._as(self.rosa).get(
            f"/api/report-files/{report.id}/text/").status_code)

    def test_a_referral_is_filed_only_for_your_own_record(self):
        def upload(child):
            return self._as(self.editha).post("/api/case-referrals/", {
                "child": child.id,
                "file": ContentFile(b"%PDF-1.4", name="referral.pdf")}, format="multipart")
        self.assertEqual(403, upload(self.ben).status_code)
        self.assertEqual(201, upload(self.ana).status_code)

    def test_a_survey_invite_only_for_your_own_record(self):
        from clinical.models import AgencyFormTemplate
        tpl = AgencyFormTemplate.objects.create(
            title="Self-report", form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            fields=[{"label": "How are you?"}])
        client = self._as(self.editha)
        self.assertEqual(403, client.post("/api/opinionnaire-invites/", {
            "child": self.ben.id, "template": tpl.id}, format="json").status_code)
        self.assertEqual(201, client.post("/api/opinionnaire-invites/", {
            "child": self.ana.id, "template": tpl.id}, format="json").status_code)

    # --- The calendar ----------------------------------------------------------------

    def _session(self, child, days=1, hour=9):
        day = timezone.localdate() + timedelta(days=days)
        return Appointment.objects.create(
            child=child, psychologist=self.psy,
            start=timezone.make_aware(datetime.combine(day, time(hour))))

    def test_the_calendar_shows_other_workers_sessions_but_not_to_act_on(self):
        theirs = self._session(self.ben)
        client = self._as(self.editha)
        rows = {a["id"]: a for a in client.get("/api/appointments/").data}
        self.assertTrue(rows[theirs.id]["name_hidden"])
        self.assertEqual(403, client.post(f"/api/appointments/{theirs.id}/cancel/").status_code)
        self.assertEqual(403, client.patch(f"/api/appointments/{theirs.id}/",
                                           {"notes": "moved"}, format="json").status_code)
        theirs.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, theirs.status)
        mine = self._session(self.ana, hour=11)
        self.assertEqual(200, client.post(f"/api/appointments/{mine.id}/cancel/").status_code)

    def test_a_session_is_booked_only_for_your_own_record(self):
        day = timezone.localdate() + timedelta(days=2)
        AvailabilityBlock.objects.create(psychologist=self.psy, weekday=day.weekday(),
                                         start_time=time(8), end_time=time(17), capacity=3)
        payload = {"psychologist": self.psy.id, "start": f"{day.isoformat()}T09:00:00",
                   "duration_minutes": 60, "purpose": "session"}
        client = self._as(self.editha)
        self.assertEqual(403, client.post("/api/appointments/", {**payload, "child": self.ben.id},
                                          format="json").status_code)
        self.assertEqual(201, client.post("/api/appointments/", {**payload, "child": self.ana.id},
                                          format="json").status_code)
        self.assertEqual(404, client.get("/api/availability/slots/", {
            "psychologist": self.psy.id, "date": day.isoformat(), "child": self.ben.id}).status_code)
        self.assertEqual(404, client.get("/api/availability/next-slots/",
                                         {"child": self.ben.id}).status_code)

    # --- Dashboard, feed, summary --------------------------------------------------------

    def test_the_dashboard_counts_their_own_records(self):
        data = self._as(self.editha).get("/api/reports/dashboard/").data
        self.assertEqual(1, data["census"]["active"])
        self.assertEqual(3, self._as(self.admin).get("/api/reports/dashboard/").data["census"]["active"])

    def test_the_agency_summary_stays_agency_wide(self):
        # Counts with no names, mirroring the agency's own report form.
        staff = self._as(self.editha).get("/api/reports/summary/").data
        admin = self._as(self.admin).get("/api/reports/summary/").data
        self.assertEqual(admin["nacc_service_users"], staff["nacc_service_users"])
        self.assertEqual(3, sum(r["total"] for r in staff["nacc_service_users"]["age_groups"]))

    def test_the_activity_feed_shows_only_their_own_records(self):
        for child in (self.ana, self.ben, self.orphan):
            log_activity(self.admin, ActivityLog.UPDATED, ActivityLog.RECORD,
                         entity_type="Child", entity_label=child.fullname, entity_id=child.id)
        log_activity(self.admin, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="Guardian", entity_label="Old guardian row", entity_id=self.ana.id)
        labels = {row["entity_label"] for row in self._as(self.editha).get("/api/activity/").data}
        self.assertIn("Ana Cruz", labels)
        self.assertNotIn("Ben Lim", labels)
        self.assertNotIn("Cara Diaz", labels)
        self.assertNotIn("Old guardian row", labels)


class BackfillTest(TestCase):
    """children 0025: existing records get the owner's best guess."""

    def setUp(self):
        roles = {r: Role.objects.create(role_name=r) for r in (Role.STAFF, Role.ADMINISTRATOR)}
        make = lambda email, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345", role=roles[role])
        self.editha = make("editha@t.ph", Role.STAFF)
        self.rosa = make("rosa@t.ph", Role.STAFF)
        self.admin = make("admin@t.ph", Role.ADMINISTRATOR)
        self.backfill = import_module("children.migrations.0025_backfill_social_worker").backfill

    @override_settings(MEDIA_ROOT=MEDIA)
    def test_referral_first_then_the_creator_and_never_an_administrator(self):
        referred = Child.objects.create(fullname="Referred Child")
        created = Child.objects.create(fullname="Created Child")
        by_admin = Child.objects.create(fullname="Admin Child")
        nothing = Child.objects.create(fullname="Unknown Child")
        kept = Child.objects.create(fullname="Kept Child", social_worker=self.rosa)
        for child, by in ((referred, self.rosa), (referred, self.editha), (by_admin, self.admin),
                          (kept, self.editha)):
            ref = CaseReferral(child=child, uploaded_by=by, original_filename="r.pdf")
            ref.file.save("r.pdf", ContentFile(b"%PDF-1.4"), save=True)
        # The log says Rosa created the referred child; the referral wins.
        log_activity(self.rosa, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label="x", entity_id=referred.id)
        log_activity(self.rosa, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label="x", entity_id=created.id)
        log_activity(self.admin, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label="x", entity_id=by_admin.id)
        before = Child.objects.get(pk=kept.pk).updated_at

        self.backfill(apps, None)

        owner = dict(Child.objects.values_list("pk", "social_worker_id"))
        self.assertEqual(self.editha.id, owner[referred.pk])  # the latest referral
        self.assertEqual(self.rosa.id, owner[created.pk])
        self.assertIsNone(owner[by_admin.pk])
        self.assertIsNone(owner[nothing.pk])
        self.assertEqual(self.rosa.id, owner[kept.pk])  # never moves an assignment
        self.assertEqual(before, Child.objects.get(pk=kept.pk).updated_at)
