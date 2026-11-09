"""One availability window a day (owner, 9 Nov 2026).

A day holds one window and its slot count is the day's limit. Adding
availability to a day that has one means editing that day, a one-off date
replaces that weekday's weekly window for that date only, and a database that
already holds two windows on a day keeps working until somebody combines them.
"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from scheduling import availability, booking
from scheduling.models import Appointment, AvailabilityBlock
from scheduling.tests.test_api import SchedulingBase, next_weekday

URL = "/api/availability/"


class OneWindowARuleTest(SchedulingBase):
    def post(self, **body):
        return self.client.post(URL, body, format="json")

    def test_second_weekly_window_on_a_weekday_is_refused_with_the_existing_id(self):
        self._auth("p@racco1.gov.ph")
        r = self.post(weekday=2, start_time="13:00", end_time="15:00", capacity=1)
        self.assertEqual(400, r.status_code)
        self.assertEqual(self.block.id, r.data["existing_id"])
        self.assertIsInstance(r.data["existing_id"], int)
        self.assertIn("Wednesdays already have availability "
                      "(9:00 AM-12:00 PM, 2 slots). Edit that day instead.",
                      r.data["start_time"])
        self.assertEqual(1, AvailabilityBlock.objects.filter(psychologist=self.psy).count())

    def test_patching_a_window_onto_a_taken_weekday_is_refused(self):
        other_day = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=3, start_time="09:00", end_time="10:00")
        self._auth("p@racco1.gov.ph")
        r = self.client.patch(f"{URL}{other_day.id}/", {"weekday": 2}, format="json")
        self.assertEqual(400, r.status_code)
        self.assertEqual(self.block.id, r.data["existing_id"])
        other_day.refresh_from_db()
        self.assertEqual(3, other_day.weekday)

    def test_editing_the_window_a_day_already_has_is_fine(self):
        self._auth("p@racco1.gov.ph")
        r = self.client.patch(f"{URL}{self.block.id}/",
                              {"capacity": 3, "end_time": "13:00"}, format="json")
        self.assertEqual(200, r.status_code, r.data)

    def test_different_weekdays_are_fine(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(201, self.post(weekday=3, start_time="09:00",
                                        end_time="12:00").status_code)

    def test_an_inactive_window_does_not_count(self):
        self.block.active = False
        self.block.save()
        self._auth("p@racco1.gov.ph")
        self.assertEqual(201, self.post(weekday=2, start_time="13:00",
                                        end_time="15:00").status_code)

    def test_switching_an_inactive_window_back_on_onto_a_taken_day_is_refused(self):
        old = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2, start_time="13:00", end_time="15:00",
            active=False)
        self._auth("p@racco1.gov.ph")
        r = self.client.patch(f"{URL}{old.id}/?include_inactive=true", {"active": True}, format="json")
        self.assertEqual(400, r.status_code)
        self.assertEqual(self.block.id, r.data["existing_id"])

    def test_a_second_one_off_window_on_a_date_is_refused(self):
        day = next_weekday(4, 9).date().isoformat()
        self._auth("p@racco1.gov.ph")
        first = self.post(date=day, start_time="13:00", end_time="15:00", capacity=1)
        self.assertEqual(201, first.status_code, first.data)
        r = self.post(date=day, start_time="15:00", end_time="16:00")
        self.assertEqual(400, r.status_code)
        self.assertEqual(first.data["id"], r.data["existing_id"])

    def test_a_one_off_date_is_allowed_on_a_weekday_that_has_a_weekly_window(self):
        day = next_weekday(2, 9).date().isoformat()
        self._auth("p@racco1.gov.ph")
        self.assertEqual(201, self.post(date=day, start_time="13:00",
                                        end_time="15:00").status_code)

    def test_a_colleagues_windows_do_not_count(self):
        self._auth("o@racco1.gov.ph")
        self.assertEqual(201, self.post(weekday=2, start_time="09:00",
                                        end_time="12:00").status_code)

    def test_a_psychologist_cannot_probe_a_colleagues_day(self):
        self._auth("o@racco1.gov.ph")
        r = self.post(psychologist=self.psy.id, weekday=2,
                      start_time="13:00", end_time="15:00")
        # Filed under themselves, as ever: their Wednesday is free.
        self.assertIn(r.status_code, (201, 403))
        if r.status_code == 201:
            self.assertEqual(self.other.id, r.data["psychologist"])

    def test_patching_onto_a_colleague_is_forbidden_not_described(self):
        AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00", end_time="12:00")
        self._auth("p@racco1.gov.ph")
        r = self.client.patch(f"{URL}{self.block.id}/",
                              {"psychologist": self.other.id}, format="json")
        self.assertEqual(403, r.status_code)

    def test_the_admin_files_it_for_a_named_psychologist(self):
        self._auth("a@racco1.gov.ph")
        r = self.post(psychologist=self.psy.id, weekday=2,
                      start_time="13:00", end_time="15:00")
        self.assertEqual(400, r.status_code)
        self.assertEqual(self.block.id, r.data["existing_id"])

    def test_a_legacy_day_with_two_windows_can_still_be_edited(self):
        twin = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2, start_time="13:00", end_time="17:00",
            capacity=2)
        self._auth("p@racco1.gov.ph")
        r = self.client.patch(f"{URL}{twin.id}/", {"capacity": 4}, format="json")
        self.assertEqual(200, r.status_code, r.data)
        r = self.client.patch(f"{URL}{twin.id}/", {"start_time": "11:00"}, format="json")
        self.assertEqual(400, r.status_code)       # may not be stretched over its twin


class OneOffReplacesWeeklyTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.wed = next_weekday(2, 9).date()
        self.wed2 = self.wed + timedelta(days=7)
        self.dated = AvailabilityBlock.objects.create(
            psychologist=self.psy, date=self.wed, start_time="13:00",
            end_time="15:00", capacity=1)

    def at(self, day, hour):
        return timezone.make_aware(datetime.combine(day, time(hour, 0)))

    def test_the_weekly_hours_are_not_offered_on_the_overridden_date(self):
        starts = [s["start"] for s in booking.bookable_slots(self.psy, self.child, self.wed)]
        self.assertTrue(starts)
        self.assertTrue(all("13:00" <= s < "15:00" for s in starts), starts)
        weekly = [s["start"] for s in booking.bookable_slots(self.psy, self.child, self.wed2)]
        self.assertIn("09:00", weekly)

    def test_the_rule_refuses_the_weekly_hours_on_that_date(self):
        self.assertIn("start", booking.errors_for(self.psy, self.child, self.at(self.wed, 9), 60))
        self.assertEqual({}, booking.errors_for(self.psy, self.child, self.at(self.wed, 13), 60))

    def test_the_dated_windows_capacity_applies_and_does_not_add_to_the_weekly(self):
        self.assertEqual({}, booking.errors_for(self.psy, self.child, self.at(self.wed, 13), 60))
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=self.at(self.wed, 13), duration_minutes=60)
        # capacity 1 reached; the weekly window's second place is not borrowed
        self.assertIn("start", booking.errors_for(self.psy, self.child, self.at(self.wed, 14), 60))
        self.assertIn("fully booked",
                      booking.errors_for(self.psy, self.child, self.at(self.wed, 14), 60)["start"])
        self.assertEqual({}, booking.errors_for(self.psy, self.child, self.at(self.wed2, 9), 60))

    def test_the_month_view_agrees(self):
        month = booking.month_view(self.psy, self.child, self.wed.year, self.wed.month)
        day = next(d for d in month["days"] if d["date"] == self.wed.isoformat())
        self.assertTrue(all("13:00" <= s["start"] < "15:00" for s in day["slots"]))

    def test_why_empty_and_free_windows_follow_the_override(self):
        wins = availability.free_windows(self.psy, self.wed, self.wed + timedelta(days=1),
                                         now=timezone.localtime() - timedelta(days=30))
        self.assertEqual([("13:00", "15:00", 1)],
                         [(w["start"], w["end"], w["remaining"]) for w in wins])

    def test_booked_ahead_of_the_weekly_window_skips_the_overridden_date(self):
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=self.at(self.wed, 13), duration_minutes=60)
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=self.at(self.wed2, 10), duration_minutes=60)
        self._auth("p@racco1.gov.ph")
        rows = {b["id"]: b for b in self.client.get(URL).data}
        self.assertEqual(1, rows[self.block.id]["booked_ahead"])
        self.assertEqual(1, rows[self.dated.id]["booked_ahead"])

    def test_an_inactive_override_replaces_nothing(self):
        self.dated.active = False
        self.dated.save()
        starts = [s["start"] for s in booking.bookable_slots(self.psy, self.child, self.wed)]
        self.assertIn("09:00", starts)


class CombineTest(SchedulingBase):
    URL = "/api/availability/combine/"

    def setUp(self):
        super().setUp()
        self.twin = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2, start_time="13:00", end_time="17:00",
            capacity=3)

    def combine(self, ids):
        return self.client.post(self.URL, {"ids": ids}, format="json")

    def test_happy_path_keeps_lowest_id_spans_and_sums(self):
        self._auth("p@racco1.gov.ph")
        r = self.combine([self.twin.id, self.block.id])
        self.assertEqual(200, r.status_code, r.data)
        self.assertEqual(self.block.id, r.data["id"])
        self.assertEqual("09:00:00", r.data["start_time"])
        self.assertEqual("17:00:00", r.data["end_time"])
        self.assertEqual(5, r.data["capacity"])
        self.assertFalse(AvailabilityBlock.objects.filter(pk=self.twin.id).exists())

    def test_bookings_are_untouched(self):
        start = next_weekday(2, 14)
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=start, duration_minutes=60)
        self._auth("p@racco1.gov.ph")
        self.assertEqual(200, self.combine([self.block.id, self.twin.id]).status_code)
        appt.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, appt.status)
        self.assertEqual({}, booking.errors_for(self.psy, self.child,
                                                next_weekday(2, 11), 60))

    def test_different_days_are_refused(self):
        thu = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=3, start_time="09:00", end_time="12:00")
        self._auth("p@racco1.gov.ph")
        r = self.combine([self.block.id, thu.id])
        self.assertEqual(400, r.status_code)
        self.assertTrue(AvailabilityBlock.objects.filter(pk=thu.id).exists())

    def test_a_weekly_and_a_dated_window_are_not_the_same_day(self):
        dated = AvailabilityBlock.objects.create(
            psychologist=self.psy, date=next_weekday(2, 9).date(),
            start_time="09:00", end_time="10:00")
        self._auth("p@racco1.gov.ph")
        self.assertEqual(400, self.combine([self.block.id, dated.id]).status_code)

    def test_another_psychologists_ids_are_refused(self):
        theirs = AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00", end_time="12:00")
        self._auth("p@racco1.gov.ph")
        self.assertEqual(403, self.combine([self.block.id, theirs.id]).status_code)
        self.assertTrue(AvailabilityBlock.objects.filter(pk=theirs.id).exists())

    def test_the_admin_may_combine_for_anybody_but_not_across_people(self):
        theirs = AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00", end_time="12:00")
        self._auth("a@racco1.gov.ph")
        self.assertEqual(400, self.combine([self.block.id, theirs.id]).status_code)
        self.assertEqual(200, self.combine([self.block.id, self.twin.id]).status_code)

    def test_staff_cannot(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(403, self.combine([self.block.id, self.twin.id]).status_code)

    def test_bad_bodies_and_inactive_windows_are_refused(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(400, self.combine([self.block.id]).status_code)
        self.assertEqual(400, self.combine([self.block.id, self.block.id]).status_code)
        self.assertEqual(400, self.combine(["x", 2]).status_code)
        self.assertEqual(400, self.combine([self.block.id, 999999]).status_code)
        self.twin.active = False
        self.twin.save()
        self.assertEqual(400, self.combine([self.block.id, self.twin.id]).status_code)

    def test_anonymous_is_refused(self):
        self.assertEqual(401, self.combine([1, 2]).status_code)


class FoldOneOffsMigrationTest(SchedulingBase):
    """A one-off used to ADD hours beside the weekly window. The data step
    widens each upcoming pair into one window so nothing is lost."""

    def fold(self):
        import importlib
        from django.apps import apps
        mod = importlib.import_module("scheduling.migrations.0009_fold_one_offs_into_weekday")
        mod.fold_one_offs(apps, None)

    def test_an_additive_one_off_keeps_the_weekly_morning_after_the_rule(self):
        wed = next_weekday(2, 9).date()
        extra = AvailabilityBlock.objects.create(
            psychologist=self.psy, date=wed, start_time="13:00", end_time="17:00",
            capacity=3)
        self.fold()
        extra.refresh_from_db()
        self.assertEqual((time(9, 0), time(17, 0), 5),
                         (extra.start_time, extra.end_time, extra.capacity))
        starts = [s["start"] for s in booking.bookable_slots(self.psy, self.child, wed)]
        self.assertIn("09:00", starts)
        self.assertIn("13:00", starts)
        # the weekly window itself is untouched
        self.block.refresh_from_db()
        self.assertEqual(2, self.block.capacity)

    def test_a_one_off_on_a_day_with_no_weekly_window_and_a_past_one_are_left(self):
        thu = next_weekday(3, 9).date()
        lone = AvailabilityBlock.objects.create(
            psychologist=self.psy, date=thu, start_time="13:00", end_time="15:00")
        past_wed = timezone.localdate() - timedelta(days=(timezone.localdate().weekday() - 2) % 7 + 7)
        old = AvailabilityBlock.objects.create(
            psychologist=self.psy, date=past_wed, start_time="13:00", end_time="15:00")
        self.fold()
        lone.refresh_from_db(); old.refresh_from_db()
        self.assertEqual(time(13, 0), lone.start_time)
        self.assertEqual(time(13, 0), old.start_time)


class OutsideHoursTest(SchedulingBase):
    def test_a_one_off_that_strands_a_session_says_how_many(self):
        self.block.end_time = "17:00"
        self.block.save()
        wed = next_weekday(2, 15).date()
        Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=timezone.make_aware(datetime.combine(wed, time(15, 0))),
            duration_minutes=60)
        self._auth("p@racco1.gov.ph")
        r = self.client.post(URL, {"date": wed.isoformat(), "start_time": "09:00",
                                   "end_time": "11:00", "capacity": 1}, format="json")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual(1, r.data["outside_hours"])

    def test_nothing_outside_reads_zero(self):
        self._auth("p@racco1.gov.ph")
        r = self.client.get(URL)
        self.assertEqual(0, r.data[0]["outside_hours"])


class CombineMustCoverTheDayTest(SchedulingBase):
    def test_combining_two_around_a_third_is_refused(self):
        self.block.delete()
        a = AvailabilityBlock.objects.create(psychologist=self.psy, weekday=1,
                                             start_time="08:00", end_time="10:00")
        AvailabilityBlock.objects.create(psychologist=self.psy, weekday=1,
                                         start_time="11:00", end_time="12:00")
        c = AvailabilityBlock.objects.create(psychologist=self.psy, weekday=1,
                                             start_time="14:00", end_time="16:00")
        self._auth("p@racco1.gov.ph")
        r = self.client.post("/api/availability/combine/", {"ids": [a.id, c.id]}, format="json")
        self.assertEqual(400, r.status_code)
        self.assertEqual(3, AvailabilityBlock.objects.filter(psychologist=self.psy).count())
        a.refresh_from_db()
        self.assertEqual(time(10, 0), a.end_time)

    def test_combining_every_window_on_the_day_works(self):
        self.block.delete()
        ids = [AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=1, start_time=s, end_time=e).id
            for s, e in (("08:00", "10:00"), ("11:00", "12:00"), ("14:00", "16:00"))]
        self._auth("p@racco1.gov.ph")
        r = self.client.post("/api/availability/combine/", {"ids": ids}, format="json")
        self.assertEqual(200, r.status_code, r.data)
