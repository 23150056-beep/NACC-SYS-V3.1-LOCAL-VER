from datetime import timedelta

from django.conf import settings
from django.db import models

from children.models import Child
from clinical.models import PreAssessment


class AvailabilityBlock(models.Model):
    """A psychologist's bookable window: either a recurring weekday block or a
    one-off dated block. Staff/admin book appointments against these."""
    WEEKDAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"),
                (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")]

    psychologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="availability_blocks")
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS, null=True, blank=True)
    date = models.DateField(null=True, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    capacity = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_availability_block"
        ordering = ["weekday", "date", "start_time"]

    def covers(self, dt):
        """Does this block cover local datetime `dt`?"""
        if not self.active:
            return False
        d, t = dt.date(), dt.time()
        if self.date is not None and self.date != d:
            return False
        if self.date is None and self.weekday is not None and self.weekday != d.weekday():
            return False
        if self.date is None and self.weekday is None:
            return False
        return self.start_time <= t < self.end_time


class Appointment(models.Model):
    PRE_ASSESSMENT = "pre_assessment"
    SESSION = "session"
    FOLLOW_UP = "follow_up"
    PURPOSE_CHOICES = [
        (PRE_ASSESSMENT, "Pre-Assessment"),
        (SESSION, "Session"),
        (FOLLOW_UP, "Follow-up"),
    ]

    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [(SCHEDULED, "Scheduled"), (COMPLETED, "Completed"),
                      (NO_SHOW, "No-show"), (CANCELLED, "Cancelled")]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="appointments")
    psychologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appointments")
    start = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=SESSION)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SCHEDULED)
    pre_assessment = models.ForeignKey(
        PreAssessment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments")
    notes = models.CharField(max_length=255, blank=True)
    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="appointments_booked")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_appointment"
        ordering = ["start"]


class Unavailability(models.Model):
    """A range of dates when a psychologist is not seeing children.

    AvailabilityBlock can only express presence, so the only way to say
    somebody was away was to delete the weekday window and add it back
    afterwards - which loses whatever capacity had been tuned and silently
    strands every session already booked into it.

    A date RANGE rather than a weekday, because that is the shape leave
    actually takes: "the 12th to the 16th", not "Tuesdays". Inclusive at both
    ends, which is how anybody writing it down means it.

    Leave, a training day and a court appearance are the same thing to this
    model - somebody is not there - so `reason` is free text rather than a
    choice list nobody would agree on.

    Declaring one never cancels an appointment. Those were agreed with
    somebody, and quietly dropping them as a side effect of recording leave
    would be worse than the gap this closes; the screen warns instead.
    """

    psychologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="unavailability")
    starts_on = models.DateField()
    ends_on = models.DateField()
    reason = models.CharField(max_length=140, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="unavailability_declared")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_unavailability"
        ordering = ["starts_on", "id"]

    def covers(self, day):
        return self.starts_on <= day <= self.ends_on


class SessionReminder(models.Model):
    """The record that a psychologist was texted about one day's sessions.

    One row per psychologist per day, and the database's unique constraint is
    what stops a second text. It used to be a cache key, and the cache is the
    default in-memory one - per process - so `manage.py
    send_session_reminders`, a fresh process every run, always found it empty
    and would have texted everybody again; and two overlapping runs could both
    pass a check-then-set.

    A row is claimed before sending and stamped `sent_at` once the gateway
    accepts; a refusal deletes it, so the next run retries. A claim never
    stamped - the process died mid-send - is taken over by a later run once
    it is older than CLAIM_LEASE, rather than read as "already told" forever.
    """

    # Comfortably longer than a send can take (a twenty-second timeout), so
    # a claim this old belongs to a run that is gone, not one still sending.
    CLAIM_LEASE = timedelta(minutes=10)

    psychologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="session_reminders")
    day = models.DateField()
    session_count = models.PositiveSmallIntegerField()
    claimed_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tbl_session_reminder"
        ordering = ["-day", "psychologist_id"]
        constraints = [
            models.UniqueConstraint(fields=["psychologist", "day"],
                                    name="one_session_reminder_per_day"),
        ]
