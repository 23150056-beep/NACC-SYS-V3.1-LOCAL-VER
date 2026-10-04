from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import AssignmentRequest, Child
from clinical.models import (AgencyFormTemplate, CaseReferral, ConsentRecord,
                             OpinionnaireInvite, PreAssessment,
                             PsychologicalReport, SelfReportFlag)
from clinical.care_gaps import alerts_for, compute_alerts, compute_staff_alerts
from scheduling.models import Appointment

User = get_user_model()


class CareGapTest(APITestCase):
    def setUp(self):
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=self.psy_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)

    def _types_for(self, child):
        return {a["type"] for a in compute_alerts(Child.objects.filter(id=child.id))}

    def test_consent_missing_on_open_pre_assessment(self):
        c = Child.objects.create(fullname="Ana", assigned_psychologist=self.psy)
        PreAssessment.objects.create(child=c, psychologist=self.psy, status="in_progress")
        self.assertIn("consent_missing", self._types_for(c))

    def test_no_consent_alert_when_signed(self):
        c = Child.objects.create(fullname="Ana", assigned_psychologist=self.psy)
        ConsentRecord.objects.create(child=c, status="signed")
        PreAssessment.objects.create(child=c, psychologist=self.psy, status="in_progress")
        self.assertNotIn("consent_missing", self._types_for(c))

    def test_pre_assessment_overdue_after_intake(self):
        c = Child.objects.create(fullname="Ben", assigned_psychologist=self.psy)
        Child.objects.filter(id=c.id).update(
            created_at=timezone.now() - timedelta(days=30))
        self.assertIn("pre_assessment_overdue", self._types_for(c))

    def test_report_missing_after_completed_pre_assessment(self):
        c = Child.objects.create(fullname="Cara", assigned_psychologist=self.psy)
        PreAssessment.objects.create(
            child=c, psychologist=self.psy, status="completed",
            completed_at=timezone.now() - timedelta(days=20))
        self.assertIn("report_missing", self._types_for(c))

    def test_no_report_alert_when_uploaded(self):
        c = Child.objects.create(fullname="Cara", assigned_psychologist=self.psy)
        PreAssessment.objects.create(
            child=c, psychologist=self.psy, status="completed",
            completed_at=timezone.now() - timedelta(days=20))
        PsychologicalReport.objects.create(child=c, author=self.psy, file="reports/x.pdf")
        self.assertNotIn("report_missing", self._types_for(c))

    def test_no_upcoming_appointment_flag(self):
        c = Child.objects.create(fullname="Dan", assigned_psychologist=self.psy)
        self.assertIn("no_upcoming_appointment", self._types_for(c))
        Appointment.objects.create(child=c, psychologist=self.psy,
                                   start=timezone.now() + timedelta(days=3))
        self.assertNotIn("no_upcoming_appointment", self._types_for(c))

    def test_inactive_children_ignored(self):
        c = Child.objects.create(fullname="Zed", assigned_psychologist=self.psy,
                                 status=Child.INACTIVE)
        self.assertEqual(compute_alerts(Child.objects.filter(id=c.id)), [])

    def test_dashboard_returns_census_and_gaps(self):
        Child.objects.create(fullname="Ana", case_type="Adoption",
                             assigned_psychologist=self.psy)
        token = self.client.post("/api/auth/login/", {
            "email": "a@racco1.gov.ph", "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        resp = self.client.get("/api/reports/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["census"]["active"], 1)
        self.assertEqual(resp.data["census"]["by_case_type"], {"Adoption": 1})
        self.assertIn("today_schedule", resp.data)
        self.assertIn("intake_vs_termination", resp.data)
        self.assertTrue(any(g["type"] == "no_upcoming_appointment"
                            for g in resp.data["care_gaps"]))


class SelfReportConcernAlertTest(APITestCase):
    """The alert reads persisted flags. No text analysis runs inside
    compute_alerts — it is called on every page load."""

    def setUp(self):
        from clinical.models import (AgencyFormTemplate, OpinionnaireInvite,
                                     SelfReportFlag)
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="sp@racco1.gov.ph", username="sp", password="pass1234", role=role)
        self.other = User.objects.create_user(
            email="sq@racco1.gov.ph", username="sq", password="pass1234", role=role)
        self.mine = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=self.psy)
        self.theirs = Child.objects.create(
            fullname="Juan Dela Cruz", assigned_psychologist=self.other)
        template = AgencyFormTemplate.objects.create(
            title="Self-report",
            fields=[{"label": "How are you feeling this week?"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.mine, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7))
        self.flag = SelfReportFlag.objects.create(
            invite=invite, child=self.mine,
            question="How are you feeling this week?",
            answer="Lagi akong umiiyak sa gabi.",
            source=SelfReportFlag.LEXICON, matched="umiiyak")

    def _alerts(self, child):
        return compute_alerts(Child.objects.filter(pk=child.pk))

    def _types(self, child):
        return [a["type"] for a in self._alerts(child)]

    def test_an_unreviewed_flag_raises_an_alert(self):
        self.assertIn("self_report_concern", self._types(self.mine))

    def test_the_alert_does_not_quote_the_child(self):
        # The message says a self-report is waiting. The words themselves
        # belong on her page beside the notes, not in a skimmed caseload list.
        alert = [a for a in self._alerts(self.mine)
                 if a["type"] == "self_report_concern"][0]
        self.assertNotIn("umiiyak", alert["message"])
        self.assertEqual("danger", alert["severity"])
        self.assertEqual("Maria Santos", alert["child_name"])

    def test_an_acknowledged_flag_raises_nothing(self):
        self.flag.reviewed_by = self.psy
        self.flag.reviewed_at = timezone.now()
        self.flag.save()
        self.assertNotIn("self_report_concern", self._types(self.mine))

    def test_it_is_scoped_to_the_children_passed_in(self):
        self.assertNotIn("self_report_concern", self._types(self.theirs))


class StaffGapFixture:
    """A social worker's child with nothing wrong: a referral, a psychologist, a
    signed consent and a session still to come. Each test then spoils one thing."""

    def setUp(self):
        super().setUp()
        staff_role = Role.objects.create(role_name=Role.STAFF)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.other_sw = User.objects.create_user(
            email="t@racco1.gov.ph", username="t", password="pass1234", role=staff_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.ana = Child.objects.create(
            fullname="Ana Reyes", social_worker=self.sw, assigned_psychologist=self.psy)
        CaseReferral.objects.create(
            child=self.ana, uploaded_by=self.sw, file="case-referrals/x.pdf")
        ConsentRecord.objects.create(child=self.ana, status=ConsentRecord.SIGNED)
        self.booked = Appointment.objects.create(
            child=self.ana, psychologist=self.psy,
            start=timezone.now() + timedelta(days=3))


class StaffCareGapTest(StaffGapFixture, TestCase):
    def _alerts(self, child=None):
        return compute_staff_alerts(Child.objects.filter(pk=(child or self.ana).pk))

    def _types(self, child=None):
        return {a["type"] for a in self._alerts(child)}

    def test_a_child_with_nothing_wrong_has_no_gaps(self):
        self.assertEqual(self._types(), set())

    def test_no_case_referral(self):
        CaseReferral.objects.filter(child=self.ana).delete()
        self.assertEqual(self._types(), {"no_case_referral"})
        self.assertEqual("danger", self._alerts()[0]["severity"])

    def test_no_psychologist_and_nobody_asked(self):
        Child.objects.filter(pk=self.ana.pk).update(assigned_psychologist=None)
        self.assertEqual(self._types(), {"no_psychologist"})

    def _ask(self, status=AssignmentRequest.PENDING):
        return AssignmentRequest.objects.create(
            child=self.ana, psychologist=self.psy, requested_by=self.sw, status=status)

    def test_a_fresh_request_is_not_a_gap(self):
        Child.objects.filter(pk=self.ana.pk).update(assigned_psychologist=None)
        self._ask()
        self.assertNotIn("no_psychologist", self._types())

    def test_a_request_unanswered_for_a_week_is_a_gap_again(self):
        Child.objects.filter(pk=self.ana.pk).update(assigned_psychologist=None)
        asked = self._ask()
        AssignmentRequest.objects.filter(pk=asked.pk).update(
            created_at=timezone.now() - timedelta(days=8))
        gap = [a for a in self._alerts() if a["type"] == "no_psychologist"]
        self.assertEqual(1, len(gap))
        self.assertIn("asked", gap[0]["message"])

    def test_a_declined_request_is_a_gap_at_once(self):
        Child.objects.filter(pk=self.ana.pk).update(assigned_psychologist=None)
        self._ask(AssignmentRequest.DECLINED)
        self.assertIn("no_psychologist", self._types())

    def test_a_transfer_in_progress_is_not_a_gap(self):
        # Already has a psychologist; a request to move them changes nothing here.
        self._ask()
        self.assertNotIn("no_psychologist", self._types())

    def test_no_signed_consent(self):
        ConsentRecord.objects.filter(child=self.ana).delete()
        self.assertEqual(self._types(), {"no_signed_consent"})
        ConsentRecord.objects.create(child=self.ana, status=ConsentRecord.PENDING)
        self.assertIn("no_signed_consent", self._types())

    def test_a_completed_pre_assessment_stands_in_for_the_consent(self):
        ConsentRecord.objects.filter(child=self.ana).delete()
        PreAssessment.objects.create(
            child=self.ana, psychologist=self.psy, status=PreAssessment.COMPLETED,
            completed_at=timezone.now())
        self.assertNotIn("no_signed_consent", self._types())

    def _invite(self, days_ago, status=OpinionnaireInvite.PENDING):
        template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": "Q"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.ana, template=template, status=status,
            expires_at=timezone.now() + timedelta(days=7))
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago))
        return invite

    def test_no_link_and_a_fresh_link_are_not_gaps(self):
        self.assertNotIn("survey_unanswered", self._types())
        self._invite(0)
        self.assertNotIn("survey_unanswered", self._types())

    def test_an_old_unanswered_link_is_a_gap(self):
        self._invite(8)
        self.assertEqual(self._types(), {"survey_unanswered"})
        self.assertEqual("info", self._alerts()[0]["severity"])

    def test_an_answered_link_is_not_a_gap(self):
        self._invite(8, OpinionnaireInvite.SUBMITTED)
        self.assertNotIn("survey_unanswered", self._types())

    def test_only_the_newest_link_counts(self):
        self._invite(10)
        self._invite(1)
        self.assertNotIn("survey_unanswered", self._types())
        # And the other way round: a newer answered link supersedes an old open one.
        self._invite(0, OpinionnaireInvite.SUBMITTED)
        self.assertNotIn("survey_unanswered", self._types())

    def _flag(self):
        invite = self._invite(0, OpinionnaireInvite.SUBMITTED)
        return SelfReportFlag.objects.create(
            invite=invite, child=self.ana, question="How are you feeling this week?",
            answer="Lagi akong umiiyak sa gabi.",
            source=SelfReportFlag.LEXICON, matched="umiiyak")

    def test_an_unreviewed_self_report_is_kept(self):
        self._flag()
        alert = self._alerts()[0]
        self.assertEqual("self_report_concern", alert["type"])
        self.assertEqual("danger", alert["severity"])
        self.assertNotIn("umiiyak", alert["message"])

    def test_a_reviewed_self_report_is_not_listed(self):
        flag = self._flag()
        flag.reviewed_by = self.sw
        flag.reviewed_at = timezone.now()
        flag.save()
        self.assertNotIn("self_report_concern", self._types())

    # --- the booking gaps stay: booking is a social worker's job ------------

    def test_a_social_worker_keeps_no_upcoming_appointment(self):
        Appointment.objects.filter(pk=self.booked.pk).delete()
        self.assertEqual(self._types(), {"no_upcoming_appointment"})
        self.assertEqual("info", self._alerts()[0]["severity"])

    def test_a_social_worker_keeps_follow_up_overdue(self):
        Appointment.objects.filter(pk=self.booked.pk).delete()
        Appointment.objects.create(
            child=self.ana, psychologist=self.psy, status=Appointment.COMPLETED,
            start=timezone.now() - timedelta(days=40))
        self.assertEqual(self._types(), {"follow_up_overdue", "no_upcoming_appointment"})
        # ...and a session still to come answers both.
        Appointment.objects.create(
            child=self.ana, psychologist=self.psy,
            start=timezone.now() + timedelta(days=2))
        self.assertEqual(self._types(), set())

    def test_the_booking_gaps_are_computed_as_the_psychologists_are(self):
        # One rule, not a copy: the same alerts, word for word, from both lists.
        Appointment.objects.filter(pk=self.booked.pk).delete()
        Appointment.objects.create(
            child=self.ana, psychologist=self.psy, status=Appointment.COMPLETED,
            start=timezone.now() - timedelta(days=40))
        booking = {"follow_up_overdue", "no_upcoming_appointment"}
        theirs = [a for a in self._alerts() if a["type"] in booking]
        clinical = [a for a in compute_alerts(Child.objects.filter(pk=self.ana.pk))
                    if a["type"] in booking]
        self.assertEqual(2, len(theirs))
        self.assertEqual(clinical, theirs)

    # --- and what is not theirs to close is gone ----------------------------

    def test_the_psychologists_clinical_rules_are_absent(self):
        # Everything the psychologist's list would raise for this child: an open
        # pre-assessment with no consent, a stalled intake, a report not filed.
        Appointment.objects.filter(pk=self.booked.pk).delete()
        ConsentRecord.objects.filter(child=self.ana).delete()
        PreAssessment.objects.create(
            child=self.ana, psychologist=self.psy, status=PreAssessment.COMPLETED,
            completed_at=timezone.now() - timedelta(days=20))
        Child.objects.filter(pk=self.ana.pk).update(
            created_at=timezone.now() - timedelta(days=30))
        clinical = {a["type"] for a in compute_alerts(Child.objects.filter(pk=self.ana.pk))}
        self.assertIn("report_missing", clinical)
        self.assertEqual(self._types(), {"no_upcoming_appointment"})
        PreAssessment.objects.all().delete()
        PreAssessment.objects.create(
            child=self.ana, psychologist=self.psy, status=PreAssessment.IN_PROGRESS)
        self.assertNotIn("pre_assessment_overdue", self._types())
        self.assertNotIn("consent_missing", self._types())

    def test_an_inactive_child_has_no_gaps(self):
        CaseReferral.objects.filter(child=self.ana).delete()
        Child.objects.filter(pk=self.ana.pk).update(status=Child.INACTIVE)
        self.assertEqual(self._alerts(), [])

    def test_danger_comes_before_warning_before_info(self):
        # Zed: a warning (no consent). Bea: danger (no referral) and info (nothing booked).
        zed = Child.objects.create(
            fullname="Zed", social_worker=self.sw, assigned_psychologist=self.psy)
        CaseReferral.objects.create(child=zed, uploaded_by=self.sw, file="case-referrals/z.pdf")
        Appointment.objects.create(
            child=zed, psychologist=self.psy, start=timezone.now() + timedelta(days=3))
        bea = Child.objects.create(
            fullname="Bea", social_worker=self.sw, assigned_psychologist=self.psy)
        ConsentRecord.objects.create(child=bea, status=ConsentRecord.SIGNED)
        alerts = compute_staff_alerts(Child.objects.filter(pk__in=[zed.pk, bea.pk]))
        self.assertEqual(
            [("danger", "Bea"), ("warning", "Zed"), ("info", "Bea")],
            [(a["severity"], a["child_name"]) for a in alerts])
        self.assertEqual(
            ["no_case_referral", "no_signed_consent", "no_upcoming_appointment"],
            [a["type"] for a in alerts])


class CareGapRoleTest(StaffGapFixture, APITestCase):
    def setUp(self):
        super().setUp()
        # Another social worker's child, held to the same standard and failing it.
        self.theirs = Child.objects.create(
            fullname="Juan Dela Cruz", social_worker=self.other_sw,
            assigned_psychologist=self.psy)

    def _request(self, user):
        request = APIRequestFactory().get("/")
        request.user = user
        return request

    def _dashboard(self, user):
        self.client.force_authenticate(user)
        res = self.client.get("/api/reports/dashboard/")
        self.assertEqual(res.status_code, 200)
        return res.data["care_gaps"]

    def test_alerts_for_picks_the_rule_set_by_role(self):
        everyone = Child.objects.all()
        self.assertEqual(compute_staff_alerts(everyone),
                         alerts_for(self._request(self.sw), everyone))
        for user in (self.psy, self.admin):
            with self.subTest(user=user.email):
                self.assertEqual(compute_alerts(everyone),
                                 alerts_for(self._request(user), everyone))

    def test_a_social_workers_dashboard_carries_their_gaps(self):
        CaseReferral.objects.filter(child=self.ana).delete()
        gaps = self._dashboard(self.sw)
        mine = {g["type"] for g in gaps if g["child_id"] == self.ana.id}
        self.assertEqual({"no_case_referral"}, mine)
        # Another social worker's child is never named, gap or no gap.
        self.assertNotIn(self.theirs.id, {g["child_id"] for g in gaps})

    def test_a_social_worker_is_not_handed_the_psychologists_gaps(self):
        Appointment.objects.filter(pk=self.booked.pk).delete()
        Child.objects.filter(pk=self.ana.pk).update(
            created_at=timezone.now() - timedelta(days=30))
        types = {g["type"] for g in self._dashboard(self.sw)
                 if g["child_id"] == self.ana.id}
        self.assertEqual({"no_upcoming_appointment"}, types)

    def test_a_psychologists_dashboard_is_unchanged(self):
        Appointment.objects.filter(pk=self.booked.pk).delete()
        types = {g["type"] for g in self._dashboard(self.psy)
                 if g["child_id"] == self.ana.id}
        self.assertIn("no_upcoming_appointment", types)
        self.assertNotIn("no_case_referral", {g["type"] for g in self._dashboard(self.psy)})

    def test_the_isa_still_sees_the_agency_with_the_clinical_rules(self):
        Appointment.objects.filter(pk=self.booked.pk).delete()
        gaps = self._dashboard(self.admin)
        named = {g["child_id"] for g in gaps if g["type"] == "no_upcoming_appointment"}
        self.assertEqual({self.ana.id, self.theirs.id}, named)
        self.assertNotIn("no_case_referral", {g["type"] for g in gaps})
