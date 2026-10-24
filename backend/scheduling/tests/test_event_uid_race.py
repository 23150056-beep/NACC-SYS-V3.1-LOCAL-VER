"""Two first invites for one appointment, handled at the same moment, still
share one UID (scheduling/session_mail.py `_invite`).

The rows are per attendee, so each send locks only its own; the UID is shared,
so the appointment is locked first. Without that lock the two sends would each
find no UID, each draw one, and the session would be two events in the two
calendars. SQLite takes no row locks (and serialises writers anyway), so this
only means something on PostgreSQL, which is what the hosted copies run: it is
skipped elsewhere rather than passing on a database that cannot fail it.
"""
import threading
from datetime import date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from accounts.models import Role
from children.models import Child
from scheduling.calendar_invite import build_ics
from scheduling.models import Appointment, CalendarInvite
from scheduling.session_mail import _invite

User = get_user_model()


class FirstInvitesTogetherTest(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("row locks need PostgreSQL")
        psychologist = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff = Role.objects.create(role_name=Role.STAFF)
        make = lambda email, role: User.objects.create_user(  # noqa: E731
            email=email, username=email, password="pass12345", role=role)
        self.first, self.second = make("a@t.ph", psychologist), make("b@t.ph", psychologist)
        sw = make("sw@t.ph", staff)
        child = Child.objects.create(
            first_name="Ana", last_name="Cruz", fullname="Ana Cruz",
            birth_date=date(2016, 5, 4), case_type="Foster Care",
            case_category="Neglected", social_worker=sw)
        self.appointment = Appointment.objects.create(
            child=child, psychologist=self.first, booked_by=sw, duration_minutes=60,
            start=timezone.make_aware(datetime(2026, 10, 21, 9, 30)))

    def test_the_second_waits_for_the_first_and_uses_its_id(self):
        inside, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)            # a failed assertion must not leave it waiting
        result, errors = {}, []
        real_build = build_ics

        def build(appointment, attendee, **kwargs):
            if attendee.pk == self.first.pk:
                inside.set()                    # its locks are taken, nothing committed
                release.wait(timeout=20)
            return real_build(appointment, attendee, **kwargs)

        def send(name, attendee):
            try:
                _, ics = _invite(self.appointment, attendee, "REQUEST")()
                result[name] = [line for line in ics.split("\r\n")
                                if line.startswith("UID:")][0]
            except Exception as exc:                                   # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        with patch("scheduling.session_mail.build_ics", side_effect=build):
            slow = threading.Thread(target=send, args=("slow", self.first))
            slow.start()
            self.assertTrue(inside.wait(timeout=20), "the first send never got its locks")
            quick = threading.Thread(target=send, args=("quick", self.second))
            quick.start()
            quick.join(timeout=1.5)
            self.assertTrue(quick.is_alive(),
                            "the second send did not wait for the first: it can draw its own id")
            release.set()
            slow.join(timeout=20)
            quick.join(timeout=20)
        self.assertEqual([], errors)
        self.assertEqual(result["slow"], result["quick"])
        self.assertEqual({result["slow"].removeprefix("UID:")}, set(
            CalendarInvite.objects.filter(appointment=self.appointment)
            .values_list("uid", flat=True)))
        self.assertEqual(2, CalendarInvite.objects.filter(appointment=self.appointment).count())
