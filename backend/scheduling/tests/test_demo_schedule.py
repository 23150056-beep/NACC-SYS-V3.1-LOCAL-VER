"""Mock data that the system itself would accept.

Two things were wrong with the seeded caseload, and the second one only became
visible once the booking rules got strict.

**Nobody had availability.** The three psychologists carrying all forty
children had no bookable window between them, so the one thing a staff member
opens the calendar to do - book a session for a child - was impossible for
every child in the database. The only psychologist with availability had no
caseload.

**The appointments were at night, at weekends.** `_appointments` placed them
at `now()` plus a few hours' jitter, so whatever time the seeder happened to
be run became the office's clinic hours. The demo showed a child-welfare
office running counselling at 23:22 on a Sunday.

The contract these tests hold is the one that makes demo data worth having:
**every scheduled appointment in it is one the booking endpoint would accept.**
Mock data the real rules would reject is not a sample of the system, it is a
second system that happens to share a database.
"""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from children.models import Child
from scheduling import booking, demo_schedule
from scheduling.models import Appointment, AvailabilityBlock
from scheduling.tests.test_api import SchedulingBase

User = get_user_model()


class AvailabilityForEveryPsychologistTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        # SchedulingBase gives self.psy one Wednesday block; self.other has none,
        # which is exactly the state the demo database was in.
        AvailabilityBlock.objects.all().delete()

    def test_it_gives_each_psychologist_a_working_week(self):
        demo_schedule.install_availability([self.psy, self.other])
        for person in (self.psy, self.other):
            weekdays = set(AvailabilityBlock.objects
                           .filter(psychologist=person)
                           .values_list("weekday", flat=True))
            self.assertEqual({0, 1, 2, 3, 4}, weekdays,
                             "a clinic that opens Monday to Friday")

    def test_the_windows_do_not_overlap(self):
        # The serializer refuses overlapping blocks because capacity would
        # double-count across them. Seeded data has to obey the same rule the
        # form does, or the first person to edit a block is told it is invalid.
        demo_schedule.install_availability([self.psy])
        for weekday in range(5):
            blocks = list(AvailabilityBlock.objects.filter(
                psychologist=self.psy, weekday=weekday).order_by("start_time"))
            for earlier, later in zip(blocks, blocks[1:]):
                self.assertLessEqual(earlier.end_time, later.start_time)

    def test_there_is_a_lunch_gap(self):
        demo_schedule.install_availability([self.psy])
        monday = AvailabilityBlock.objects.filter(
            psychologist=self.psy, weekday=0).order_by("start_time")
        self.assertEqual(2, monday.count(), "a morning and an afternoon")
        self.assertLess(monday[0].end_time, monday[1].start_time)

    def test_it_does_not_overlap_a_window_that_already_exists(self):
        """The availability form refuses overlapping blocks outright.

        A psychologist who already posted 09:00-12:00 on Mondays must not end
        up with 08:00-12:00 beside it: capacity double-counts across the pair,
        the slot grid offers the shared hours twice, and the first person to
        edit either one is told their own data is invalid.
        """
        AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=0, start_time=time(9, 0),
            end_time=time(12, 0), capacity=2)

        demo_schedule.install_availability([self.psy])

        monday = list(AvailabilityBlock.objects.filter(
            psychologist=self.psy, weekday=0).order_by("start_time"))
        for earlier, later in zip(monday, monday[1:]):
            self.assertLessEqual(earlier.end_time, later.start_time,
                                 f"{earlier.start_time}-{earlier.end_time} overlaps "
                                 f"{later.start_time}-{later.end_time}")

    def test_pruning_removes_an_overlapping_window_and_keeps_the_older(self):
        """Repairs a database an earlier, buggier run already damaged.

        The first version of install_availability wrote 08:00-12:00 straight
        over a psychologist's existing 09:00-12:00. Keeping the OLDER of the
        pair is the right way round: it is the one somebody chose.
        """
        original = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=0, start_time=time(9, 0),
            end_time=time(12, 0), capacity=2)
        intruder = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=0, start_time=time(8, 0),
            end_time=time(12, 0), capacity=6)

        removed = demo_schedule.prune_overlapping_blocks()

        self.assertEqual(1, removed)
        self.assertTrue(AvailabilityBlock.objects.filter(pk=original.pk).exists())
        self.assertFalse(AvailabilityBlock.objects.filter(pk=intruder.pk).exists())

    def test_pruning_a_clean_diary_removes_nothing(self):
        demo_schedule.install_availability([self.psy])
        before = AvailabilityBlock.objects.count()
        self.assertEqual(0, demo_schedule.prune_overlapping_blocks())
        self.assertEqual(before, AvailabilityBlock.objects.count())

    def test_running_it_again_adds_nothing(self):
        demo_schedule.install_availability([self.psy])
        before = AvailabilityBlock.objects.count()
        demo_schedule.install_availability([self.psy])
        self.assertEqual(before, AvailabilityBlock.objects.count())

    def test_it_leaves_a_window_somebody_edited_alone(self):
        demo_schedule.install_availability([self.psy])
        block = AvailabilityBlock.objects.filter(psychologist=self.psy,
                                                 weekday=0).first()
        block.capacity = 9
        block.save()
        demo_schedule.install_availability([self.psy])
        block.refresh_from_db()
        self.assertEqual(9, block.capacity)


class AppointmentsLandInClinicHoursTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        AvailabilityBlock.objects.all().delete()
        demo_schedule.install_availability([self.psy])
        self.child_b = Child.objects.create(fullname="Ben",
                                            assigned_psychologist=self.psy)

    def _at(self, days_from_now, hour, minute=22):
        """A local datetime `days_from_now` away, at a deliberately odd time."""
        return (timezone.localtime().replace(hour=hour, minute=minute,
                                             second=0, microsecond=0)
                + timedelta(days=days_from_now))

    def test_a_late_night_appointment_is_moved_into_the_day(self):
        appt = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=self._at(9, 23), duration_minutes=60)
        demo_schedule.realign_appointments()
        appt.refresh_from_db()
        local = timezone.localtime(appt.start)
        self.assertGreaterEqual(local.time(), time(8, 0))
        self.assertLessEqual(local.time(), time(17, 0))

    def test_a_weekend_appointment_is_moved_onto_a_weekday(self):
        # Find the next Saturday and put a session on it.
        today = timezone.localdate()
        saturday = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
        start = timezone.make_aware(
            datetime.combine(saturday, time(21, 22)))
        appt = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=start, duration_minutes=60)

        demo_schedule.realign_appointments()

        appt.refresh_from_db()
        self.assertLess(timezone.localtime(appt.start).weekday(), 5)

    def test_two_appointments_are_not_moved_on_top_of_each_other(self):
        for child in (self.child, self.child_b):
            Appointment.objects.create(child=child, psychologist=self.psy,
                                       start=self._at(9, 22), duration_minutes=60)
        demo_schedule.realign_appointments()

        starts = [a.start for a in Appointment.objects.all()]
        self.assertEqual(len(starts), len(set(starts)))

    def test_a_cancelled_appointment_is_left_where_it_is(self):
        appt = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self._at(9, 23),
            duration_minutes=60, status=Appointment.CANCELLED)
        was = appt.start
        demo_schedule.realign_appointments()
        appt.refresh_from_db()
        self.assertEqual(was, appt.start)

    def test_it_leaves_the_diary_with_room_to_book(self):
        """A demo where every day is full is as useless as one with no windows.

        Realigning packs sessions onto the nearest weekdays, and the seeded
        caseload puts every child on the same few dates - so without a ceiling
        the repair produces days that are correct and completely unbookable.
        """
        for day in (7, 7, 7, 7, 7, 7, 7, 7):
            Appointment.objects.create(child=self.child, psychologist=self.psy,
                                       start=self._at(day, 22),
                                       duration_minutes=60)
        demo_schedule.realign_appointments()

        by_day = {}
        for appt in Appointment.objects.all():
            local = timezone.localtime(appt.start)
            by_day.setdefault(local.date(), []).append(local.time())
        for day, times in by_day.items():
            self.assertLessEqual(
                len(times), demo_schedule.MAX_SESSIONS_PER_DAY,
                f"{day} was packed to {len(times)} with nothing left to book")

    def test_respread_thins_a_day_that_is_already_legal_but_packed(self):
        """An in-hours day can still be a useless one.

        Ordinary realignment leaves valid appointments alone, which is right -
        but a first pass that packed eight sessions into one day leaves a
        calendar nobody can book into, and every one of those sessions is
        perfectly valid. Respreading re-places them all.
        """
        demo_schedule.realign_appointments()
        for hour in (8, 9, 10, 11, 13, 14, 15, 16):
            Appointment.objects.create(
                child=self.child_b, psychologist=self.psy,
                start=self._at(7, hour, minute=0), duration_minutes=60)

        demo_schedule.realign_appointments(respread=True)

        by_day = {}
        for appt in Appointment.objects.exclude(status=Appointment.CANCELLED):
            local = timezone.localtime(appt.start)
            by_day.setdefault((appt.psychologist_id, local.date()), 0)
            by_day[(appt.psychologist_id, local.date())] += 1
        for key, count in by_day.items():
            self.assertLessEqual(count, demo_schedule.MAX_SESSIONS_PER_DAY, key)

    def test_every_FUTURE_appointment_would_now_be_accepted_by_the_booker(self):
        """The contract. Demo data the real rules reject is not demo data.

        Checked with the appointment excluded from its own clash search, which
        is exactly what the booking endpoint does when it validates a move.
        """
        for offset, child in ((7, self.child), (8, self.child_b), (9, self.child)):
            Appointment.objects.create(child=child, psychologist=self.psy,
                                       start=self._at(offset, 22),
                                       duration_minutes=60)
        demo_schedule.realign_appointments()

        for appt in Appointment.objects.filter(start__gt=timezone.now()):
            errors = booking.errors_for(
                appt.psychologist, appt.child, appt.start,
                appt.duration_minutes, exclude_id=appt.pk)
            self.assertEqual({}, errors,
                             f"{appt.child.fullname} at "
                             f"{timezone.localtime(appt.start)} would be refused")
