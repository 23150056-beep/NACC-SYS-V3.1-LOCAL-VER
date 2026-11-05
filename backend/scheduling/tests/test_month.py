"""The month calendar in the booking drawer: GET /api/availability/month/.

The day grid asked the server one day at a time and the drawer showed about a
week. A month is the same question asked 31 times, and asking it that way
would be thousands of queries, so the month is read once and every candidate
time goes through the same rule the day grid and the booking endpoint use
(booking.Diary). These tests hold three things:

* the answer is right for every state a day can be in;
* the month and the day grid can never disagree (the equivalence test at the
  bottom is the one that matters);
* the number of queries does not grow with the amount of data.

The clock is pinned. `MONTH_TEST_CLOCK` (sat, sun or wed) moves it to the
Saturday 23:58, Sunday 00:02 and Wednesday 09:30 that CLAUDE.md asks every
clock-sensitive test to be run at; most tests ask about December, which is
ahead of all of them.
"""
import json
import os
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from children.models import Child
from scheduling import booking
from scheduling.models import Appointment, AvailabilityBlock, Unavailability
from scheduling.tests.test_api import (
    SchedulingBase, User, child_with_referral, give_referral,
)

_CLOCKS = {
    "": (2026, 11, 2, 10, 0),        # Monday 10:00
    "sat": (2026, 11, 7, 23, 58),
    "sun": (2026, 11, 8, 0, 2),
    "wed": (2026, 11, 4, 9, 30),
}


def pinned(y, m, d, hh=0, mm=0):
    return timezone.make_aware(datetime(y, m, d, hh, mm))


def at(day, hh, mm=0):
    """A local time on a December 2026 day."""
    return pinned(2026, 12, day, hh, mm)


class MonthBase(SchedulingBase):
    """The base fixture's Wednesday 09:00-12:00 (capacity 2) for `self.psy`,
    one referred child held by `self.staff`, and a second social worker."""

    def setUp(self):
        patcher = patch("django.utils.timezone.now", return_value=pinned(
            *_CLOCKS[os.environ.get("MONTH_TEST_CLOCK", "")]))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()
        for person, first in ((self.psy, "Marta"), (self.other, "Oscar")):
            person.first_name, person.last_name = first, "Bulan"
            person.save()
        self.other_sw = User.objects.create_user(
            email="s2@racco1.gov.ph", username="s2", password="pass1234",
            role=self.staff_role, first_name="Rosa", last_name="Santos")

    def month(self, month="2026-12", **params):
        params.setdefault("psychologist", self.psy.id)
        params.setdefault("child", self.child.id)
        return self.client.get("/api/availability/month/", {"month": month, **params})

    def day(self, response, iso):
        return next(d for d in response.data["days"] if d["date"] == iso)

    def book(self, day, hh, mm=0, *, child=None, psychologist=None, minutes=60,
             status=Appointment.SCHEDULED):
        return Appointment.objects.create(
            child=child or child_with_referral(f"Kid {Child.objects.count()}", self.psy),
            psychologist=psychologist or self.psy, start=at(day, hh, mm),
            duration_minutes=minutes, status=status)

    def window(self, hh1, hh2, *, weekday=None, day=None, capacity=1, psychologist=None):
        return AvailabilityBlock.objects.create(
            psychologist=psychologist or self.psy, weekday=weekday,
            date=date(2026, 12, day) if day else None,
            start_time=f"{hh1:02d}:00", end_time=f"{hh2:02d}:00", capacity=capacity)

    def leave(self, first, last, psychologist=None):
        return Unavailability.objects.create(
            psychologist=psychologist or self.psy, starts_on=date(2026, 12, first),
            ends_on=date(2026, 12, last), reason="Training")


class MonthAccessTest(MonthBase):
    def test_anonymous_is_refused(self):
        self.assertEqual(401, self.month().status_code)

    def test_a_social_worker_asking_for_their_own_child(self):
        self._auth("s@racco1.gov.ph")
        response = self.month()
        self.assertEqual(200, response.status_code)
        self.assertEqual("2026-12", response.data["month"])

    def test_the_administrator_can_ask(self):
        self._auth("a@racco1.gov.ph")
        self.assertEqual(200, self.month().status_code)

    def test_no_child_still_shows_the_shape_of_the_month(self):
        self._auth("s@racco1.gov.ph")
        response = self.client.get("/api/availability/month/", {
            "month": "2026-12", "psychologist": self.psy.id})
        self.assertEqual(200, response.status_code)
        self.assertEqual("open", self.day(response, "2026-12-09")["state"])

    def test_a_psychologist_asking_about_themselves(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(200, self.month().status_code)

    def test_a_psychologist_asking_about_a_colleague_is_told_nothing(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(404, self.month(psychologist=self.other.id).status_code)

    def test_a_child_the_asker_cannot_see_is_not_found(self):
        stranger = Child.objects.create(fullname="Not Mine", social_worker=self.other_sw,
                                        assigned_psychologist=self.psy)
        give_referral(stranger)
        self._auth("s@racco1.gov.ph")
        self.assertEqual(404, self.month(child=stranger.id).status_code)

    def test_psychologist_required_and_must_exist(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(400, self.client.get(
            "/api/availability/month/", {"month": "2026-12"}).status_code)
        self.assertEqual(404, self.month(psychologist=99999).status_code)


class MonthValidationTest(MonthBase):
    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")

    def test_a_malformed_month_is_refused(self):
        for bad in ("", "2026", "2026-1", "2026-13", "2026-00", "december", "2026-12-01",
                    "0000-01"):
            response = self.month(bad)
            self.assertEqual(400, response.status_code, bad)
            self.assertEqual({"detail": "A month in YYYY-MM, please."}, response.data, bad)

    def test_a_month_that_has_passed_is_refused(self):
        response = self.month("2026-10")
        self.assertEqual(400, response.status_code)
        # The agency's date rides along, so a browser on another clock can
        # move to the month the server will take.
        self.assertEqual({"detail": "That month has already passed.",
                          "today": timezone.localdate().isoformat()}, response.data)

    def test_this_month_and_twelve_months_ahead_are_allowed(self):
        self.assertEqual(200, self.month("2026-11").status_code)
        self.assertEqual(200, self.month("2027-11").status_code)

    def test_more_than_twelve_months_ahead_is_refused(self):
        response = self.month("2027-12")
        self.assertEqual(400, response.status_code)
        self.assertEqual({"detail": "Months can be booked up to 12 months ahead.",
                          "today": timezone.localdate().isoformat()}, response.data)

    def test_the_duration_is_clamped_like_the_day_grid(self):
        self.assertEqual(15, self.month(duration=1).data["duration"])
        self.assertEqual(480, self.month(duration=99999).data["duration"])
        self.assertEqual(60, self.month(duration="abc").data["duration"])


class MonthStatesTest(MonthBase):
    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")

    def test_the_shape_of_the_answer(self):
        data = self.month().data
        self.assertEqual(
            {"psychologist", "month", "duration", "works", "days", "reason",
         "reason_kind", "today", "blocked"}, set(data))
        self.assertEqual("Marta Bulan", data["psychologist"])
        self.assertEqual(60, data["duration"])
        self.assertEqual([2], data["works"])
        self.assertEqual("", data["reason"])
        self.assertEqual(31, len(data["days"]))
        self.assertEqual("2026-12-01", data["days"][0]["date"])
        self.assertEqual("2026-12-31", data["days"][-1]["date"])
        self.assertEqual({"date", "state", "slots"}, set(data["days"][0]))

    def test_an_open_day_lists_exactly_the_start_times(self):
        wed = self.day(self.month(), "2026-12-09")
        self.assertEqual("open", wed["state"])
        self.assertEqual(
            [{"start": "09:00", "end": "10:00"}, {"start": "09:30", "end": "10:30"},
             {"start": "10:00", "end": "11:00"}, {"start": "10:30", "end": "11:30"},
             {"start": "11:00", "end": "12:00"}], wed["slots"])

    def test_a_day_with_no_window_is_off(self):
        tue = self.day(self.month(), "2026-12-08")
        self.assertEqual({"date": "2026-12-08", "state": "off", "slots": []}, tue)

    def test_a_day_whose_only_window_is_full_is_full(self):
        # Capacity 2, counted by start time within the window.
        self.book(9, 9)
        self.book(9, 11)
        response = self.month()
        self.assertEqual({"date": "2026-12-09", "state": "full", "slots": []},
                         self.day(response, "2026-12-09"))
        self.assertEqual("open", self.day(response, "2026-12-16")["state"])

    def test_a_cancelled_session_does_not_fill_a_day(self):
        self.book(9, 9)
        self.book(9, 11, status=Appointment.CANCELLED)
        self.assertEqual("open", self.day(self.month(), "2026-12-09")["state"])

    def test_leave_over_part_of_the_month_is_inclusive_at_both_ends(self):
        self.leave(9, 16)                         # a Wednesday to a Wednesday
        response = self.month()
        for iso in ("2026-12-09", "2026-12-16"):
            self.assertEqual("leave", self.day(response, iso)["state"], iso)
        # Away outranks "no window": the weekend inside the leave is shaded too.
        self.assertEqual("leave", self.day(response, "2026-12-12")["state"])
        self.assertEqual("open", self.day(response, "2026-12-02")["state"])
        self.assertEqual("open", self.day(response, "2026-12-23")["state"])
        self.assertEqual("off", self.day(response, "2026-12-17")["state"])
        self.assertEqual("", response.data["reason"])

    def test_a_dated_one_off_window_opens_its_day(self):
        self.window(13, 15, day=5)                 # a Saturday
        response = self.month()
        self.assertEqual("open", self.day(response, "2026-12-05")["state"])
        self.assertEqual(["13:00", "13:30", "14:00"],
                         [s["start"] for s in self.day(response, "2026-12-05")["slots"]])
        self.assertEqual([2], response.data["works"])      # a one-off is not a pattern

    def test_overlapping_windows_offer_each_start_once_in_order(self):
        self.window(10, 13, weekday=2, capacity=1)
        slots = self.day(self.month(), "2026-12-09")["slots"]
        starts = [s["start"] for s in slots]
        self.assertEqual(["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00"],
                         starts)
        self.assertEqual(starts, sorted(set(starts)))

    def test_a_session_too_long_for_any_window_leaves_every_day_full(self):
        response = self.month(duration=240)
        self.assertEqual("full", self.day(response, "2026-12-09")["state"])
        self.assertIn("240-minute session does not fit", response.data["reason"])
        self.assertEqual("too_long", response.data["reason_kind"])

    def test_a_longer_session_offers_fewer_starts(self):
        slots = self.day(self.month(duration=90), "2026-12-09")["slots"]
        self.assertEqual(["09:00", "09:30", "10:00", "10:30"], [s["start"] for s in slots])
        self.assertEqual("10:30", slots[0]["end"])

    def test_the_psychologists_other_sessions_remove_overlapping_starts(self):
        self.book(9, 10)
        starts = [s["start"] for s in self.day(self.month(), "2026-12-09")["slots"]]
        self.assertEqual(["09:00", "11:00"], starts)

    def test_moving_keeps_the_appointments_own_time_on_offer(self):
        own = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                         start=at(9, 10), duration_minutes=60)
        without = [s["start"] for s in self.day(self.month(), "2026-12-09")["slots"]]
        moving = [s["start"] for s in
                  self.day(self.month(exclude=own.id), "2026-12-09")["slots"]]
        self.assertNotIn("10:00", without)
        self.assertIn("10:00", moving)
        self.assertEqual(["09:00", "09:30", "10:00", "10:30", "11:00"], moving)

    def test_the_child_being_busy_elsewhere_removes_that_time(self):
        self.window(9, 12, weekday=2, capacity=2, psychologist=self.other)
        Appointment.objects.create(child=self.child, psychologist=self.other,
                                   start=at(9, 10), duration_minutes=60)
        starts = [s["start"] for s in self.day(self.month(), "2026-12-09")["slots"]]
        self.assertEqual(["09:00", "11:00"], starts)

    def test_without_a_case_referral_nothing_is_open_and_the_reason_says_so(self):
        self.child.case_referrals.all().delete()
        response = self.month()
        self.assertFalse([d for d in response.data["days"] if d["state"] == "open"])
        self.assertEqual(booking.missing_referral_error(self.child)["child"],
                         response.data["reason"])
        self.assertEqual("referral", response.data["reason_kind"])

    def test_the_answer_names_todays_date_and_whether_the_referral_blocks(self):
        self.child.case_referrals.all().delete()
        found = self.month().data
        self.assertTrue(found["blocked"])
        self.assertEqual(found["today"], timezone.localdate().isoformat())
        self.assertFalse(self.month(child="").data["blocked"])

    def test_the_referral_rule_is_waived_for_a_move(self):
        self.child.case_referrals.all().delete()
        own = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                         start=at(9, 10), duration_minutes=60)
        response = self.month(exclude=own.id)
        self.assertEqual("open", self.day(response, "2026-12-09")["state"])
        self.assertEqual("", response.data["reason"])
        self.assertEqual("", response.data["reason_kind"])

    def test_no_availability_posted(self):
        response = self.month(psychologist=self.other.id)
        self.assertEqual(f"{self.other.fullname} has no availability posted yet.",
                         response.data["reason"])
        self.assertEqual([], response.data["works"])
        self.assertEqual("no_availability", response.data["reason_kind"])

    def test_away_the_whole_month(self):
        self.leave(1, 31)
        response = self.month()
        self.assertEqual({"leave"}, {d["state"] for d in response.data["days"]})
        self.assertEqual(f"{self.psy.fullname} is away for the rest of December.",
                         response.data["reason"])
        self.assertEqual("away", response.data["reason_kind"])

    def test_fully_booked_the_whole_month(self):
        for wednesday in (2, 9, 16, 23, 30):
            self.book(wednesday, 9)
            self.book(wednesday, 11)
        response = self.month()
        self.assertEqual(f"{self.psy.fullname} is fully booked for the rest of December.",
                         response.data["reason"])
        self.assertEqual("full", response.data["reason_kind"])

    def test_windows_only_in_other_months_leave_nothing_to_open(self):
        AvailabilityBlock.objects.filter(psychologist=self.psy).delete()
        AvailabilityBlock.objects.create(psychologist=self.psy, date=date(2026, 11, 20),
                                         start_time="09:00", end_time="12:00")
        response = self.month()
        self.assertEqual(f"{self.psy.fullname} has no open time in December.",
                         response.data["reason"])
        self.assertEqual("none", response.data["reason_kind"])

    def test_the_reason_is_empty_when_a_day_is_open(self):
        self.assertEqual("", self.month().data["reason"])
        self.assertEqual("", self.month().data["reason_kind"])


class MonthTodayAndPastTest(MonthBase):
    """The current month, with the clock set by each test."""

    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")
        AvailabilityBlock.objects.filter(psychologist=self.psy).delete()
        AvailabilityBlock.objects.create(psychologist=self.psy, weekday=0,
                                         start_time="08:00", end_time="12:00", capacity=4)

    def _november(self, hh, mm=0):
        with patch("django.utils.timezone.now", return_value=pinned(2026, 11, 2, hh, mm)):
            return self.month("2026-11")

    def test_earlier_days_are_past_and_offer_nothing(self):
        response = self._november(10)
        self.assertEqual({"date": "2026-11-01", "state": "past", "slots": []},
                         self.day(response, "2026-11-01"))

    def test_today_is_judged_on_the_times_still_ahead(self):
        today = self.day(self._november(10), "2026-11-02")
        self.assertEqual("open", today["state"])
        self.assertEqual(["10:30", "11:00"], [s["start"] for s in today["slots"]])
        self.assertEqual("open", self.day(self._november(10), "2026-11-09")["state"])

    def test_today_with_nothing_left_is_full_not_off(self):
        today = self.day(self._november(11, 30), "2026-11-02")
        self.assertEqual({"date": "2026-11-02", "state": "full", "slots": []}, today)

    def test_past_beats_leave(self):
        Unavailability.objects.create(psychologist=self.psy, starts_on=date(2026, 11, 1),
                                      ends_on=date(2026, 11, 3))
        response = self._november(10)
        self.assertEqual("past", self.day(response, "2026-11-01")["state"])
        self.assertEqual("leave", self.day(response, "2026-11-02")["state"])


class MonthPrivacyTest(MonthBase):
    def test_names_and_case_numbers_never_leak(self):
        """Another social worker's child sits in the slot. The refusal behind a
        missing time names that case, so the month must carry times only."""
        stranger = Child.objects.create(fullname="Zaldy Pellagrino-Xu",
                                        social_worker=self.other_sw,
                                        assigned_psychologist=self.psy)
        give_referral(stranger)
        for hh in (9, 11):
            Appointment.objects.create(child=stranger, psychologist=self.psy,
                                       start=at(9, hh), duration_minutes=60)
        Appointment.objects.create(child=stranger, psychologist=self.psy,
                                   start=at(16, 10), duration_minutes=60)
        self._auth("s@racco1.gov.ph")
        response = self.month()
        body = json.dumps(response.data)
        self.assertNotIn("Pellagrino", body)
        self.assertNotIn(f"C-{stranger.pk:04d}", body)
        self.assertNotIn(str(stranger.pk).zfill(4), body)
        # The clash really removed a time, so the answer was under pressure.
        self.assertNotIn("10:00", [s["start"] for s in self.day(response, "2026-12-16")["slots"]])
        for day in response.data["days"]:
            for slot in day["slots"]:
                self.assertEqual({"start", "end"}, set(slot))


class MonthQueryCountTest(MonthBase):
    """A month is a fixed number of queries: windows, leave, the psychologist's
    sessions, the child's, and the referral. Not one per day or per time."""

    QUERIES = 5

    def _count(self, month=(2026, 12)):
        with CaptureQueriesContext(connection) as ctx:
            booking.month_view(self.psy, self.child, *month)
        return len(ctx)

    def test_the_month_costs_five_queries(self):
        with self.assertNumQueries(self.QUERIES):
            booking.month_view(self.psy, self.child, 2026, 12)

    def test_moving_skips_the_referral_query(self):
        with self.assertNumQueries(self.QUERIES - 1):
            booking.month_view(self.psy, self.child, 2026, 12, exclude_id=1)

    def test_the_count_does_not_grow_with_the_data_or_the_days(self):
        small = self._count((2026, 12))
        for wd in (0, 1, 3, 4):
            self.window(8, 17, weekday=wd, capacity=6)
        for day in (5, 12, 19):
            self.window(9, 12, day=day, capacity=3)
        self.leave(24, 26)
        self.leave(30, 31)
        for day in range(1, 29):
            self.book(day, 9 + day % 5, 30 if day % 2 else 0)
        Appointment.objects.create(child=self.child, psychologist=self.other,
                                   start=at(10, 9), duration_minutes=60)
        self.assertEqual(self.QUERIES, small)
        self.assertEqual(self.QUERIES, self._count((2026, 12)))
        self.assertEqual(self.QUERIES, self._count((2027, 2)))      # 28 days

    def test_the_endpoint_count_does_not_grow_either(self):
        self._auth("s@racco1.gov.ph")

        def endpoint():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(200, self.month().status_code)
            return len(ctx)

        small = endpoint()
        for wd in (0, 1, 3, 4):
            self.window(8, 17, weekday=wd, capacity=6)
        for day in range(1, 29):
            self.book(day, 9 + day % 5)
        self.leave(24, 26)
        self.assertEqual(small, endpoint())

    def test_a_day_of_the_slot_grid_is_five_diary_queries_too(self):
        with self.assertNumQueries(self.QUERIES):
            booking.bookable_slots(self.psy, self.child, date(2026, 12, 9))


class MonthEquivalenceTest(MonthBase):
    """The month and the rule behind the booking endpoint cannot disagree.

    A fixture with every awkward thing at once: a full window, overlapping
    windows, a dated one-off, leave, a cancelled session, and a child who is
    busy with another psychologist."""

    def setUp(self):
        super().setUp()
        self.window(10, 13, weekday=2, capacity=1)        # overlaps the 09:00-12:00
        self.window(13, 17, weekday=3, capacity=1)        # Thursday, will be full
        self.window(9, 11, day=5, capacity=2)             # a Saturday, one-off
        self.window(9, 12, weekday=2, capacity=2, psychologist=self.other)
        a = child_with_referral("Ana B", self.psy)
        b = child_with_referral("Ben C", self.psy)
        self.book(9, 9, child=a)                          # fills 09:00-12:00 with the next
        self.book(9, 11, child=b)
        self.book(10, 13, child=a)                        # fills Thursday
        self.book(23, 10, child=a, status=Appointment.CANCELLED)
        self.book(23, 11, child=b, minutes=45)
        Appointment.objects.create(child=self.child, psychologist=self.other,
                                   start=at(16, 10), duration_minutes=60)
        self.book(5, 9, child=a)
        self.leave(24, 25)
        self.leave(30, 30)
        self._auth("s@racco1.gov.ph")

    def test_every_day_of_the_month_matches_the_slot_endpoint(self):
        for duration in (30, 60, 90):
            month = self.month(duration=duration)
            self.assertEqual(31, len(month.data["days"]))
            for day in month.data["days"]:
                single = self.client.get("/api/availability/slots/", {
                    "psychologist": self.psy.id, "child": self.child.id,
                    "date": day["date"], "duration": duration})
                self.assertEqual(single.data["slots"], day["slots"],
                                 f"{day['date']} at {duration} minutes")
        # The control: the fixture really does produce each kind of day.
        states = {d["state"] for d in self.month().data["days"]}
        self.assertEqual({"open", "full", "leave", "off"}, states)

    def test_moving_matches_the_slot_endpoint_too(self):
        own = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                         start=at(16, 11), duration_minutes=60)
        month = self.month(exclude=own.id)
        for day in month.data["days"]:
            single = self.client.get("/api/availability/slots/", {
                "psychologist": self.psy.id, "child": self.child.id,
                "date": day["date"], "exclude": own.id})
            self.assertEqual(single.data["slots"], day["slots"], day["date"])
        self.assertIn("11:00", [s["start"] for s in self.day(month, "2026-12-16")["slots"]])

    def test_the_evaluator_gives_the_dict_errors_for_gives(self):
        """Every half hour of every day, inside and outside the windows, for
        the booking endpoint's two callers (own calendar or not)."""
        first, last = date(2026, 12, 1), date(2026, 12, 31)
        for child, exclude in ((self.child, None), (None, None), (self.child, 1)):
            # The main case is compared both ways and at two lengths; the
            # variants once each, as they differ only in what they read.
            main = child is self.child and exclude is None
            diary = booking.Diary.load(self.psy, child, first, last,
                                       duration_minutes=90, exclude_id=exclude)
            seen = set()
            for n in range(31):
                day = first + timedelta(days=n)
                for step in range(16, 36):                  # 08:00 to 17:30
                    start = pinned(day.year, day.month, day.day, step // 2, step % 2 * 30)
                    for minutes in ((30, 90) if main else (90,)):
                        for own in ((False, True) if main else (False,)):
                            expected = booking.errors_for(
                                self.psy, child, start, minutes,
                                own_calendar=own, exclude_id=exclude)
                            got = diary.errors_for(start, minutes, own_calendar=own)
                            self.assertEqual(expected, got, (start, minutes, own, exclude))
                            seen.add(tuple(sorted(got)))
                            seen.add(tuple(sorted(got.values())))
            # Not a vacuous pass: refusals of every kind were compared.
            self.assertTrue(any(isinstance(k, tuple) and k for k in seen))
        messages = " ".join(m for t in seen for m in t)
        for text in ("is away on", "fully booked", "outside the psychologist",
                     "is already booked", "already has an appointment"):
            self.assertIn(text, messages)

    def test_a_diary_refuses_a_time_it_was_not_read_for(self):
        diary = booking.Diary.load(self.psy, self.child, date(2026, 12, 1),
                                   date(2026, 12, 1), duration_minutes=60)
        with self.assertRaises(ValueError):
            diary.errors_for(at(2, 9), 60)
        with self.assertRaises(ValueError):
            diary.errors_for(at(1, 9), 120)
