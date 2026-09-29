"""The custodian: who the child lives with now, and texting them (29 Sep 2026).

"Previous Custodian" became the Custodian (children 0028), with a contact
number the system may text about appointments - only when the custodian
agreed and the number was confirmed with a one-time code. children/custodian.py
holds the rules, accounts/sms_notifications.py section 4 the wording.
"""
from datetime import datetime, timedelta
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import sms_notifications
from accounts.models import Role
from accounts.sms import SmsResult, fits_one_segment
from children import custodian, demo_custodians, intake
from children.models import Child, CustodianContactCheck
from children.tests.payloads import complete
from scheduling.models import Appointment, CustodianReminder
from scheduling.reminders import send_session_reminders
from scheduling.tests.test_api import give_referral

User = get_user_model()
NUMBER = "+639171234567"


def fake_send(sent):
    def _send(number, text, description="message", otp_code=None):
        sent.append((number, text.replace("{otp}", otp_code) if otp_code else text))
        return SmsResult(True, "captured", code=otp_code)
    return _send


class CustodianBase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        self.sw = User.objects.create_user(email="sw@t.ph", username="sw", password="x",
                                           first_name="Editha", last_name="Pascua",
                                           role=roles[Role.STAFF])
        self.sw2 = User.objects.create_user(email="sw2@t.ph", username="sw2", password="x",
                                            role=roles[Role.STAFF])
        self.psy = User.objects.create_user(email="p@t.ph", username="p", password="x",
                                            role=roles[Role.PSYCHOLOGIST])
        self.child = Child.objects.create(
            first_name="Ana", last_name="Cruz", fullname="Ana Cruz", case_type="Foster Care",
            custodian_name="Rosa Dela Cruz (foster parent)", social_worker=self.sw,
            assigned_psychologist=self.psy)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _confirm(self, user=None, number=NUMBER):
        """Send a code and read it back, as the social worker at intake. The
        once-a-minute limit is lifted first: a test that confirms twice is
        not the abuse it exists to stop (test_codes_are_rate_limited is)."""
        CustodianContactCheck.objects.update(last_sent_at=None)
        sent = []
        with patch("accounts.sms.send_sms", side_effect=fake_send(sent)):
            res = self._as(user or self.sw).post("/api/custodian-contact/code/",
                                                 {"number": number}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        code = sent[-1][1].split("code is ")[1][:6]
        res = self._as(user or self.sw).put("/api/custodian-contact/code/",
                                            {"number": number, "code": code}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        return sent

    def _patch(self, user=None, **fields):
        return self._as(user or self.sw).patch(f"/api/children/{self.child.id}/", fields,
                                               format="json")

    def _texts_on(self):
        self._confirm()
        res = self._patch(custodian_contact="0917 123 4567", custodian_sms_consent=True)
        self.assertEqual(200, res.status_code, res.data)
        self.child.refresh_from_db()
        self.assertTrue(custodian.texts_allowed(self.child))


class TheFieldTest(CustodianBase):
    def test_the_field_is_the_custodian_on_the_same_column(self):
        field = Child._meta.get_field("custodian_name")
        self.assertEqual("surrendered_by", field.column,
                         "the rename must not touch the database column")
        self.assertIn("custodian_name", intake.CASE_TYPE_FIELDS["Foster Care"])

    def test_it_is_still_required_where_the_case_asks_it(self):
        res = self._as(self.sw).post("/api/children/", complete(custodian_name=""), format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("custodian_name", res.data)

    def test_the_old_placeholder_values_are_cleared_by_the_migration(self):
        clear = import_module("children.migrations.0028_custodian").clear_placeholders
        kept = Child.objects.create(fullname="Ben Lim", custodian_name="Lorna Bautista (aunt)")
        for value in ("Social Worker", "Police", "Relatives"):
            Child.objects.create(fullname=f"Kid {value}", custodian_name=value)
        clear(apps, None)
        self.assertEqual(0, Child.objects.filter(
            custodian_name__in=["Social Worker", "Police", "Relatives"]).count())
        kept.refresh_from_db()
        self.assertEqual("Lorna Bautista (aunt)", kept.custodian_name)


class ContactAndConsentTest(CustodianBase):
    def test_a_number_is_stored_the_gateways_way_and_shown_the_readers_way(self):
        res = self._patch(custodian_contact="0917 123 4567")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual((NUMBER, "0917 123 4567"),
                         (res.data["custodian_contact"], res.data["custodian_contact_display"]))

    def test_a_landline_is_refused(self):
        res = self._patch(custodian_contact="(072) 888 1234")
        self.assertEqual(400, res.status_code)
        self.assertIn("landline", str(res.data["custodian_contact"]))

    def test_consent_needs_a_number(self):
        res = self._patch(custodian_sms_consent=True)
        self.assertEqual(400, res.status_code)
        self.assertIn("custodian_sms_consent", res.data)

    def test_consent_records_who_and_when(self):
        res = self._patch(custodian_contact="09171234567", custodian_sms_consent=True)
        self.assertEqual(200, res.status_code, res.data)
        self.child.refresh_from_db()
        self.assertEqual(self.sw, self.child.custodian_sms_consent_by)
        self.assertIsNotNone(self.child.custodian_sms_consent_at)
        self.assertEqual("Editha Pascua", res.data["custodian_sms_consent_by_name"])

    def test_a_new_custodian_or_number_clears_consent_and_confirmation(self):
        self._texts_on()
        self._patch(custodian_name="Mercedes Soriano (foster parent)")
        self.child.refresh_from_db()
        self.assertFalse(self.child.custodian_sms_consent)
        self._texts_on()
        self._patch(custodian_contact="0918 765 4321")
        self.child.refresh_from_db()
        self.assertFalse(self.child.custodian_sms_consent)
        self.assertIsNone(self.child.custodian_contact_verified_at)
        self.assertFalse(custodian.texts_allowed(self.child))

    def test_an_unrelated_edit_keeps_both(self):
        self._texts_on()
        self._patch(medical_notes="Asthma.")
        self.child.refresh_from_db()
        self.assertTrue(custodian.texts_allowed(self.child))

    def test_withdrawing_consent_stops_texts(self):
        self._texts_on()
        self._patch(custodian_sms_consent=False)
        self.child.refresh_from_db()
        self.assertFalse(custodian.texts_allowed(self.child))
        self.assertIsNone(self.child.custodian_sms_consent_by)

    def test_a_psychologist_cannot_change_any_of_it(self):
        res = self._patch(user=self.psy, custodian_contact="09171234567")
        self.assertEqual(400, res.status_code)
        res = self._patch(user=self.psy, custodian_name=self.child.custodian_name,
                          medical_notes="Asthma.")
        self.assertEqual(200, res.status_code, "resending the same values is fine")

    def test_a_case_moved_off_custodian_care_takes_the_custodian_with_it(self):
        """A psychologist's case-type change used to be unsavable: the form
        blanks the custodian the new type does not ask for, and the refusal
        landed on a field that type does not show (29 Sep 2026)."""
        self._texts_on()
        res = self._patch(user=self.psy, case_type="Residential Care", custodian_name="",
                          custodian_contact="", custodian_sms_consent=False,
                          date_of_placement_to_custodian=None, date_of_admission="2026-03-02")
        self.assertEqual(200, res.status_code, res.data)
        self.child.refresh_from_db()
        self.assertEqual(("", ""), (self.child.custodian_name, self.child.custodian_contact))
        self.assertIsNone(self.child.custodian_contact_verified_at)
        self.assertFalse(custodian.texts_allowed(self.child))

    def test_a_psychologist_is_not_asked_for_a_custodian_they_cannot_record(self):
        Child.objects.filter(pk=self.child.pk).update(custodian_name="")
        res = self._patch(user=self.psy, case_type="Kinship Care",
                          date_of_placement_to_custodian="2026-03-01")
        self.assertEqual(200, res.status_code, res.data)
        res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                      {"case_type": "Foster Care",
                                       "date_of_placement_to_custodian": "2026-03-01"},
                                      format="json")
        self.assertEqual(400, res.status_code, "the social worker still is")
        self.assertIn("custodian_name", res.data)

    def test_but_only_blanking_it_and_only_off_custodian_care(self):
        for over in ({"custodian_name": ""},
                     {"case_type": "Kinship Care", "custodian_name": "",
                      "date_of_placement_to_custodian": "2026-03-01"},
                     {"case_type": "Residential Care", "custodian_name": "Someone else",
                      "date_of_admission": "2026-03-02"}):
            res = self._patch(user=self.psy, **over)
            self.assertEqual(400, res.status_code, over)
            self.assertIn("custodian_name", res.data, over)


class OneTimeCodeTest(CustodianBase):
    def test_a_confirmed_number_is_put_on_the_record_by_the_save(self):
        self._confirm()
        res = self._patch(custodian_contact="0917 123 4567", custodian_sms_consent=True)
        self.assertTrue(res.data["custodian_contact_verified"])
        self.assertEqual({"on": True, "status": "Texts on"}, res.data["custodian_texts"])

    def test_without_the_code_texts_stay_off(self):
        res = self._patch(custodian_contact="0917 123 4567", custodian_sms_consent=True)
        self.assertFalse(res.data["custodian_contact_verified"])
        self.assertEqual("Number not confirmed - texts off", res.data["custodian_texts"]["status"])

    def test_the_code_text_carries_no_name_and_fits_one_segment(self):
        sent = self._confirm()
        number, text = sent[-1]
        self.assertEqual(NUMBER, number)
        self.assertNotIn("Ana", text)
        self.assertTrue(fits_one_segment(text))
        self.assertFalse(text.upper().startswith("TEST"))

    def test_a_wrong_code_is_refused_and_counted(self):
        with patch("accounts.sms.send_sms", side_effect=fake_send([])):
            self._as(self.sw).post("/api/custodian-contact/code/", {"number": NUMBER}, format="json")
        res = self._as(self.sw).put("/api/custodian-contact/code/",
                                    {"number": NUMBER, "code": "000000x"}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("attempts left", res.data["code"])

    def test_one_persons_confirmation_is_not_anothers(self):
        self._confirm(user=self.sw2)
        res = self._patch(custodian_contact="0917 123 4567")
        self.assertFalse(res.data["custodian_contact_verified"])

    def test_a_confirmation_goes_stale(self):
        self._confirm()
        CustodianContactCheck.objects.update(
            verified_at=timezone.now() - custodian.CONFIRMED_FOR - timedelta(minutes=1))
        res = self._patch(custodian_contact="0917 123 4567")
        self.assertFalse(res.data["custodian_contact_verified"])

    def test_codes_are_rate_limited(self):
        with patch("accounts.sms.send_sms", side_effect=fake_send([])):
            first = self._as(self.sw).post("/api/custodian-contact/code/", {"number": NUMBER}, format="json")
            again = self._as(self.sw).post("/api/custodian-contact/code/", {"number": NUMBER}, format="json")
        self.assertEqual(200, first.status_code)
        self.assertEqual(429, again.status_code)

    def test_only_staff_and_the_isa_send_codes(self):
        res = self._as(self.psy).post("/api/custodian-contact/code/", {"number": NUMBER}, format="json")
        self.assertEqual(403, res.status_code)


class AppointmentTextsTest(CustodianBase):
    def setUp(self):
        super().setUp()
        give_referral(self.child, self.sw)
        self.start = (timezone.localtime() + timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0)

    def _book(self, start=None):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._as(self.psy).post("/api/appointments/", {
                "child": self.child.id, "psychologist": self.psy.id,
                "start": (start or self.start).isoformat(), "duration_minutes": 60,
                "purpose": "session"}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        return res.data["id"]

    def _queued(self, action):
        sent = []
        with patch.object(sms_notifications, "queue_sms",
                          side_effect=lambda n, t, d: sent.append((n, t)) or True):
            action()
        return sent

    def test_booked_moved_and_cancelled_are_texted_when_texts_are_on(self):
        self._texts_on()
        sent = self._queued(self._book)
        self.assertEqual(1, len(sent))
        self.assertEqual(NUMBER, sent[0][0])
        self.assertIn("An appointment is set", sent[0][1])
        appt = Appointment.objects.get()
        later = self.start + timedelta(days=1)
        moved = self._queued(lambda: self._as(self.psy).patch(
            f"/api/appointments/{appt.id}/", {"start": later.isoformat()}, format="json"))
        self.assertIn("is moved to", moved[0][1])
        cancelled = self._queued(lambda: self._as(self.psy).post(
            f"/api/appointments/{appt.id}/cancel/"))
        self.assertIn("is cancelled", cancelled[0][1])

    def test_nothing_is_texted_without_consent(self):
        self._patch(custodian_contact="0917 123 4567")
        self.assertEqual([], self._queued(self._book))

    def test_a_no_show_the_same_day_is_texted_and_a_late_one_is_not(self):
        self._texts_on()
        today = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=timezone.now() - timedelta(minutes=5), purpose="session")
        sent = self._queued(lambda: self._as(self.psy).post(f"/api/appointments/{today.id}/no_show/"))
        self.assertIn("We missed you", sent[0][1])
        old = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=timezone.now() - timedelta(days=3), purpose="session")
        self.assertEqual([], self._queued(
            lambda: self._as(self.psy).post(f"/api/appointments/{old.id}/no_show/")))

    def test_the_day_before_reminder_is_sent_once(self):
        self._texts_on()
        tomorrow = (timezone.localtime() + timedelta(days=1)).replace(
            hour=9, minute=30, second=0, microsecond=0)
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=tomorrow, purpose="session")
        sent = []
        with patch.object(sms_notifications, "send_sms", side_effect=fake_send(sent)):
            send_session_reminders()
            send_session_reminders()
        custodian_texts = [t for n, t in sent if n == NUMBER]
        self.assertEqual(1, len(custodian_texts))
        self.assertIn("appointment tomorrow", custodian_texts[0])
        self.assertIn("9:30 AM", custodian_texts[0])
        self.assertEqual(1, CustodianReminder.objects.filter(sent_at__isnull=False).count())

    def test_a_closed_case_is_not_texted(self):
        self._texts_on()
        self.child.status = Child.INACTIVE
        self.child.save()
        self.assertFalse(custodian.texts_allowed(self.child))


class TheWordingTest(SimpleTestCase):
    """Each text is one plain GSM-7 segment, never starts with TEST, and names
    no child - at the longest a date and time can be written."""

    def test_every_custodian_text(self):
        class Appt:
            start = timezone.make_aware(datetime(2026, 9, 30, 12, 30))
        for event in sms_notifications.CUSTODIAN_TEXTS:
            for day in ("tomorrow", "today"):
                text = sms_notifications.custodian_text(event, Appt, day=day)
                self.assertTrue(fits_one_segment(text), (event, len(text), text))
                self.assertFalse(text.upper().startswith("TEST"), event)
                self.assertNotIn("{", text, event)

    def test_times_read_the_way_people_write_them(self):
        at = timezone.make_aware(datetime(2026, 9, 30, 9, 5))
        self.assertEqual("Wed 30 Sep, 9:05 AM", sms_notifications.custodian_when(at))
        noon = timezone.make_aware(datetime(2026, 9, 30, 12, 0))
        self.assertEqual("Wed 30 Sep, 12:00 PM", sms_notifications.custodian_when(noon))


class DemoCustodiansTest(TestCase):
    def test_demo_children_get_a_custodian_where_the_case_asks_and_no_number(self):
        kin = Child.objects.create(fullname="Ana Cruz", last_name="Cruz", case_type="Kinship Care")
        residential = Child.objects.create(fullname="Ben Lim", case_type="Residential Care")
        kept = Child.objects.create(fullname="Cara Diaz", case_type="Foster Care",
                                    custodian_name="Already recorded")
        self.assertEqual(1, demo_custodians.fill_custodians([kin, residential, kept]))
        kin.refresh_from_db()
        residential.refresh_from_db()
        kept.refresh_from_db()
        self.assertIn("Cruz", kin.custodian_name, "kin share the child's surname")
        self.assertEqual("", residential.custodian_name)
        self.assertEqual("Already recorded", kept.custodian_name)
        self.assertEqual("", kin.custodian_contact, "an invented number is a stranger's phone")

    def test_an_older_fixture_is_upgraded(self):
        upgrade = import_module("children.management.commands.import_demo_data").upgrade_rows
        rows = [{"model": "children.child", "fields": {"surrendered_by": "Police"}},
                {"model": "children.child", "fields": {"surrendered_by": "Lorna Bautista"}}]
        self.assertTrue(upgrade(rows))
        self.assertEqual([{"custodian_name": ""}, {"custodian_name": "Lorna Bautista"}],
                         [r["fields"] for r in rows])

