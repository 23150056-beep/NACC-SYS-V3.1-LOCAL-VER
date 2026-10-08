"""The case brief: facts for a social worker and the ISA, no written brief.

Owner's decision, 8 Oct 2026. The model reads a child's case notes to write a
brief, and neither a social worker nor IT support has a case reason to ask for
that, so the written brief is the child's psychologist's alone. What the others
get is the facts panel with the case's paperwork on it - referral,
psychologist, consent, custodian texts, survey - from plain queries.

Two things are held here. The refusal comes before anything else, so nothing is
sent to a model and no AssistantJob is written for a read that did not happen.
And the facts carry no more than the screens that already show them: no
custodian name or number, no child's words, no summary a person has not
confirmed.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.utils import timezone

from accounts.models import Role
from assistant import services, views
from assistant.models import AssistantJob, AssistantSetting
from assistant.tests.test_brief_facts import FactsFixture
from children.models import AssignmentRequest, Child
from clinical.models import (AgencyFormTemplate, CaseReferral, ConsentRecord,
                             OpinionnaireInvite)

BRIEF = "/api/assistant/brief/child/{}/"
LATEST = "/api/assistant/brief/child/{}/latest/"
PREFETCH = "/api/assistant/prefetch-briefs/"

# A day that is not today, so nothing here reads the wall clock.
NOW = timezone.make_aware(datetime(2026, 10, 8, 10, 0))


class WrittenBriefIsThePsychologistsTest(FactsFixture):
    """Every door to the written brief is shut to everyone else."""

    def setUp(self):
        super().setUp()
        self.refused = (self.sw, self.admin)

    def test_staff_and_administrators_are_refused_at_all_three_doors(self):
        with patch.object(services.OllamaClient, "generate") as generate:
            for user in self.refused:
                self.client.force_authenticate(user)
                for res in (self.client.post(BRIEF.format(self.child.id)),
                            self.client.get(LATEST.format(self.child.id)),
                            self.client.post(PREFETCH)):
                    with self.subTest(user=user.email, url=res.request["PATH_INFO"]):
                        self.assertEqual(res.status_code, 403)
                        self.assertEqual(res.data["detail"], views.WRITTEN_BRIEF_REFUSED)
        # Nothing reached the model, and nothing was audited as a read.
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    def test_the_refusal_is_one_plain_sentence_that_points_at_the_facts(self):
        self.assertEqual(
            views.WRITTEN_BRIEF_REFUSED,
            "The written brief is for the child's psychologist. "
            "The case facts are on the brief panel.")

    def test_it_comes_before_anything_is_looked_up_or_gated(self):
        # Assistant off: a psychologist is told it is unavailable (503), but
        # the others are refused for who they are, whatever the switch says.
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.sw)
        self.assertEqual(self.client.post(BRIEF.format(self.child.id)).status_code, 403)
        # And a child that does not exist, or is another social worker's, is not
        # distinguishable from one that does: the answer is the same 403.
        self.assertEqual(self.client.post(BRIEF.format(99999)).status_code, 403)
        self.assertEqual(
            self.client.post(BRIEF.format(self.other_child.id)).status_code, 403)
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.post(BRIEF.format(self.child.id)).status_code, 503)

    def test_a_brief_drafted_for_someone_is_not_served_to_staff_or_the_isa(self):
        # Even a row that exists (drafted by the psychologist this morning) is
        # not readable through the staff door.
        AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{self.child.id}", ok=True,
            output_text="The psychologist's brief.", created_by=self.psy)
        for user in self.refused:
            self.client.force_authenticate(user)
            res = self.client.get(LATEST.format(self.child.id))
            with self.subTest(user=user.email):
                self.assertEqual(res.status_code, 403)
                self.assertNotIn("psychologist's brief", json.dumps(res.json()))

    def test_the_psychologist_is_unchanged(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Brief."):
            res = self.client.post(BRIEF.format(self.child.id))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], "Brief.")
        self.assertEqual(self.client.get(LATEST.format(self.child.id)).status_code, 200)
        self.assertEqual(AssistantJob.objects.filter(job_type="brief").count(), 1)

    def test_capabilities_say_which_brief_each_role_gets(self):
        expected = {self.psy: "clinical", self.sw: "case", self.admin: "case"}
        for user, kind in expected.items():
            self.client.force_authenticate(user)
            res = self.client.get("/api/assistant/capabilities/")
            with self.subTest(user=user.email):
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.data["brief"], kind)
                # Unchanged beside it.
                self.assertIs(res.data["drafting"], True)

    def test_a_role_it_does_not_know_gets_the_case_brief_not_the_clinical_one(self):
        from assistant.brief_facts import brief_kind
        self.assertEqual(brief_kind(None), "case")
        self.assertEqual(brief_kind(Role.PSYCHOLOGIST), "clinical")


class CaseBriefFactsTest(FactsFixture):
    """What the facts panel holds for a social worker and for the ISA."""

    def _case(self, user=None, child=None):
        res = self._facts(user or self.sw, child)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def _referral(self, **fields):
        return CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="r.pdf",
            extracted_text="Text.", **fields)

    def _unassign(self):
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=None)
        self.child.refresh_from_db()

    # --- who gets it ---------------------------------------------------

    def test_a_social_workers_own_child_returns_the_case_rows(self):
        data = self._case(self.sw)
        self.assertEqual(data["kind"], "case")
        for key in ("case_referral", "psychologist", "consent", "custodian_texts",
                    "survey"):
            self.assertIn(key, data)

    def test_another_social_workers_child_is_404(self):
        self.assertEqual(self._facts(self.sw, self.other_child).status_code, 404)
        self.assertEqual(self._facts(self.other_sw, self.child).status_code, 404)

    def test_the_isa_gets_the_case_brief_too(self):
        data = self._case(self.admin)
        self.assertEqual(data["kind"], "case")
        self.assertIn("case_referral", data)
        self.assertIn("psychologist", data)

    def test_the_psychologist_gets_the_clinical_kind_and_none_of_the_case_rows(self):
        data = self._case(self.psy)
        self.assertEqual(data["kind"], "clinical")
        for key in ("case_referral", "psychologist", "consent", "custodian_texts",
                    "survey"):
            self.assertNotIn(key, data)

    def test_no_job_is_written_for_facts(self):
        for user in (self.sw, self.admin, self.psy):
            self._case(user)
        self.assertFalse(AssistantJob.objects.exists())

    # --- the case referral ----------------------------------------------

    def test_no_referral_is_null(self):
        self.assertIsNone(self._case()["case_referral"])

    def test_a_referral_is_counted_and_dated(self):
        first = self._referral()
        second = self._referral()
        CaseReferral.objects.filter(pk=first.pk).update(
            created_at=NOW - timedelta(days=30))
        CaseReferral.objects.filter(pk=second.pk).update(
            created_at=NOW - timedelta(days=5))
        row = self._case()["case_referral"]
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["latest_uploaded_on"], "2026-10-03")
        self.assertIsNone(row["summary"])
        self.assertIs(row["summary_confirmed"], False)

    def test_a_confirmed_summary_is_returned(self):
        self._referral(ai_summary="Referred after a fire at home.",
                       ai_summary_confirmed=True)
        row = self._case()["case_referral"]
        self.assertEqual(row["summary"], "Referred after a fire at home.")
        self.assertIs(row["summary_confirmed"], True)

    def test_an_unconfirmed_summary_is_never_returned(self):
        self._referral(ai_summary="UNCONFIRMED MODEL DRAFT", ai_summary_confirmed=False)
        for user in (self.sw, self.admin):
            res = self._facts(user)
            with self.subTest(user=user.email):
                self.assertIsNone(res.data["case_referral"]["summary"])
                self.assertIs(res.data["case_referral"]["summary_confirmed"], False)
                self.assertNotIn("UNCONFIRMED MODEL DRAFT", json.dumps(res.json()))

    def test_only_the_latest_referrals_summary_counts(self):
        old = self._referral(ai_summary="OLD CONFIRMED", ai_summary_confirmed=True)
        new = self._referral(ai_summary="NEW DRAFT", ai_summary_confirmed=False)
        CaseReferral.objects.filter(pk=old.pk).update(created_at=NOW - timedelta(days=9))
        CaseReferral.objects.filter(pk=new.pk).update(created_at=NOW - timedelta(days=1))
        row = self._case()["case_referral"]
        self.assertIsNone(row["summary"])
        self.assertEqual(row["count"], 2)

    # --- the psychologist -------------------------------------------------

    def test_an_assigned_psychologist_is_named(self):
        self.psy.first_name, self.psy.last_name = "Mila", "Bulan"
        self.psy.save()
        self.assertEqual(self._case()["psychologist"],
                         {"state": "assigned", "name": "Mila Bulan"})

    def test_nobody_asked_reads_none(self):
        self._unassign()
        self.assertEqual(self._case()["psychologist"], {"state": "none"})

    def test_a_pending_request_reads_asked_with_the_days(self):
        self._unassign()
        self.previous.first_name, self.previous.last_name = "Mila", "Bulan"
        self.previous.save()
        with patch("django.utils.timezone.now", return_value=NOW - timedelta(days=3)):
            AssignmentRequest.objects.create(
                child=self.child, psychologist=self.previous, requested_by=self.sw)
        with patch("django.utils.timezone.now", return_value=NOW):
            row = self._case()["psychologist"]
        self.assertEqual(row, {"state": "asked", "name": "Mila Bulan", "days_ago": 3})

    def test_a_declined_request_gives_the_reason(self):
        self._unassign()
        self.previous.first_name, self.previous.last_name = "Mila", "Bulan"
        self.previous.save()
        AssignmentRequest.objects.create(
            child=self.child, psychologist=self.previous, requested_by=self.sw,
            status=AssignmentRequest.DECLINED, reason="Caseload is full this month.")
        self.assertEqual(
            self._case()["psychologist"],
            {"state": "declined", "name": "Mila Bulan",
             "reason": "Caseload is full this month."})

    def test_a_decline_is_history_once_somebody_else_is_asked(self):
        self._unassign()
        AssignmentRequest.objects.create(
            child=self.child, psychologist=self.previous, requested_by=self.sw,
            status=AssignmentRequest.DECLINED, reason="Full.")
        AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy, requested_by=self.sw)
        self.assertEqual(self._case()["psychologist"]["state"], "asked")

    def test_a_decline_is_history_once_the_child_has_a_psychologist(self):
        AssignmentRequest.objects.create(
            child=self.child, psychologist=self.previous, requested_by=self.sw,
            status=AssignmentRequest.DECLINED, reason="Full.")
        self.assertEqual(self._case()["psychologist"]["state"], "assigned")

    # --- consent -----------------------------------------------------------

    def test_consent_is_null_until_one_is_recorded(self):
        self.assertIsNone(self._case()["consent"])

    def test_the_latest_consent_is_its_status_and_date(self):
        ConsentRecord.objects.create(
            child=self.child, status=ConsentRecord.PENDING, date=datetime(2026, 8, 1).date())
        ConsentRecord.objects.create(
            child=self.child, status=ConsentRecord.SIGNED, date=datetime(2026, 9, 12).date(),
            signer_name="NOT IN THE FACTS")
        row = self._case()["consent"]
        self.assertEqual(row, {"status": "signed", "date": "2026-09-12"})

    # --- the custodian's texts --------------------------------------------

    def test_a_case_type_that_asks_no_custodian_has_no_texts_row(self):
        self.assertIsNone(self._case()["custodian_texts"])

    def test_the_sentence_is_why_texts_are_on_or_off(self):
        Child.objects.filter(pk=self.child.pk).update(case_type="Foster Care")
        self.assertEqual(self._case()["custodian_texts"],
                         "No contact number - texts off")
        Child.objects.filter(pk=self.child.pk).update(custodian_contact="+639171234567")
        self.assertEqual(self._case()["custodian_texts"],
                         "No consent recorded - texts off")
        Child.objects.filter(pk=self.child.pk).update(custodian_sms_consent=True)
        self.assertEqual(self._case()["custodian_texts"],
                         "Number not confirmed - texts off")
        Child.objects.filter(pk=self.child.pk).update(
            custodian_contact_verified_at=NOW)
        self.assertEqual(self._case()["custodian_texts"], "Texts on")

    def test_the_custodians_name_and_number_never_leave(self):
        Child.objects.filter(pk=self.child.pk).update(
            case_type="Foster Care", custodian_name="Lola Cora Dizon",
            custodian_contact="+639171234567", custodian_sms_consent=True,
            custodian_contact_verified_at=NOW)
        for user in (self.sw, self.admin):
            body = json.dumps(self._facts(user).json())
            with self.subTest(user=user.email):
                for leaked in ("Lola", "Cora", "Dizon", "639171234567", "9171234567",
                               "0917"):
                    self.assertNotIn(leaked, body)

    # --- the survey --------------------------------------------------------

    def _invite(self, **fields):
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report",
            fields=[{"label": "Who do you talk to when you are sad?"}])
        return OpinionnaireInvite.objects.create(
            child=self.child, template=template, created_by=self.sw, **fields)

    def test_no_survey_is_null(self):
        self.assertIsNone(self._case()["survey"])

    def test_an_answered_survey_gives_the_date_it_was_answered(self):
        invite = self._invite(
            status=OpinionnaireInvite.SUBMITTED, submitted_at=NOW - timedelta(days=6),
            expires_at=NOW + timedelta(days=1),
            answers={"Who do you talk to when you are sad?": "Nobody"})
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            created_at=NOW - timedelta(days=9))
        with patch("django.utils.timezone.now", return_value=NOW):
            row = self._case()["survey"]
        self.assertEqual(row, {"state": "answered", "date": "2026-10-02"})

    def test_a_link_still_out_is_sent_and_a_lapsed_one_is_expired(self):
        invite = self._invite(status=OpinionnaireInvite.PENDING,
                              expires_at=NOW + timedelta(days=2))
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            created_at=NOW - timedelta(days=5))
        with patch("django.utils.timezone.now", return_value=NOW):
            self.assertEqual(self._case()["survey"], {"state": "sent", "date": "2026-10-03"})
        with patch("django.utils.timezone.now", return_value=NOW + timedelta(days=3)):
            self.assertEqual(self._case()["survey"],
                             {"state": "expired", "date": "2026-10-03"})
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            status=OpinionnaireInvite.EXPIRED)
        with patch("django.utils.timezone.now", return_value=NOW):
            self.assertEqual(self._case()["survey"]["state"], "expired")

    def test_the_newest_link_is_the_one_that_counts(self):
        old = self._invite(status=OpinionnaireInvite.SUBMITTED, submitted_at=NOW,
                           expires_at=NOW + timedelta(days=1))
        new = self._invite(status=OpinionnaireInvite.PENDING,
                           expires_at=NOW + timedelta(days=30))
        OpinionnaireInvite.objects.filter(pk=old.pk).update(
            created_at=NOW - timedelta(days=20))
        OpinionnaireInvite.objects.filter(pk=new.pk).update(
            created_at=NOW - timedelta(days=1))
        with patch("django.utils.timezone.now", return_value=NOW):
            self.assertEqual(self._case()["survey"]["state"], "sent")

    # --- what must not be in it -------------------------------------------

    def test_a_childs_own_words_are_not_in_the_case_brief(self):
        flag = self._flag(answer="Lagi akong umiiyak sa gabi.")
        self._invite(status=OpinionnaireInvite.SUBMITTED, submitted_at=NOW,
                     expires_at=NOW + timedelta(days=1),
                     answers={"Who do you talk to when you are sad?": "Nobody"})
        for user in (self.sw, self.admin):
            res = self._facts(user)
            body = json.dumps(res.json())
            with self.subTest(user=user.email):
                # Counted, never quoted - the number the page's badge shows.
                self.assertEqual(res.data["unreviewed_self_reports"], 1)
                for words in (flag.answer, flag.question, "umiiyak", "Nobody",
                              "Who do you talk to"):
                    self.assertNotIn(words, body)

    def test_the_rows_say_what_the_care_gaps_no_longer_repeat(self):
        # A child with no referral, no psychologist and no consent: each of the
        # three is a row above, so the care-gap list does not say it again.
        self._unassign()
        data = self._case(self.sw)
        self.assertIsNone(data["case_referral"])
        self.assertEqual(data["psychologist"], {"state": "none"})
        self.assertIsNone(data["consent"])
        types = {g["type"] for g in data["care_gaps"]}
        self.assertFalse(types & {"no_case_referral", "no_psychologist",
                                  "no_signed_consent"}, types)
        # The control: the Dashboard still carries them.
        self.client.force_authenticate(self.sw)
        dashboard = {a["type"] for a in
                     self.client.get("/api/reports/dashboard/").data["care_gaps"]
                     if a["child_id"] == self.child.id}
        self.assertTrue({"no_case_referral", "no_psychologist",
                         "no_signed_consent"} <= dashboard, dashboard)

    def test_the_psychologists_care_gaps_are_still_their_dashboards(self):
        # The case brief drops three lines it says in rows; the psychologist's
        # facts have no such rows, so nothing is dropped from theirs.
        self._flag()
        Child.objects.filter(pk=self.child.pk).update(
            created_at=timezone.now() - timedelta(days=30))
        facts = self._case(self.psy)
        self.client.force_authenticate(self.psy)
        dashboard = [a for a in self.client.get("/api/reports/dashboard/").data["care_gaps"]
                     if a["child_id"] == self.child.id]
        self.assertEqual(
            [(g["type"], g["message"]) for g in facts["care_gaps"]],
            [(a["type"], a["message"]) for a in dashboard
             if a["type"] != "self_report_concern"])
        self.assertIn("pre_assessment_overdue", {g["type"] for g in facts["care_gaps"]})
