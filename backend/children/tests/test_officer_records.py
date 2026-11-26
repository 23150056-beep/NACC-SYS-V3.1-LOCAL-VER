"""The RACC Officer over every record (WP3 of the Officer plan).

The Officer sees every record - active and closed, whoever holds it, and the
ones nobody holds - and on any of them may do what that record's social worker
may do. It does NOT oversee casework yet (the Social Worker field, Transfer,
closing by outcome, removing a duplicate: WP4), is never IT and never a
psychologist.

This module is the Officer's counterpart of test_own_records.py, which closes
every door to a social worker's colleagues' records: each is OPEN to the
Officer, and the doors that stay shut are asserted at the end.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from children.models import AssignmentRequest, Child, TerminationRecord
from children.tests.payloads import complete
from clinical.models import (AgencyFormTemplate, CaseReferral, OpinionnaireInvite,
                             PsychologicalReport, RemarkNote)

User = get_user_model()
MEDIA = "/tmp/officer-records-test-media"
SW_REFUSAL = "Only an administrator can move a record to another social worker."


@override_settings(MEDIA_ROOT=MEDIA)
class OfficerRecordsCase(TestCase):
    def setUp(self):
        def make(email, first, last, role):
            return User.objects.create_user(
                email=email, username=email.split("@")[0], password="pass12345",
                first_name=first, last_name=last,
                role=Role.objects.get_or_create(role_name=role)[0])
        self.editha = make("editha@t.ph", "Editha", "Pascua", Role.STAFF)
        self.rosa = make("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.psy2 = make("psy2@t.ph", "Jose", "Rizal", Role.PSYCHOLOGIST)
        self.officer = make("officer@t.ph", "Corazon", "Dizon", Role.OFFICER)
        born = timezone.localdate() - timedelta(days=9 * 366)
        self.ana = Child.objects.create(
            first_name="Ana", last_name="Cruz", birth_date=born, social_worker=self.editha,
            assigned_psychologist=self.psy)
        self.ben = Child.objects.create(
            first_name="Ben", last_name="Lim", birth_date=born, social_worker=self.rosa,
            assigned_psychologist=self.psy)
        self.orphan = Child.objects.create(
            first_name="Cara", last_name="Diaz", birth_date=born,
            assigned_psychologist=self.psy)
        self.closed = Child.objects.create(
            first_name="Dino", last_name="Reyes", birth_date=born, social_worker=self.rosa,
            status=Child.INACTIVE, case_status=Child.STAGE_TERMINATED)
        self.refs = {}
        for child in (self.ana, self.ben):
            self.refs[child.pk] = self.refer(child, child.social_worker)

    def refer(self, child, by):
        ref = CaseReferral(child=child, uploaded_by=by, original_filename="referral.pdf")
        ref.file.save("referral.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return ref

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def api(self):
        return self.as_(self.officer)


class EveryDoorIsOpenTest(OfficerRecordsCase):
    """The doors test_own_records closes to a social worker."""

    def test_lists_every_record_active_and_closed(self):
        ids = {c["id"] for c in self.api().get("/api/children/").data}
        self.assertEqual({self.ana.id, self.ben.id, self.orphan.id}, ids)
        every = {c["id"]: c for c in self.api().get(
            "/api/children/", {"include_archived": "true"}).data}
        self.assertIn(self.closed.id, every)
        self.assertEqual("Rosa Santos", every[self.ben.id]["social_worker_name"])
        self.assertIsNone(every[self.orphan.id]["social_worker"])

    def test_opens_edits_and_reads_the_page_of_another_workers_record_and_an_unheld_one(self):
        for child in (self.ben, self.orphan):
            client = self.api()
            self.assertEqual(200, client.get(f"/api/children/{child.id}/").status_code)
            edited = client.patch(f"/api/children/{child.id}/",
                                  {"medical_notes": "Asthma."}, format="json")
            self.assertEqual(200, edited.status_code, edited.data)
            self.assertEqual(200, client.get(f"/api/reports/child/{child.id}/").status_code)
            self.assertEqual(200, client.get(
                f"/api/assistant/brief/child/{child.id}/facts/").status_code)
            self.assertEqual(200, client.post(f"/api/children/{child.id}/presence/").status_code)
        # The holder is exactly as it was.
        self.ben.refresh_from_db()
        self.orphan.refresh_from_db()
        self.assertEqual((self.rosa, None), (self.ben.social_worker, self.orphan.social_worker))
        self.assertEqual("Asthma.", self.ben.medical_notes)

    def test_presence_carries_the_role(self):
        self.api().post(f"/api/children/{self.ben.id}/presence/")
        seen = self.as_(self.rosa).get(f"/api/children/{self.ben.id}/presence/").data
        self.assertEqual([{"name": "Corazon Dizon", "role": "Officer"}], seen["others"])

    def test_reports_referrals_and_the_lists_behind_them(self):
        reports = []
        for child in (self.ben, self.orphan):      # another worker's, and nobody's
            report = PsychologicalReport(child=child, author=self.psy,
                                         original_filename="r.pdf")
            report.file.save("r.pdf", ContentFile(b"%PDF-1.4"), save=True)
            reports.append(report)
        client = self.api()
        self.assertEqual({r.id for r in reports},
                         {r["id"] for r in client.get("/api/report-files/").data})
        for report in reports:
            self.assertEqual(
                200, client.get(f"/api/report-files/{report.id}/download/").status_code)
            self.assertEqual(200, client.get(f"/api/report-files/{report.id}/text/").status_code)
        self.assertEqual({self.ana.id, self.ben.id},
                         {r["child"] for r in client.get("/api/case-referrals/").data})

    def test_the_duplicate_check_gives_every_match_with_its_link_as_the_isa_gets(self):
        for child in (self.ben, self.closed, self.ana, self.orphan):
            res = self.api().get("/api/children/check-duplicate/",
                                 {"first_name": child.first_name, "last_name": child.last_name})
            (match,) = res.data["matches"]
            self.assertTrue(match["yours"], child.first_name)
            self.assertEqual(child.id, match["id"])
            self.assertEqual(str(child.birth_date), str(match["birth_date"]))
            self.assertNotIn("reopenable", match)
        isa = self.as_(self.admin).get("/api/children/check-duplicate/",
                                       {"first_name": "Ben", "last_name": "Lim"}).data
        officer = self.api().get("/api/children/check-duplicate/",
                                 {"first_name": "Ben", "last_name": "Lim"}).data
        self.assertEqual(isa, officer)

    def test_a_second_record_for_a_child_is_refused_and_says_where_the_first_is(self):
        res = self.api().post("/api/children/", complete(
            first_name="Ben", last_name="Lim", birth_date=str(self.ben.birth_date)), format="json")
        self.assertEqual(400, res.status_code, res.data)
        self.assertEqual(1, Child.objects.filter(first_name="Ben", last_name="Lim").count())

    def test_the_dashboard_the_summary_and_the_monitoring_list_are_agency_wide(self):
        self.assertEqual(3, self.api().get("/api/reports/dashboard/").data["census"]["active"])
        self.assertEqual(200, self.api().get("/api/reports/summary/").status_code)
        rows = self.api().get("/api/reports/monitoring/").data
        self.assertEqual({self.ana.id, self.ben.id, self.orphan.id},
                         {r["child_id"] for r in (rows["results"] if isinstance(rows, dict) else rows)})

    def test_the_log_line_for_an_export_is_written(self):
        res = self.api().post("/api/children/exported/", {"rows": 3}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        line = ActivityLog.objects.get(action=ActivityLog.EXPORTED)
        self.assertEqual((ActivityLog.SECURITY, "Corazon Dizon"),
                         (line.category, line.actor_label))

    def test_the_survey_forms_follow_the_social_workers_rule(self):
        shared = AgencyFormTemplate.objects.create(
            title="Shared", form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            fields=[{"label": "How are you?"}])
        mine = AgencyFormTemplate.objects.create(
            title="This childs psychologists", form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            fields=[{"label": "?"}], owner=self.psy)
        private = AgencyFormTemplate.objects.create(
            title="Somebody elses", form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            fields=[{"label": "?"}], owner=self.psy2)
        offered = {t["id"] for t in self.api().get(
            "/api/opinionnaire-invites/templates/", {"child": self.ana.id}).data}
        # Shared and the child's psychologist's; not another psychologist's.
        self.assertEqual({shared.id, mine.id}, offered)
        self.assertNotIn(private.id, offered)
        self.assertEqual(
            offered, {t["id"] for t in self.as_(self.editha).get(
                "/api/opinionnaire-invites/templates/", {"child": self.ana.id}).data})

    def test_starts_a_survey_for_any_child_and_never_with_a_form_that_is_not_for_it(self):
        shared = AgencyFormTemplate.objects.create(
            title="Shared", form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            fields=[{"label": "How are you?"}])
        private = AgencyFormTemplate.objects.create(
            title="Somebody elses", form_type=AgencyFormTemplate.SELF_REPORT_GOV,
            fields=[{"label": "?"}], owner=self.psy2)
        client = self.api()
        for child in (self.ana, self.ben, self.orphan):
            res = client.post("/api/opinionnaire-invites/",
                              {"child": child.id, "template": shared.id}, format="json")
            self.assertEqual(201, res.status_code, (child.first_name, res.data))
        refused = client.post("/api/opinionnaire-invites/",
                              {"child": self.ana.id, "template": private.id}, format="json")
        self.assertEqual(400, refused.status_code, refused.data)
        # And closes one, as a social worker does.
        invite = OpinionnaireInvite.objects.filter(child=self.ben).first()
        self.assertIn(client.delete(f"/api/opinionnaire-invites/{invite.id}/").status_code,
                      (200, 204))


class AddAndEditTest(OfficerRecordsCase):
    def test_a_new_record_without_a_social_worker_belongs_to_nobody(self):
        res = self.api().post("/api/children/", complete(), format="json")
        self.assertEqual(201, res.status_code, res.data)
        made = Child.objects.get(pk=res.data["id"])
        self.assertIsNone(made.social_worker)
        self.assertEqual(Child.ACTIVE, made.status)
        self.assertTrue(ActivityLog.objects.filter(
            action=ActivityLog.CREATED, entity_type="Child", entity_id=made.id).exists())

    def test_a_null_social_worker_is_the_same_as_leaving_the_key_out(self):
        res = self.api().post("/api/children/", complete(social_worker=None), format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertIsNone(Child.objects.get(pk=res.data["id"]).social_worker)
        self.assertIsNone(res.data["social_worker"])

    def test_naming_a_holder_stays_the_oversight_of_wp4(self):
        res = self.api().post(
            "/api/children/", complete(first_name="Dina", social_worker=self.editha.id),
            format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual([SW_REFUSAL], res.data["social_worker"])
        self.assertFalse(Child.objects.filter(first_name="Dina").exists())

    def test_the_social_worker_field_cannot_be_changed_by_an_edit_either_way(self):
        for target in (self.editha.id, None):
            res = self.api().patch(f"/api/children/{self.ben.id}/",
                                   {"social_worker": target}, format="json")
            self.assertEqual(400, res.status_code, res.data)
            self.assertEqual([SW_REFUSAL], res.data["social_worker"])
        # Naming the holder it has is fine: the edit form resends everything.
        same = self.api().patch(f"/api/children/{self.ben.id}/",
                                {"social_worker": self.rosa.id, "medical_notes": "ok"},
                                format="json")
        self.assertEqual(200, same.status_code, same.data)
        self.ben.refresh_from_db()
        self.assertEqual(self.rosa, self.ben.social_worker)

    def test_an_edit_that_names_a_psychologist_asks_and_assigns_nobody(self):
        res = self.api().patch(f"/api/children/{self.ben.id}/",
                               {"psychologist": self.psy2.id}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.ben.refresh_from_db()
        self.assertEqual(self.psy, self.ben.assigned_psychologist)
        pending = AssignmentRequest.objects.get(child=self.ben, status="pending")
        self.assertEqual((self.psy2, self.officer), (pending.psychologist, pending.requested_by))
        self.assertEqual(self.psy2.id, res.data["pending_assignment"]["psychologist"])

    def test_the_custodian_their_number_and_consent_are_recorded(self):
        res = self.api().patch(f"/api/children/{self.ben.id}/", {
            "custodian_name": "Lola Nena", "custodian_contact": "09171234567",
            "custodian_sms_consent": True}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.ben.refresh_from_db()
        self.assertEqual(("Lola Nena", True, self.officer),
                         (self.ben.custodian_name, self.ben.custodian_sms_consent,
                          self.ben.custodian_sms_consent_by))
        self.assertTrue(self.ben.custodian_contact)
        self.assertEqual({"on": True, "status": "Texts on"}, res.data["custodian_texts"])

    def test_files_and_deletes_a_referral_for_a_child_another_worker_holds(self):
        client = self.api()
        for child in (self.ben, self.orphan):
            filed = client.post("/api/case-referrals/", {
                "child": child.id, "file": ContentFile(b"%PDF-1.4", name="referral.pdf")},
                format="multipart")
            self.assertEqual(201, filed.status_code, filed.data)
            gone = client.delete(f"/api/case-referrals/{filed.data['id']}/")
            self.assertIn(gone.status_code, (200, 204))
            self.assertFalse(CaseReferral.objects.filter(pk=filed.data["id"]).exists())
        # The one that was already there is replaced as its social worker would.
        old = self.refs[self.ben.id]
        self.assertIn(client.delete(f"/api/case-referrals/{old.id}/").status_code, (200, 204))


class ReopenTest(OfficerRecordsCase):
    def test_reopens_in_place_and_the_holder_stays(self):
        res = self.api().post(f"/api/children/{self.closed.id}/reopen/")
        self.assertEqual(200, res.status_code, res.data)
        self.closed.refresh_from_db()
        self.assertEqual((Child.ACTIVE, self.rosa), (self.closed.status, self.closed.social_worker))
        self.assertEqual(self.rosa.id, res.data["social_worker"])

    def test_reopens_a_record_nobody_holds_and_it_still_belongs_to_nobody(self):
        Child.objects.filter(pk=self.closed.pk).update(social_worker=None)
        res = self.api().post(f"/api/children/{self.closed.id}/reopen/")
        self.assertEqual(200, res.status_code, res.data)
        self.closed.refresh_from_db()
        self.assertIsNone(self.closed.social_worker)

    def test_an_active_record_is_already_active(self):
        self.assertEqual(400, self.api().post(f"/api/children/{self.ben.id}/reopen/").status_code)

    def test_no_take_over_for_the_officer_even_with_the_intake_name(self):
        """"Reopen it - it becomes yours" is a social worker's, for a closed
        case found at intake. The Officer finds the record the ordinary way and
        the previous holder is never told it left."""
        res = self.api().post(f"/api/children/{self.closed.id}/reopen/",
                              {"first_name": "Dino", "last_name": "Reyes"}, format="json")
        self.assertEqual(200, res.status_code)
        self.closed.refresh_from_db()
        self.assertEqual(self.rosa, self.closed.social_worker)


class StatusIsNotAFormFieldForTheOfficerTest(OfficerRecordsCase):
    """Design note, section 5. The lifecycle branch replaces this for every
    role; until then the Officer, who may save any record, is held to it."""

    WORDS = "A case is closed with Terminate and reopened with Reopen."

    def test_an_edit_cannot_close_a_case(self):
        res = self.api().patch(f"/api/children/{self.ben.id}/",
                               {"status": "inactive"}, format="json")
        self.assertEqual(400, res.status_code, res.data)
        self.assertEqual([self.WORDS], res.data["status"])
        self.ben.refresh_from_db()
        self.assertEqual((Child.ACTIVE, Child.STAGE_PRE_ASSESSMENT),
                         (self.ben.status, self.ben.case_status))
        self.assertFalse(TerminationRecord.objects.exists())

    def test_an_edit_cannot_reopen_a_closed_case_even_with_include_archived(self):
        for method in ("patch", "put"):
            res = getattr(self.api(), method)(
                f"/api/children/{self.closed.id}/?include_archived=true",
                {"status": "active"}, format="json")
            self.assertEqual(400, res.status_code, (method, res.data))
            self.assertEqual([self.WORDS], res.data["status"])
        self.closed.refresh_from_db()
        self.assertEqual(Child.INACTIVE, self.closed.status)

    def test_a_create_that_sends_any_status_but_active_is_refused(self):
        res = self.api().post("/api/children/", complete(status="inactive"), format="json")
        self.assertEqual(400, res.status_code, res.data)
        self.assertEqual(["A new record is always open."], res.data["status"])
        self.assertFalse(Child.objects.filter(first_name="Mika").exists())

    def test_the_records_own_status_sent_back_passes(self):
        sent_back = self.api().patch(
            f"/api/children/{self.ben.id}/", {"status": "active", "medical_notes": "ok"},
            format="json")
        self.assertEqual(200, sent_back.status_code, sent_back.data)
        closed = self.api().patch(
            f"/api/children/{self.closed.id}/?include_archived=true",
            {"status": "inactive", "medical_notes": "still closed"}, format="json")
        self.assertEqual(200, closed.status_code, closed.data)
        created = self.api().post("/api/children/", complete(status="active"), format="json")
        self.assertEqual(201, created.status_code, created.data)

    def test_the_other_roles_are_not_changed_by_it(self):
        """The hole is closed for the Officer only; the lifecycle branch closes
        it for the rest. Pinned so that nobody mistakes this for it.

        WHEN THE LIFECYCLE BRANCH MERGES this test is expected to fail: flip
        every 200 and 201 below to 400 (or delete it) - that is the fix
        arriving, not a regression."""
        for who in (self.rosa, self.admin):
            res = self.as_(who).patch(f"/api/children/{self.ben.id}/",
                                      {"status": "inactive"}, format="json")
            self.assertEqual(200, res.status_code, (who.email, res.data))
            self.ben.refresh_from_db()
            self.assertEqual(Child.INACTIVE, self.ben.status)
            res = self.as_(who).patch(
                f"/api/children/{self.ben.id}/?include_archived=true",
                {"status": "active"}, format="json")
            self.assertEqual(200, res.status_code, (who.email, res.data))
            self.ben.refresh_from_db()
            self.assertEqual(Child.ACTIVE, self.ben.status)
        for who in (self.rosa, self.admin):
            res = self.as_(who).post("/api/children/", complete(
                first_name="Mika", last_name=f"Open{who.pk}", status="inactive"), format="json")
            self.assertEqual(201, res.status_code, (who.email, res.data))


class WhatStaysShutTest(OfficerRecordsCase):
    def test_no_clinical_write(self):
        client = self.api()
        for url in ("remarks", "treatment-plans", "result-entries", "interviews",
                    "pre-assessments", "problems", "consents", "report-files"):
            res = client.post(f"/api/{url}/", {"child": self.ben.id}, format="json")
            self.assertEqual(403, res.status_code, url)
            # Control: the psychologist who holds the child is not stopped at
            # the permission check by the same request (it may be a 400).
            control = self.as_(self.psy).post(f"/api/{url}/", {"child": self.ben.id},
                                              format="json")
            self.assertNotEqual(403, control.status_code, (url, control.data))
        # PATCH, PUT and DELETE of a record that exists are shut too.
        remark = RemarkNote.objects.create(child=self.ben, author=self.psy, text="x")
        for method in ("patch", "put", "delete"):
            res = getattr(client, method)(f"/api/remarks/{remark.id}/", {"text": "y"},
                                          format="json")
            self.assertEqual(403, res.status_code, method)
        remark.refresh_from_db()
        self.assertEqual("x", remark.text)

    def test_no_transfer_terminate_closure_reasons_or_remove_duplicate(self):
        client = self.api()
        self.assertEqual(403, client.post("/api/children/transfer/", {
            "from_social_worker": self.rosa.id, "to_social_worker": self.editha.id,
            "children": [self.ben.id]}, format="json").status_code)
        self.assertEqual(403, client.post(f"/api/children/{self.ben.id}/terminate/", {
            "reason_category": "Services completed", "note": "x"}, format="json").status_code)
        self.assertEqual(403, client.get(
            f"/api/children/{self.ben.id}/closure-reasons/").status_code)
        dup = Child.objects.create(first_name="Ben", last_name="Lim",
                                   birth_date=self.ben.birth_date)
        self.assertEqual(403, client.post(
            f"/api/children/{dup.id}/remove-duplicate/",
            {"duplicate_of": self.ben.id, "case_reference": f"C-{dup.id:04d}"},
            format="json").status_code)
        self.assertTrue(Child.objects.filter(pk=dup.id).exists())
        self.ben.refresh_from_db()
        self.assertEqual((Child.ACTIVE, self.rosa), (self.ben.status, self.ben.social_worker))

    def test_nothing_that_is_it(self):
        client = self.api()
        for url in ("/api/admin/overview/", "/api/users/", "/api/roles/",
                    "/api/users/pending-count/", "/api/assistant/settings/",
                    "/api/assistant/metrics/", "/api/assistant/unanswered/",
                    "/api/assistant/model-health/", "/api/sms-test/",
                    f"/api/assistant/access-log/child/{self.ben.id}/"):
            self.assertEqual(403, client.get(url).status_code, url)
        for url in ("/api/users/", "/api/sms-test/", "/api/email-test/"):
            self.assertEqual(403, client.post(url, {}, format="json").status_code, url)
        self.assertEqual(403, client.patch("/api/agency-profile/", {"name": "x"},
                                           format="json").status_code)
        self.assertEqual(403, client.get("/api/instruments/").status_code)
        self.assertEqual(403, client.get("/api/form-templates/").status_code)

    def test_no_accepting_for_a_psychologist_and_no_psychologists_pre_assessment(self):
        self.assertEqual(403, self.api().get(f"/api/assistant/brief/child/{self.ana.id}/latest/"
                                             ).status_code)
        self.assertEqual(403, self.api().post(
            f"/api/assistant/brief/child/{self.ana.id}/").status_code)
        self.assertEqual(403, self.api().post(
            "/api/assistant/polish-remark/", {"text": "x"}, format="json").status_code)
