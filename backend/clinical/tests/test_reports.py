from datetime import date

from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child, TerminationRecord
from clinical.models import (
    AgencyFormTemplate, ClinicalInterviewRecord, InstrumentCatalog,
    PreAssessment, ResultEntry, RemarkNote,
)

User = get_user_model()


class ReportsBase(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=self.psy_role)
        self.other = User.objects.create_user(
            email="o@racco1.gov.ph", username="o", password="pass1234", role=self.psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=self.staff_role)
        self.child = Child.objects.create(
            fullname="Ana", case_type="Foster Care", assigned_psychologist=self.psy)
        self.tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)
        pa = PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="completed")
        pa.instruments.add(self.tool)
        ResultEntry.objects.create(
            child=self.child, instrument=self.tool, entered_by=self.psy,
            summary="Findings.", classification="Adjustment difficulties")
        RemarkNote.objects.create(child=self.child, author=self.psy, text="Note.")

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)


class ChildReportTest(ReportsBase):
    def test_child_report_bundles_all_sections(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["child"]["pre_assessment_status"], "Answered")
        self.assertEqual(len(resp.data["pre_assessments"]), 1)
        self.assertEqual(len(resp.data["result_entries"]), 1)
        self.assertEqual(len(resp.data["remarks"]), 1)
        self.assertIn("treatment_plans", resp.data)
        self.assertIn("reports", resp.data)
        self.assertIn("problems", resp.data)

    def test_blocked_for_unassigned_psychologist(self):
        self._auth("o@racco1.gov.ph")
        self.assertEqual(self.client.get(f"/api/reports/child/{self.child.id}/").status_code, 404)

    def test_carry_history_off_hides_others_records(self):
        self.child.assigned_psychologist = self.other
        self.child.assignee_sees_history = False
        self.child.save()
        self._auth("o@racco1.gov.ph")
        resp = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["pre_assessments"]), 0)
        self.assertEqual(len(resp.data["result_entries"]), 0)
        self.assertEqual(len(resp.data["remarks"]), 0)


class MonitoringTest(ReportsBase):
    def test_rows_have_v2_columns(self):
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/monitoring/")
        self.assertEqual(resp.status_code, 200)
        ana = next(r for r in resp.data if r["child_name"] == "Ana")
        self.assertEqual(ana["pre_assessment_status"], "Answered")
        self.assertEqual(ana["latest_classification"], "Adjustment difficulties")
        self.assertIsNotNone(ana["last_activity"])
        self.assertEqual(ana["pre_assessment_count"], 1)
        # The psychologist's most recent note, not just its date: monitoring is
        # read to answer "what is going on with this child right now?".
        self.assertEqual(ana["latest_remark"], "Note.")

    def test_a_child_with_no_remarks_reports_none(self):
        """None, not an empty string — the column renders a dash for it."""
        Child.objects.create(fullname="Quiet", case_type="Adoption",
                             assigned_psychologist=self.psy)
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/monitoring/")
        quiet = next(r for r in resp.data if r["child_name"] == "Quiet")
        self.assertIsNone(quiet["latest_remark"])

    def test_the_latest_remark_wins_not_the_first(self):
        self._auth("a@racco1.gov.ph")
        RemarkNote.objects.create(child=self.child, author=self.psy,
                                  text="Later note.", date="2030-01-01")
        resp = self.client.get("/api/reports/monitoring/")
        ana = next(r for r in resp.data if r["child_name"] == "Ana")
        self.assertEqual(ana["latest_remark"], "Later note.")

    def test_psychologist_scoped(self):
        Child.objects.create(fullname="Ben", assigned_psychologist=self.other)
        self._auth("p@racco1.gov.ph")
        names = [r["child_name"] for r in self.client.get("/api/reports/monitoring/").data]
        self.assertEqual(names, ["Ana"])

    def test_inactive_children_excluded(self):
        self.child.status = Child.INACTIVE
        self.child.save()
        self._auth("a@racco1.gov.ph")
        self.assertEqual(len(self.client.get("/api/reports/monitoring/").data), 0)


class SummaryTest(ReportsBase):
    def test_summary_kpis(self):
        TerminationRecord.objects.create(
            child=self.child, terminated_by=self.psy,
            reason_category="Reunified with family", note="Done.")
        PreAssessment.objects.create(child=self.child, psychologist=self.psy, status="in_progress")
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/summary/?range=monthly")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total"], 1)  # completed only
        self.assertEqual(resp.data["children"], 1)
        self.assertEqual(resp.data["pending_pre_assessments"], 1)
        self.assertEqual(resp.data["terminations_by_reason"], {"Reunified with family": 1})

    def test_summary_forbidden_for_psychologist(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(self.client.get("/api/reports/summary/").status_code, 403)

    def test_summary_csv(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.get("/api/reports/summary/?export=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Completed pre-assessments", resp.content.decode())


class NaccServiceUsersTest(ReportsBase):
    """Point-in-time census block (NACC-SAMD-GF-000 "Service Users" section).
    self.child ("Ana", from ReportsBase) has no birth_date/gender/case_category
    and is active — she should land in the "Unspecified" buckets."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone as tz
        today = tz.localdate()

        def years_ago(n):
            return today.replace(year=today.year - n)

        self.infant = Child.objects.create(
            fullname="Infant One", gender="Male",
            birth_date=years_ago(3), case_category="Abandoned")
        self.middle = Child.objects.create(
            fullname="Middle One", gender="Female",
            birth_date=years_ago(9), case_category="Neglected")
        self.teen = Child.objects.create(
            fullname="Teen One", gender="Male",
            birth_date=years_ago(15), case_category="Abandoned")
        self.adult = Child.objects.create(
            fullname="Adult One", gender="Female",
            birth_date=years_ago(20))
        # Inactive: same profile as the infant, but must be excluded entirely.
        Child.objects.create(
            fullname="Inactive One", gender="Male",
            birth_date=years_ago(5), case_category="Abandoned", status=Child.INACTIVE)

    def test_age_group_math_excludes_inactive(self):
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/summary/")
        self.assertEqual(resp.status_code, 200)
        by_label = {g["label"]: g for g in resp.data["nacc_service_users"]["age_groups"]}
        self.assertEqual(by_label["Infants and Young Children (0-6)"],
                          {"label": "Infants and Young Children (0-6)", "male": 1, "female": 0, "total": 1})
        self.assertEqual(by_label["Middle Childhood (7-11)"],
                          {"label": "Middle Childhood (7-11)", "male": 0, "female": 1, "total": 1})
        self.assertEqual(by_label["Adolescents (12-17)"],
                          {"label": "Adolescents (12-17)", "male": 1, "female": 0, "total": 1})
        self.assertEqual(by_label["Young Adults (18+)"],
                          {"label": "Young Adults (18+)", "male": 0, "female": 1, "total": 1})
        # Ana has no birth_date -> unspecified age; the inactive child never appears.
        self.assertEqual(by_label["Unspecified age"],
                          {"label": "Unspecified age", "male": 0, "female": 0, "total": 1})

    def test_case_category_counts_include_unspecified_bucket(self):
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/summary/")
        cats = {c["label"]: c["count"] for c in resp.data["nacc_service_users"]["case_categories"]}
        self.assertEqual(cats["Abandoned"], 2)  # infant + teen; inactive one excluded
        self.assertEqual(cats["Neglected"], 1)
        self.assertEqual(cats["Unspecified"], 2)  # Ana + Adult One (blank case_category)
        self.assertNotIn("Foundling", cats)  # zero-count categories are omitted

    def test_csv_contains_nacc_service_users_sections(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.get("/api/reports/summary/?export=csv")
        content = resp.content.decode()
        self.assertIn("NACC Service Users by Age Group", content)
        self.assertIn("NACC Service Users by Case Category", content)
        self.assertIn("Infants and Young Children (0-6)", content)
        self.assertIn("Abandoned", content)


class DashboardTest(ReportsBase):
    def test_admin_dashboard(self):
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total_children"], 1)
        self.assertEqual(resp.data["unassessed"], 0)
        self.assertEqual(len(resp.data["trend"]), 1)

    def test_psychologist_scoped_dashboard(self):
        Child.objects.create(fullname="Ben", assigned_psychologist=self.other)
        self._auth("p@racco1.gov.ph")
        resp = self.client.get("/api/reports/dashboard/")
        self.assertEqual(resp.data["total_children"], 1)

    def test_dashboard_range_changes_intake_bucketing(self):
        # The census range selector must re-bucket intake vs termination,
        # not just the pre-assessment trend.
        self._auth("a@racco1.gov.ph")
        yearly = self.client.get("/api/reports/dashboard/?range=yearly")
        buckets = [row["bucket"] for row in yearly.data["intake_vs_termination"]]
        self.assertTrue(buckets and all(len(b) == 4 for b in buckets), buckets)  # "2026"

        quarterly = self.client.get("/api/reports/dashboard/?range=quarterly")
        qbuckets = [row["bucket"] for row in quarterly.data["intake_vs_termination"]]
        self.assertTrue(qbuckets and all("-Q" in b for b in qbuckets), qbuckets)  # "2026-Q3"

    def test_the_today_strip_age_is_a_calendar_age(self):
        # Pinned to noon on 10 Oct 2026, Manila. Days over 365 called a child
        # a day short of ten "10" (3,651 days is 10.003 by that arithmetic and
        # still nine by the calendar); the birthday itself counts.
        from datetime import datetime, time
        from unittest.mock import patch
        from django.utils import timezone
        from scheduling.models import Appointment

        now = timezone.make_aware(datetime(2026, 10, 10, 12, 0))
        eve = Child.objects.create(fullname="Eve", birth_date=date(2016, 10, 11),
                                   assigned_psychologist=self.psy)
        day = Child.objects.create(fullname="Day", birth_date=date(2016, 10, 10),
                                   assigned_psychologist=self.psy)
        leap = Child.objects.create(fullname="Leap", birth_date=date(2012, 2, 29),
                                    assigned_psychologist=self.psy)
        none = Child.objects.create(fullname="None", assigned_psychologist=self.psy)
        for child in (eve, day, leap, none):
            Appointment.objects.create(
                child=child, psychologist=self.psy,
                start=timezone.make_aware(datetime.combine(now.date(), time(15))))
        self._auth("a@racco1.gov.ph")
        with patch("django.utils.timezone.now", return_value=now):
            strip = self.client.get("/api/reports/dashboard/").data["today_schedule"]
        ages = {row["child_id"]: row["age"] for row in strip}
        self.assertEqual(9, ages[eve.id])
        self.assertEqual(10, ages[day.id])
        self.assertEqual(14, ages[leap.id])
        self.assertIsNone(ages[none.id])

    def test_quarterly_bucket_format(self):
        from clinical import reports
        self.assertEqual(reports.bucket(date(2026, 7, 18), "quarterly"), "2026-Q3")
        self.assertEqual(reports.bucket(date(2026, 1, 2), "quarterly"), "2026-Q1")


class ChildReportInterviewsTest(ReportsBase):
    """The chart bundle must list EVERY ClinicalInterviewRecord of the child —
    not just the one linked to a PreAssessment via its `interview` FK."""

    def setUp(self):
        super().setUp()
        self.template = AgencyFormTemplate.objects.create(
            form_type="clinical_interview",
            title="Adoption Pre-Assessment Questionnaire — Child",
            owner=self.psy, attestation=True)
        pa = self.child.pre_assessments.first()
        self.primary = ClinicalInterviewRecord.objects.create(
            child=self.child, respondent="Custodian/PAP", interviewer=self.psy,
            answers={"Reason for adoption": "Kinship adoption, long planned."})
        pa.interview = self.primary
        pa.save()
        # Secondary respondent: exists ONLY as a ClinicalInterviewRecord.
        self.secondary = ClinicalInterviewRecord.objects.create(
            child=self.child, template=self.template, respondent="Child",
            interviewer=self.psy,
            answers={"How do you feel about the family?": "Happy."})

    def test_bundle_lists_all_interviews_not_just_the_linked_one(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual(resp.status_code, 200)
        ids = {i["id"] for i in resp.data["interviews"]}
        self.assertEqual(ids, {self.primary.id, self.secondary.id})

    def test_interview_rows_carry_display_fields(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.get(f"/api/reports/child/{self.child.id}/")
        row = next(i for i in resp.data["interviews"] if i["id"] == self.secondary.id)
        self.assertEqual(row["respondent"], "Child")
        self.assertEqual(row["template_title"],
                         "Adoption Pre-Assessment Questionnaire — Child")
        self.assertEqual(row["answers"], {"How do you feel about the family?": "Happy."})
        self.assertIn("interviewer_name", row)
        self.assertIn("date", row)

    def test_carry_history_off_scopes_interviews_to_own(self):
        self.child.assigned_psychologist = self.other
        self.child.assignee_sees_history = False
        self.child.save()
        mine = ClinicalInterviewRecord.objects.create(
            child=self.child, respondent="Guardian", interviewer=self.other)
        self._auth("o@racco1.gov.ph")
        resp = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual([i["id"] for i in resp.data["interviews"]], [mine.id])
