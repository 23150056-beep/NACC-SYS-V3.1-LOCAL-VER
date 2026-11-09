"""The booking drawer's psychologist picker: GET /api/availability/psychologists/.

A psychologist who is fully loaded must not be selectable. The answer comes
from booking.Diary and booking.slots_on - the rule the booking endpoint
applies - never from a count, so the equivalence test at the bottom is the one
that matters: someone is "open" on a day exactly when the slot grid has a time.

The clock is pinned to Monday 2 Nov 2026, 10:00 (see test_month.py).
"""
from datetime import date

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from clinical.models import CaseReferral
from scheduling import booking
from scheduling.models import Appointment, AvailabilityBlock
from scheduling.tests.test_month import MonthBase, at
from scheduling.tests.test_api import child_with_referral

URL = "/api/availability/psychologists/"
WED = "2026-12-09"          # a Wednesday; the fixture window is 09:00-12:00, capacity 2


class PickerBase(MonthBase):
    def ask(self, **params):
        params.setdefault("child", self.child.id)
        return self.client.get(URL, params)

    def row(self, response, person):
        return next(r for r in response.data["psychologists"] if r["id"] == person.id)

    def fill(self, day, person=None, hours=(9, 10)):
        for hh in hours:
            self.book(day, hh, psychologist=person or self.psy)


class PickerAccessTest(PickerBase):
    def test_anonymous_is_refused(self):
        self.assertEqual(401, self.ask().status_code)

    def test_a_psychologist_is_refused(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(403, self.ask().status_code)

    def test_a_social_worker_and_the_administrator_can_ask(self):
        for email in ("s@racco1.gov.ph", "a@racco1.gov.ph"):
            self._auth(email)
            self.assertEqual(200, self.ask().status_code, email)

    def test_a_child_the_asker_cannot_see_is_not_found(self):
        stranger = child_with_referral("Not Mine", self.psy)
        stranger.social_worker = self.other_sw
        stranger.save()
        self._auth("s@racco1.gov.ph")
        self.assertEqual(404, self.ask(child=stranger.id).status_code)

    def test_a_malformed_child_is_a_400(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(400, self.ask(child="abc").status_code)

    def test_only_active_psychologists_are_listed(self):
        gone = User.objects.create_user(
            email="g@racco1.gov.ph", username="g", password="pass1234", role=self.psy_role)
        gone.status = User.ARCHIVED
        gone.save()
        self._auth("s@racco1.gov.ph")
        ids = {r["id"] for r in self.ask().data["psychologists"]}
        self.assertEqual({self.psy.id, self.other.id}, ids)

    def test_the_route_is_not_swallowed_as_a_detail_pk(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(404, self.client.get("/api/availability/nonsense/").status_code)
        self.assertIn("psychologists", self.ask().data)


class PickerDayTest(PickerBase):
    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")

    def test_open_off_and_away_on_a_day(self):
        self.window(9, 12, weekday=2, capacity=1, psychologist=self.other)
        self.leave(9, 9, psychologist=self.other)
        response = self.ask(day=WED)
        self.assertEqual(WED, response.data["day"])
        self.assertEqual("open", self.row(response, self.psy)["state"])
        other = self.row(response, self.other)
        self.assertEqual("away", other["state"])
        self.assertEqual("Away on leave on Wednesday, December 9", other["label"])

    def test_no_window_is_off(self):
        row = self.row(self.ask(day=WED), self.other)
        self.assertEqual("off", row["state"])
        self.assertEqual("No hours posted on Wednesday, December 9", row["label"])

    def test_capacity_full_makes_the_day_full(self):
        self.fill(9)
        row = self.row(self.ask(day=WED), self.psy)
        self.assertEqual("full", row["state"])
        self.assertEqual("Fully booked on Wednesday, December 9", row["label"])
        self.assertEqual("open", self.row(self.ask(day="2026-12-16"), self.psy)["state"])

    def test_one_place_left_is_still_open(self):
        self.book(9, 9)
        self.assertEqual("open", self.row(self.ask(day=WED), self.psy)["state"])

    def test_a_one_off_override_replaces_the_weekly_window(self):
        AvailabilityBlock.objects.create(
            psychologist=self.psy, date=date(2026, 12, 9), start_time="13:00",
            end_time="14:00", capacity=1)
        self.assertEqual("open", self.row(self.ask(day=WED), self.psy)["state"])
        self.book(9, 13)
        row = self.row(self.ask(day=WED), self.psy)
        self.assertEqual("full", row["state"])

    def test_a_length_no_window_fits_is_full_with_its_own_sentence(self):
        row = self.row(self.ask(day=WED, duration=240), self.psy)
        self.assertEqual("full", row["state"])
        self.assertIn("240-minute", row["label"])

    def test_the_labels_name_no_child_or_case_number(self):
        self.fill(9)
        label = self.row(self.ask(day=WED), self.psy)["label"]
        self.assertNotIn("Kid", label)
        self.assertNotIn("C-", label)

    def test_exclude_makes_the_moved_appointment_open(self):
        AvailabilityBlock.objects.filter(psychologist=self.psy).delete()
        self.window(9, 10, weekday=2, capacity=1)
        mine = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=at(9, 9), duration_minutes=60)
        self.assertEqual("full", self.row(self.ask(day=WED), self.psy)["state"])
        self.assertEqual(
            "open", self.row(self.ask(day=WED, exclude=mine.id), self.psy)["state"])

    def test_an_exclude_that_is_not_the_childs_is_a_404(self):
        theirs = self.book(9, 9)
        self.assertEqual(404, self.ask(day=WED, exclude=theirs.id).status_code)
        self.assertEqual(404, self.ask(day=WED, exclude=99999).status_code)

    def test_exclude_without_a_child_is_ignored(self):
        theirs = self.book(9, 9)
        response = self.client.get(URL, {"day": WED, "exclude": theirs.id})
        self.assertEqual(200, response.status_code)

    def test_no_referral_blocks_and_judges_nobody(self):
        CaseReferral.objects.filter(child=self.child).delete()
        response = self.ask(day=WED)
        self.assertTrue(response.data["blocked"])
        self.assertEqual({"unknown"}, {r["state"] for r in response.data["psychologists"]})

    def test_a_move_is_exempt_from_the_referral_rule(self):
        mine = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=at(9, 9), duration_minutes=60)
        CaseReferral.objects.filter(child=self.child).delete()
        response = self.ask(day=WED, exclude=mine.id)
        self.assertFalse(response.data["blocked"])
        self.assertEqual("open", self.row(response, self.psy)["state"])

    def test_the_childs_own_clash_does_not_hide_a_colleague(self):
        """The child is already in a room at 9:00-12:00 with the other
        psychologist; the check is per psychologist and per the real rule."""
        self.window(9, 12, weekday=2, capacity=3, psychologist=self.other)
        for hh in (9, 10, 11):
            Appointment.objects.create(child=self.child, psychologist=self.other,
                                       start=at(9, hh), duration_minutes=60)
        response = self.ask(day=WED)
        self.assertEqual("full", self.row(response, self.psy)["state"])
        self.assertEqual("full", self.row(response, self.other)["state"])

    def test_no_child_still_judges_the_shape_of_the_day(self):
        response = self.client.get(URL, {"day": WED})
        self.assertEqual("open", self.row(response, self.psy)["state"])
        self.assertFalse(response.data["blocked"])


class PickerRangeTest(PickerBase):
    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")

    def test_the_range_is_today_and_27_days_on(self):
        response = self.ask()
        self.assertIsNone(response.data["day"])
        self.assertEqual("2026-11-02", response.data["first"])
        self.assertEqual("2026-11-29", response.data["last"])

    def test_next_open_and_open_days(self):
        row = self.row(self.ask(), self.psy)
        self.assertEqual("open", row["state"])
        self.assertEqual("2026-11-04", row["next_open"])
        self.assertEqual(4, row["open_days"])          # Nov 4, 11, 18, 25

    def test_next_open_skips_a_full_day(self):
        for hh in (9, 10):
            Appointment.objects.create(
                child=child_with_referral(f"K{hh}", self.psy), psychologist=self.psy,
                start=timezone.make_aware(timezone.datetime(2026, 11, 4, hh, 0)),
                duration_minutes=60)
        row = self.row(self.ask(), self.psy)
        self.assertEqual("2026-11-11", row["next_open"])
        self.assertEqual(3, row["open_days"])

    def test_a_psychologist_full_every_day_is_full(self):
        for d in (4, 11, 18, 25):
            for hh in (9, 10):
                Appointment.objects.create(
                    child=child_with_referral(f"K{d}{hh}", self.psy), psychologist=self.psy,
                    start=timezone.make_aware(timezone.datetime(2026, 11, d, hh, 0)),
                    duration_minutes=60)
        row = self.row(self.ask(), self.psy)
        self.assertEqual("full", row["state"])
        self.assertIsNone(row["next_open"])
        self.assertEqual("No open time in the next 4 weeks", row["label"])

    def test_no_hours_and_away_all_range(self):
        self.assertEqual("No hours posted in the next 4 weeks",
                         self.row(self.ask(), self.other)["label"])
        from scheduling.models import Unavailability
        Unavailability.objects.create(psychologist=self.psy, starts_on=date(2026, 11, 2),
                                      ends_on=date(2026, 11, 29), reason="Training")
        row = self.row(self.ask(), self.psy)
        self.assertEqual("away", row["state"])
        self.assertEqual("Away on leave for the next 4 weeks", row["label"])

    def test_no_referral_blocks_the_range_too(self):
        CaseReferral.objects.filter(child=self.child).delete()
        self.assertTrue(self.ask().data["blocked"])


class PickerValidationTest(PickerBase):
    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")

    def test_a_malformed_past_or_too_distant_day_is_a_400_with_a_sentence(self):
        for bad in ("soon", "2026-13-01", "2026-11-01", "2027-12-01"):
            response = self.ask(day=bad)
            self.assertEqual(400, response.status_code, bad)
            self.assertTrue(response.data["detail"], bad)

    def test_today_and_a_year_ahead_are_accepted(self):
        self.assertEqual(200, self.ask(day="2026-11-02").status_code)
        self.assertEqual(200, self.ask(day="2027-11-02").status_code)

    def test_the_whole_twelfth_month_is_accepted_like_the_month_endpoint(self):
        self.assertEqual(200, self.ask(day="2027-11-30").status_code)

    def test_duration_is_clamped(self):
        self.assertEqual(200, self.ask(day=WED, duration="5").status_code)
        self.assertEqual(200, self.ask(day=WED, duration="nope").status_code)


class PickerQueryCountTest(PickerBase):
    def count(self, **params):
        self._auth("s@racco1.gov.ph")
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(200, self.ask(**params).status_code)
        return len(ctx)

    def test_queries_are_bounded_and_do_not_grow_with_sessions(self):
        base_day = self.count(day=WED)
        base_range = self.count()
        for n in range(6):
            self.book(9, 9 + n % 3, child=self.child, psychologist=self.psy)
            self.book(16, 9, psychologist=self.other)
        self.assertEqual(base_day, self.count(day=WED))
        self.assertEqual(base_range, self.count())
        # Two psychologists: about five reads each plus the fixed overhead.
        self.assertLessEqual(base_day, 20)
        self.assertLessEqual(base_range, 20)

    def test_queries_grow_by_a_fixed_amount_per_psychologist(self):
        two = self.count(day=WED)
        User.objects.create_user(email="p3@racco1.gov.ph", username="p3",
                                 password="pass1234", role=self.psy_role)
        three = self.count(day=WED)
        self.assertLessEqual(three - two, 5)


class PickerEquivalenceTest(PickerBase):
    def test_open_exactly_when_the_slot_grid_has_a_time(self):
        self.window(9, 11, weekday=2, capacity=1, psychologist=self.other)
        self.book(9, 9, psychologist=self.other)
        self.book(9, 10, psychologist=self.other)
        self.leave(16, 16, psychologist=self.psy)
        self._auth("s@racco1.gov.ph")
        for iso in ("2026-12-08", "2026-12-09", "2026-12-16", "2026-12-23"):
            day = date.fromisoformat(iso)
            response = self.ask(day=iso)
            for person in (self.psy, self.other):
                grid = booking.bookable_slots(person, self.child, day)
                state = self.row(response, person)["state"]
                self.assertEqual(bool(grid), state == "open", (iso, person.email, state))
