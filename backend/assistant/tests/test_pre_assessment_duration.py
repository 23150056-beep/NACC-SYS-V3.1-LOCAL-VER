"""Time in pre-assessment: one definition, shown on the Agency Summary,
answered by the chatbot.

The owner chose the ends: from the pre-assessment's own start date to the day
it was completed, with pending and in-progress ones left out until they are
completed. The definition tests pin that choice and what it leaves out; the
parity tests ask the Summary for the same numbers the chatbot gives; the
seeder test keeps the demo measuring the caseload rather than the seeder.
"""
from datetime import datetime, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import Role
from assistant import tools
from children.models import Child
from clinical.models import PreAssessment
from clinical.reports import pre_assessment_duration
from locations.models import Barangay, Municipality, Province

User = get_user_model()


def at(day, hour=15):
    """A Manila wall-clock time on `day`."""
    return timezone.make_aware(datetime.combine(day, time(hour, 0)),
                               timezone.get_current_timezone())


class DurationBase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r) for r in
                 (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}
        self.admin = User.objects.create_user(email="a@racco1.gov.ph", username="a",
                                              password="pass1234", role=roles[Role.ADMINISTRATOR])
        self.staff = User.objects.create_user(email="s@racco1.gov.ph", username="s",
                                              password="pass1234", role=roles[Role.STAFF])
        self.psy = User.objects.create_user(email="p@racco1.gov.ph", username="p",
                                            password="pass1234", role=roles[Role.PSYCHOLOGIST])
        self.other = User.objects.create_user(email="o@racco1.gov.ph", username="o",
                                              password="pass1234", role=roles[Role.PSYCHOLOGIST])
        self.mine = Child.objects.create(fullname="Maria Santos", assigned_psychologist=self.psy)
        self.theirs = Child.objects.create(fullname="Juan Cruz", assigned_psychologist=self.other)
        self.today = timezone.localdate()
        self.factory = APIRequestFactory()

    def pa(self, started_days_ago, done_days_ago=None, child=None,
           status=PreAssessment.COMPLETED, hour=15):
        """Completed `done_days_ago` (None: no completion time recorded)."""
        child = child or self.mine
        return PreAssessment.objects.create(
            child=child, psychologist=child.assigned_psychologist,
            date=self.today - timedelta(days=started_days_ago), status=status,
            completed_at=(None if done_days_ago is None
                          else at(self.today - timedelta(days=done_days_ago), hour)))

    def duration(self, **kw):
        return pre_assessment_duration(PreAssessment.objects.all(), **kw)

    def stats(self, user, **raw):
        call = tools.validate("get_statistics", {"measure": "pre_assessment_duration", **raw})
        req = self.factory.get("/")
        req.user = user
        return tools.REGISTRY["get_statistics"]["resolve"](req, call.args)


class DefinitionTest(DurationBase):
    def test_from_the_start_date_to_the_day_it_was_completed(self):
        self.pa(20, 10)
        d = self.duration()
        self.assertEqual((1, 10, 10), (d["completed"], d["median_days"], d["longest_days"]))

    def test_pending_and_in_progress_are_left_out_and_counted_as_open(self):
        self.pa(20, 10)
        self.pa(30, status=PreAssessment.PENDING)
        self.pa(40, status=PreAssessment.IN_PROGRESS)
        d = self.duration()
        self.assertEqual((1, 10, 2), (d["completed"], d["median_days"], d["open"]))

    def test_open_ones_are_left_out_even_with_a_completion_time(self):
        # Status decides, not the timestamp: reopened, or written by hand.
        self.pa(30, 1, status=PreAssessment.IN_PROGRESS)
        d = self.duration()
        self.assertEqual((0, None, 1), (d["completed"], d["median_days"], d["open"]))

    def test_completed_with_no_completion_time_is_not_counted(self):
        self.pa(20, None)
        d = self.duration()
        self.assertEqual((0, None, 1), (d["completed"], d["median_days"], d["no_completion_date"]))
        # It cannot be placed in a window, so it is reported in every one.
        far = self.today - timedelta(days=400)
        self.assertEqual(1, self.duration(start=far, end=far)["no_completion_date"])

    def test_completed_before_its_start_date_is_not_counted(self):
        # The start date stays editable after completion. Not a negative
        # duration and not zero either: left out, and counted as left out.
        self.pa(5, 10)
        d = self.duration()
        self.assertEqual((0, None, 1), (d["completed"], d["median_days"], d["completed_before_start"]))

    def test_completed_the_day_it_started_is_zero_days(self):
        self.pa(5, 5)
        d = self.duration()
        self.assertEqual((1, 0), (d["completed"], d["median_days"]))

    def test_the_median_of_an_even_count(self):
        self.pa(10, 7)                                  # 3
        self.pa(10, 6)                                  # 4
        self.assertEqual(3.5, self.duration()["median_days"])
        self.pa(10, 5)                                  # 5
        median = self.duration()["median_days"]
        self.assertEqual(4, median)
        self.assertIsInstance(median, int)

    def test_a_window_picks_by_the_day_of_completion(self):
        self.pa(100, 30)                                # completed inside
        self.pa(35, 10)                                 # started inside, completed after
        self.pa(100, 60)                                # completed before
        self.pa(30, status=PreAssessment.PENDING)       # open, as of today
        d = self.duration(start=self.today - timedelta(days=40),
                          end=self.today - timedelta(days=20))
        self.assertEqual((1, 70, 1), (d["completed"], d["median_days"], d["open"]))

    def test_the_end_of_a_window_is_exclusive_and_either_end_may_be_open(self):
        self.pa(30, 20)
        day = self.today - timedelta(days=20)
        self.assertEqual(0, self.duration(end=day)["completed"])
        self.assertEqual(1, self.duration(end=day + timedelta(days=1))["completed"])
        self.assertEqual(1, self.duration(start=day)["completed"])
        self.assertEqual(0, self.duration(start=day + timedelta(days=1))["completed"])

    def test_days_are_counted_on_the_manila_calendar(self):
        # Completed at 07:00 in Manila, which is still the day before in UTC.
        self.pa(10, 9, hour=7)
        self.assertEqual(1, self.duration()["median_days"])


class SummaryCardTest(DurationBase):
    def setUp(self):
        super().setUp()
        self.pa(40, 30)                                           # Pia's child: 10 days
        self.pa(40, 20, child=self.theirs)                        # Oscar's: 20 days
        self.pa(10, 20, child=self.theirs)                        # before its start date
        self.pa(15, None)                                         # no completion time
        self.pa(12, status=PreAssessment.PENDING, child=self.theirs)

    def summary(self, **params):
        client = APIClient()
        client.force_authenticate(self.admin)
        res = client.get("/api/reports/summary/", params)
        self.assertEqual(200, res.status_code)
        return res

    def test_the_summary_carries_the_agency_figure(self):
        self.assertEqual({"completed": 2, "median_days": 15, "longest_days": 20,
                          "no_completion_date": 1, "completed_before_start": 1, "open": 1},
                         self.summary().data["pre_assessment_duration"])

    def test_the_summary_window_is_inclusive_of_to(self):
        day = (self.today - timedelta(days=30)).isoformat()
        d = self.summary(**{"from": day, "to": day}).data["pre_assessment_duration"]
        self.assertEqual((1, 10), (d["completed"], d["median_days"]))

    def test_the_csv_export_carries_it_too(self):
        body = self.summary(export="csv").content.decode()
        self.assertIn("Time in pre-assessment,Value", body)
        self.assertIn("Median days from start date to completion,15", body)
        self.assertIn("Still open (not counted),1", body)
        self.assertIn("Completed before the start date (not counted),1", body)


class ChatbotDurationTest(SummaryCardTest):
    def test_the_answer_is_the_median_in_days(self):
        out = self.stats(self.admin)
        self.assertEqual({"value": "15 days", "label": "median time in pre-assessment, all time"},
                         out["figure"])
        self.assertEqual((2, 2), (out["total"], tools.result_size(out)))
        self.assertEqual("2 pre-assessments completed, all time", out["title"])

    def test_the_note_says_what_is_in_the_median_and_what_is_not(self):
        note = self.stats(self.admin)["note"]
        self.assertIn("2 pre-assessments completed, counted from the start date to the day "
                      "it was completed. Longest 20 days.", note)
        self.assertIn("1 still open — not counted until completed.", note)
        self.assertIn("1 completed with no completion date recorded — not counted.", note)
        self.assertIn("1 marked completed before its start date — not counted.", note)

    def test_a_psychologist_sees_their_own_children_only(self):
        out = self.stats(self.psy)
        # Pia's child: 10 days, plus the undated one. None of Oscar's.
        self.assertEqual("10 days", out["figure"]["value"])
        self.assertEqual(1, out["total"])
        self.assertIn("1 completed with no completion date", out["note"])
        self.assertNotIn("still open", out["note"])
        self.assertNotIn("before its start date", out["note"])

    def test_administrators_see_the_agency_and_a_social_worker_their_own(self):
        self.assertEqual(2, self.stats(self.admin)["total"])
        self.assertEqual(0, self.stats(self.staff)["total"])
        Child.objects.update(social_worker=self.staff)
        self.assertEqual(2, self.stats(self.staff)["total"])

    def test_a_period_with_nothing_completed_has_no_figure(self):
        out = self.stats(self.admin, period="next_week")
        self.assertIsNone(out["figure"])
        self.assertEqual((0, 0), (out["total"], tools.result_size(out)))
        self.assertTrue(out["note"].startswith("No pre-assessment was completed in this period"))

    def test_the_label_names_the_period(self):
        PreAssessment.objects.all().delete()
        self.pa(4, 0)                                   # completed today: always this month
        out = self.stats(self.admin, period="this_month")
        self.assertEqual({"value": "4 days", "label": "median time in pre-assessment this month"},
                         out["figure"])
        self.assertEqual("1 pre-assessment completed this month", out["title"])

    def test_nothing_completed_yet_is_said_plainly(self):
        PreAssessment.objects.all().delete()
        self.pa(3, status=PreAssessment.PENDING)
        note = self.stats(self.admin)["note"]
        self.assertTrue(note.startswith("No pre-assessment has been completed yet"), note)

    def test_no_scorecard_of_psychologists(self):
        out = self.stats(self.admin, by="psychologist")
        self.assertEqual(("none", []), (out["by"], out["rows"]))

    def test_the_summary_is_linked_for_those_who_can_open_it(self):
        self.assertEqual("/reports/summary", self.stats(self.staff)["screen"]["path"])
        self.assertIsNone(self.stats(self.psy)["screen"])

    def test_follow_ups_offer_periods(self):
        call = tools.validate("get_statistics", {"measure": "pre_assessment_duration"})
        offers = tools.followups(call, self.stats(self.admin), Role.ADMINISTRATOR)
        self.assertEqual(["This month?", "This year?", "Last year?"],
                         [o["label"] for o in offers])

    def test_the_words_people_use_reach_it(self):
        for said in ("time in pre-assessment", "Pre-assessment duration", "turnaround",
                     "pre_assessment_time", "tagal ng pre-assessment"):
            call = tools.validate("get_statistics", {"measure": said})
            self.assertEqual("pre_assessment_duration", call.args["measure"], said)
        # The pending count keeps its words.
        for said in ("pending", "pre-assessments", "pre_assessment"):
            self.assertEqual("pre_assessments", tools.validate(
                "get_statistics", {"measure": said}).args["measure"], said)

    def test_the_echo(self):
        call = tools.validate("get_statistics",
                              {"measure": "pre_assessment_duration", "period": "last_year"})
        self.assertEqual("Looking up: time in pre-assessment last year", call.echo)


class ParityTest(SummaryCardTest):
    """The chatbot and the Agency Summary's card, same caller, same window."""

    def test_all_time(self):
        d = self.summary().data["pre_assessment_duration"]
        out = self.stats(self.admin)
        self.assertEqual(d["completed"], out["total"])
        self.assertEqual(tools._days(d["median_days"]), out["figure"]["value"])
        self.assertIn(f"Longest {tools._days(d['longest_days'])}", out["note"])
        self.assertIn(f"{d['open']} still open", out["note"])

    def test_last_month(self):
        start, end = tools.period_range("last_month")
        d = self.summary(**{"from": start.isoformat(),
                            "to": (end - timedelta(days=1)).isoformat()}).data["pre_assessment_duration"]
        out = self.stats(self.admin, period="last_month")
        self.assertEqual(d["completed"], out["total"])
        if d["median_days"] is None:
            self.assertIsNone(out["figure"])
        else:
            self.assertEqual(tools._days(d["median_days"]), out["figure"]["value"])


@override_settings(DEBUG=True)
class SeededPreAssessmentsTest(TestCase):
    def test_seeded_ones_take_days_not_the_time_since_seeding(self):
        province = Province.objects.create(psgc_code="012800000", name="Ilocos Norte")
        town = Municipality.objects.create(psgc_code="012812000", name="Laoag City",
                                           province=province)
        Barangay.objects.create(psgc_code="012812001", name="Barangay 1", municipality=town)
        call_command("seed_demo_data", children=6, stdout=StringIO())

        now = timezone.now()
        for pa in PreAssessment.objects.all():
            took = (timezone.localtime(pa.completed_at).date() - pa.date).days
            self.assertTrue(3 <= took <= 21, took)
            self.assertLess(pa.completed_at, now)
        self.assertEqual(PreAssessment.objects.count(),
                         pre_assessment_duration(PreAssessment.objects.all())["completed"])
