"""get_statistics: counts and breakdowns, and their parity with the screens.

Only numbers a screen already shows, counted the way that screen counts them.
The parity tests are the point: each asks the Dashboard or the Agency Summary
for the same number and fails if the chatbot would answer differently.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.display import display_name
from accounts.models import Role
from assistant import tools
from children.models import Child, TerminationRecord
from clinical.models import PreAssessment
from clinical.reports import AGE_BANDS, UNSPECIFIED_AGE

User = get_user_model()


class StatisticsBase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r) for r in
                 (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}

        def user(email, role, first, last):
            return User.objects.create_user(email=email, username=email,
                                            password="pass1234", role=roles[role],
                                            first_name=first, last_name=last)
        self.admin = user("a@racco1.gov.ph", Role.ADMINISTRATOR, "Ada", "Lim")
        self.staff = user("s@racco1.gov.ph", Role.STAFF, "Sol", "Tan")
        self.psy = user("p@racco1.gov.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        self.other = user("o@racco1.gov.ph", Role.PSYCHOLOGIST, "Oscar", "Cruz")

        today = timezone.localdate()

        def years_ago(n):
            return today.replace(year=today.year - n)

        def child(name, psy, **kw):
            return Child.objects.create(fullname=name, assigned_psychologist=psy, **kw)

        # Pia: two active, one closed. Oscar: one active. One nobody's.
        self.m1 = child("Maria Santos", self.psy, case_type="Foster Care",
                        case_status=Child.STAGE_COUNSELING, gender="Female",
                        birth_date=years_ago(9))
        self.m2 = child("Mario Lopez", self.psy, case_type="Adoption",
                        case_status=Child.STAGE_PRE_ASSESSMENT, gender="Male",
                        birth_date=years_ago(3))
        self.m3 = child("Mila Garcia", self.psy, case_type="Adoption",
                        case_status=Child.STAGE_TERMINATED, status=Child.INACTIVE)
        self.t1 = child("Tomas Reyes", self.other, case_type="Foster Care",
                        case_status=Child.STAGE_COUNSELING, gender="Male",
                        birth_date=years_ago(15))
        self.u1 = child("Una Diaz", None, case_status=Child.STAGE_PRE_ASSESSMENT)

        # Mila's case closed today; Tomas's was closed long ago and reopened.
        TerminationRecord.objects.create(child=self.m3, reason_category="Reunified with family",
                                         note="Home.", date=today)
        TerminationRecord.objects.create(child=self.t1, reason_category="Other",
                                         note="Reopened later.", date=today - timedelta(days=400))

        PreAssessment.objects.create(child=self.m2, psychologist=self.psy)               # pending
        PreAssessment.objects.create(child=self.t1, psychologist=self.other,
                                     status=PreAssessment.IN_PROGRESS)                   # pending
        PreAssessment.objects.create(child=self.m1, psychologist=self.psy,
                                     status=PreAssessment.COMPLETED)                     # done

        self.factory = APIRequestFactory()

    def stats(self, user, **raw):
        call = tools.validate("get_statistics", raw)
        self.assertTrue(call.ok, call.error)
        req = self.factory.get("/")
        req.user = user
        return tools.REGISTRY["get_statistics"]["resolve"](req, call.args)

    @staticmethod
    def counts(out):
        return {r["label"]: r["count"] for r in out["rows"]}


class ScopeTest(StatisticsBase):
    def test_a_psychologist_counts_their_own_caseload(self):
        self.assertEqual(2, self.stats(self.psy)["total"])

    def test_administrators_and_staff_count_the_agency(self):
        self.assertEqual(4, self.stats(self.admin)["total"])
        self.assertEqual(4, self.stats(self.staff)["total"])

    def test_status_is_the_old_count_tools_status(self):
        # count_my_children's three answers, unchanged.
        self.assertEqual(1, self.stats(self.psy, status="terminated")["total"])
        self.assertEqual(3, self.stats(self.psy, status="any")["total"])

    def test_closures_are_scoped_through_the_child(self):
        self.assertEqual(1, self.stats(self.psy, measure="closures")["total"])
        self.assertEqual(2, self.stats(self.admin, measure="closures")["total"])

    def test_pending_pre_assessments_are_scoped_like_the_dashboard(self):
        self.assertEqual(1, self.stats(self.psy, measure="pre_assessments")["total"])
        self.assertEqual(2, self.stats(self.admin, measure="pre_assessments")["total"])


class DefaultsTest(StatisticsBase):
    def test_no_arguments_is_the_old_child_count(self):
        out = self.stats(self.psy)
        self.assertEqual(("children", "active", "none"),
                         (out["measure"], out["status"], out["by"]))
        self.assertEqual([], out["rows"])
        self.assertEqual("2 active children", out["title"])

    def test_every_breakdown_adds_up_to_the_total(self):
        for by in ("case_type", "case_stage", "age_band", "sex", "psychologist"):
            out = self.stats(self.admin, by=by)
            self.assertEqual(out["total"], sum(r["count"] for r in out["rows"]), by)


class ChildBreakdownTest(StatisticsBase):
    def test_by_case_type_most_first_unspecified_last(self):
        rows = self.stats(self.admin, by="case_type")["rows"]
        self.assertEqual([("Foster Care", 2), ("Adoption", 1), ("Unspecified", 1)],
                         [(r["label"], r["count"]) for r in rows])

    def test_by_case_stage_in_the_stage_order(self):
        rows = self.stats(self.admin, by="case_stage")["rows"]
        # Terminated is a stage no active child is in, so it is not listed.
        self.assertEqual([("Pre-Assessment", 2), ("Counseling", 2)],
                         [(r["label"], r["count"]) for r in rows])

    def test_by_age_group_in_the_forms_order_every_band_shown(self):
        rows = self.stats(self.admin, by="age_band")["rows"]
        self.assertEqual([label for label, _, _ in AGE_BANDS] + [UNSPECIFIED_AGE],
                         [r["label"] for r in rows])
        self.assertEqual([1, 1, 1, 0, 1], [r["count"] for r in rows])

    def test_by_sex_counts_only_what_was_recorded(self):
        self.assertEqual({"Male": 2, "Female": 1, "Unspecified": 1},
                         self.counts(self.stats(self.admin, by="sex")))

    def test_by_psychologist_names_the_unassigned(self):
        rows = self.stats(self.admin, by="psychologist")["rows"]
        self.assertEqual([(display_name(self.psy), 2), (display_name(self.other), 1),
                          ("Unassigned", 1)],
                         [(r["label"], r["count"]) for r in rows])


class DatedMeasureTest(StatisticsBase):
    def test_closures_in_a_period(self):
        out = self.stats(self.admin, measure="closures", period="this_year")
        self.assertEqual(1, out["total"])
        self.assertEqual("1 case closed this year", out["title"])

    def test_closures_with_no_period_are_all_time_and_say_so(self):
        out = self.stats(self.admin, measure="closures")
        self.assertEqual("2 cases closed, all time", out["title"])

    def test_closures_by_reason(self):
        self.assertEqual({"Reunified with family": 1, "Other": 1},
                         self.counts(self.stats(self.admin, measure="closures", by="reason")))

    def test_by_month_without_a_period_is_the_last_six_months_oldest_first(self):
        rows = self.stats(self.admin, measure="closures", by="month")["rows"]
        self.assertEqual(6, len(rows))
        self.assertEqual(timezone.localdate().strftime("%b %Y"), rows[-1]["label"])
        self.assertEqual(1, rows[-1]["count"])       # today's closure
        self.assertEqual(0, sum(r["count"] for r in rows[:-1]))

    def test_a_six_month_answer_says_six_months_and_adds_up(self):
        # Regression, caught by an eval dry run: with no period, by-month counts
        # the last six months, and the title said "all time" over it. A child
        # added a year ago is in neither the rows nor the total, so the title
        # was claiming a window the number never covered.
        old = Child.objects.create(fullname="Olga Ramos", assigned_psychologist=self.psy)
        Child.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=365))
        out = self.stats(self.admin, measure="intake", by="month")
        self.assertEqual(out["total"], sum(r["count"] for r in out["rows"]))
        self.assertEqual(5, out["total"])
        self.assertEqual("5 new children added in the last six months, by month",
                         out["title"])
        # Asked without a breakdown, the same measure is all time, and says so.
        self.assertEqual("6 new children added, all time",
                         self.stats(self.admin, measure="intake")["title"])

    def test_intake_by_month_never_runs_past_this_month(self):
        rows = self.stats(self.admin, measure="intake", by="month",
                          period="this_year")["rows"]
        self.assertEqual(timezone.localdate().month, len(rows))
        self.assertEqual(5, rows[-1]["count"])        # every child was added today


class ForgivingTest(StatisticsBase):
    """The routing was right; the question gets its number and a note, never a
    refusal."""

    def test_a_breakdown_the_measure_lacks_answers_with_the_total(self):
        out = self.stats(self.admin, measure="closures", by="case_type")
        self.assertEqual(("none", 2, []), (out["by"], out["total"], out["rows"]))
        self.assertIn("can't be broken down by case type", out["note"])

    def test_a_period_on_a_count_as_of_today_is_explained(self):
        out = self.stats(self.admin, period="this_year")
        self.assertEqual(4, out["total"])
        self.assertIsNone(out["period"])
        self.assertIn("as of today", out["note"])


class ScreenParityTest(StatisticsBase):
    """The same number from the screen and from the chatbot, for one caller."""

    def _screen(self, user, path):
        client = APIClient()
        client.force_authenticate(user)
        res = client.get(path)
        self.assertEqual(200, res.status_code)
        return res.data

    def test_case_type_matches_the_dashboard_census(self):
        census = self._screen(self.admin, "/api/reports/dashboard/")["census"]
        self.assertEqual(census["by_case_type"],
                         self.counts(self.stats(self.admin, by="case_type")))

    def test_case_stage_matches_the_dashboard_census(self):
        census = self._screen(self.admin, "/api/reports/dashboard/")["census"]
        labels = dict(Child.CASE_STATUS_CHOICES)
        self.assertEqual({labels[k]: v for k, v in census["by_case_status"].items()},
                         self.counts(self.stats(self.admin, by="case_stage")))

    def test_age_group_and_sex_match_the_agency_summary(self):
        groups = self._screen(self.admin, "/api/reports/summary/")[
            "nacc_service_users"]["age_groups"]
        self.assertEqual({g["label"]: g["total"] for g in groups},
                         {label: n for label, n in
                          self.counts(self.stats(self.admin, by="age_band")).items() if n
                          or label != UNSPECIFIED_AGE})
        sex = self.counts(self.stats(self.admin, by="sex"))
        self.assertEqual(sum(g["male"] for g in groups), sex["Male"])
        self.assertEqual(sum(g["female"] for g in groups), sex["Female"])

    def test_caseload_per_psychologist_matches_the_agency_summary(self):
        rows = self._screen(self.admin, "/api/reports/summary/")["caseload_per_psychologist"]
        mine = self.counts(self.stats(self.admin, by="psychologist"))
        mine.pop("Unassigned")     # the Summary's table leaves unassigned children out
        self.assertEqual({r["name"]: r["caseload"] for r in rows}, mine)

    def test_closure_reasons_match_the_agency_summary(self):
        summary = self._screen(self.admin, "/api/reports/summary/")
        self.assertEqual(summary["terminations_by_reason"],
                         self.counts(self.stats(self.admin, measure="closures", by="reason")))

    def test_pending_pre_assessments_match_each_callers_dashboard(self):
        for user in (self.admin, self.psy):
            dash = self._screen(user, "/api/reports/dashboard/")
            self.assertEqual(dash["pending_pre_assessments"],
                             self.stats(user, measure="pre_assessments")["total"])


class ScreenLinkTest(StatisticsBase):
    def test_the_summary_is_linked_only_for_those_who_can_open_it(self):
        self.assertEqual("/reports/summary", self.stats(self.admin, by="age_band")["screen"]["path"])
        self.assertEqual("/reports/summary", self.stats(self.staff, by="psychologist")["screen"]["path"])
        # A psychologist cannot open the Agency Summary. No link beats a dead one.
        self.assertIsNone(self.stats(self.psy, by="age_band")["screen"])

    def test_dashboard_numbers_link_the_dashboard_for_everyone(self):
        for user in (self.admin, self.psy):
            self.assertEqual("/", self.stats(user, by="case_type")["screen"]["path"])
            self.assertEqual("/", self.stats(user, measure="pre_assessments")["screen"]["path"])


class WordingTest(StatisticsBase):
    def test_singular_and_plural(self):
        self.assertEqual("1 pending pre-assessment",
                         self.stats(self.psy, measure="pre_assessments")["title"])
        self.assertEqual("2 pending pre-assessments",
                         self.stats(self.admin, measure="pre_assessments")["title"])

    def test_the_title_is_the_total_then_the_subject(self):
        out = self.stats(self.admin, measure="closures", by="reason", period="this_year")
        self.assertEqual("1 case closed this year, by reason", out["title"])
        self.assertEqual(f"{out['total']} {out['subject']}", out["title"])


class StatisticsSizeAndEchoTest(SimpleTestCase):
    def test_the_logged_size_is_the_total(self):
        self.assertEqual(0, tools.result_size({"kind": "breakdown", "total": 0, "rows": []}))
        self.assertEqual(7, tools.result_size(
            {"kind": "breakdown", "total": 7, "rows": [{"label": "x", "count": 7}]}))

    def test_the_echo_says_what_was_understood(self):
        echo = tools.REGISTRY["get_statistics"]["echo"]
        self.assertEqual("Looking up: active children",
                         echo(tools.validate("get_statistics", {}).args))
        self.assertEqual("Looking up: case closures this year by reason",
                         echo(tools.validate("get_statistics", {
                             "measure": "closures", "period": "this_year",
                             "by": "reason"}).args))

    def test_the_guard_leaves_other_measures_alone(self):
        # It exists for "how many psychologists" served as a child count; a
        # closures question that mentions staff is a closures question.
        call = tools.validate("get_statistics", {"measure": "closures"})
        guarded = tools.correct_obvious_misroute("How many cases did staff close?", call)
        self.assertEqual("get_statistics", guarded.tool)

    def test_the_tool_count_holds_at_ten(self):
        self.assertNotIn("count_my_children", tools.REGISTRY)
        self.assertEqual(10, len(tools.REGISTRY))
