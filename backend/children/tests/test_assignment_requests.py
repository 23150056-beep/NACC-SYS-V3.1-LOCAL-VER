"""Assigning is asking (owner's request, 28 Sep 2026).

Picking a psychologist on a record asks them; the child joins their records
only when they accept. A decline carries a reason back to the social worker.
children/assignment.py holds the rule, and the design is in
docs/superpowers/specs/2026-09-28-assignment-acceptance-design.md.

The first group of tests is the one that matters most: a pending child must
be absent from the asked psychologist's records by EVERY door, which holds
only because nothing but acceptance writes `assigned_psychologist`.
"""
from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.sms import fits_one_segment
from activity.models import ActivityLog
from children.models import AssignmentRequest, Child
from children.tests.payloads import complete
from scheduling.models import Appointment, AvailabilityBlock
from scheduling.tests.test_api import give_referral

User = get_user_model()


class AssignmentBase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        self.sw = make("sw@t.ph", "Editha", "Pascua", Role.STAFF)
        self.other_sw = make("sw2@t.ph", "Rosa", "Santos", Role.STAFF)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.psy2 = make("psy2@t.ph", "Jose", "Rizal", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(
            first_name="Ana", last_name="Cruz", fullname="Ana Cruz",
            birth_date=timezone.localdate() - timedelta(days=9 * 366),
            case_type="Foster Care", case_category="Neglected",
            referral_reason="School refusal since June.",
            social_worker=self.sw)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _ask(self, psychologist, by=None, child=None, **extra):
        child = child or self.child
        res = self._as(by or self.sw).patch(
            f"/api/children/{child.id}/", {"psychologist": psychologist.id, **extra},
            format="json")
        self.assertEqual(200, res.status_code, res.data)
        return AssignmentRequest.objects.filter(child=child).first()

    def _answer(self, req, verb, user=None, **body):
        return self._as(user or self.psy).post(
            f"/api/assignment-requests/{req.id}/{verb}/", body, format="json")


class NotInTheirRecordsUntilAcceptedTest(AssignmentBase):
    def test_a_new_record_with_a_psychologist_asks_rather_than_assigns(self):
        res = self._as(self.sw).post("/api/children/", complete(psychologist=self.psy.id),
                                     format="json")
        self.assertEqual(201, res.status_code, res.data)
        child = Child.objects.get(pk=res.data["id"])
        self.assertIsNone(child.assigned_psychologist)
        req = AssignmentRequest.objects.get(child=child)
        self.assertEqual((self.psy, AssignmentRequest.PENDING, self.sw),
                         (req.psychologist, req.status, req.requested_by))
        # The response already says who was asked.
        self.assertEqual(self.psy.id, res.data["pending_assignment"]["psychologist"])
        self.assertIsNone(res.data["psychologist"])

    def test_the_isa_asks_too(self):
        """One door. An ISA assignment that skipped the question would be a
        second one, and the local sign-in is the ISA's."""
        self._ask(self.psy, by=self.admin)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.assigned_psychologist)

    def test_a_pending_child_is_absent_from_every_door(self):
        self._ask(self.psy)
        client = self._as(self.psy)
        self.assertNotIn(self.child.id, {c["id"] for c in client.get("/api/children/").data})
        self.assertEqual(404, client.get(f"/api/children/{self.child.id}/").status_code)
        self.assertEqual(404, client.get(f"/api/reports/child/{self.child.id}/").status_code)
        self.assertEqual(404, client.get(
            f"/api/appointments/next-slots/?child={self.child.id}").status_code)
        self.assertEqual(404, client.get(
            f"/api/availability/openings/?child={self.child.id}").status_code)

    def test_but_the_request_is_in_their_queue_with_what_they_need_to_decide(self):
        self._ask(self.psy)
        rows = self._as(self.psy).get("/api/assignment-requests/?status=pending").data
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(("Ana Cruz", f"C-{self.child.id:04d}", 9, "Foster Care",
                          "School refusal since June.", False, "Editha Pascua"),
                         (row["child_name"], row["child_ref"], row["child_age"],
                          row["case_type"], row["referral_reason"],
                          row["has_case_referral"], row["requested_by_name"]))

    def test_the_preview_carries_nothing_clinical(self):
        self._ask(self.psy)
        row = self._as(self.psy).get("/api/assignment-requests/").data[0]
        for leak in ("remarks", "reports", "pre_assessments", "self_reports",
                     "medical_notes", "recommendation", "address", "photo"):
            self.assertNotIn(leak, row)

    def test_a_transfer_leaves_the_child_where_it_is_until_accepted(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        self._ask(self.psy2)
        self.child.refresh_from_db()
        self.assertEqual(self.psy, self.child.assigned_psychologist)
        self.assertEqual(200, self._as(self.psy).get(f"/api/children/{self.child.id}/").status_code)
        self.assertEqual(404, self._as(self.psy2).get(f"/api/children/{self.child.id}/").status_code)
        row = self._as(self.psy2).get("/api/assignment-requests/").data[0]
        self.assertEqual("Marivic Bulan", row["previous_psychologist_name"])


class AcceptTest(AssignmentBase):
    def test_accepting_puts_the_child_in_their_records(self):
        req = self._ask(self.psy)
        res = self._answer(req, "accept")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("accepted", res.data["status"])
        self.child.refresh_from_db()
        self.assertEqual(self.psy, self.child.assigned_psychologist)
        self.assertIn(self.child.id, {c["id"] for c in self._as(self.psy).get("/api/children/").data})

    def test_the_social_worker_hears_about_it(self):
        req = self._ask(self.psy)
        self._answer(req, "accept")
        self.assertTrue(ActivityLog.objects.filter(
            action=ActivityLog.ACCEPTED, entity_type="Assignment",
            entity_id=self.child.id, recipient=self.sw).exists())
        feed = self._as(self.sw).get("/api/activity/").data
        self.assertIn("accepted", {e["action"] for e in feed})

    def test_the_psychologist_who_held_the_child_hears_it_moved(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        self._answer(req, "accept", user=self.psy2)
        self.assertTrue(ActivityLog.objects.filter(
            action=ActivityLog.ACCEPTED, recipient=self.psy, entity_id=self.child.id).exists())

    def test_a_transfer_reads_as_one_acceptance_in_the_social_workers_feed(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        self._answer(req, "accept", user=self.psy2)
        feed = self._as(self.sw).get("/api/activity/").data
        self.assertEqual(1, sum(1 for e in feed if e["action"] == "accepted"))

    def test_the_carry_history_choice_is_applied_at_acceptance_not_before(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2, assignee_sees_history=False)
        self.child.refresh_from_db()
        self.assertTrue(self.child.assignee_sees_history,
                        "the current psychologist's view changed before anyone agreed")
        self.assertFalse(req.carry_history)
        self._answer(req, "accept", user=self.psy2)
        self.child.refresh_from_db()
        self.assertFalse(self.child.assignee_sees_history)

    def test_only_the_psychologist_asked_can_answer(self):
        req = self._ask(self.psy)
        self.assertEqual(404, self._answer(req, "accept", user=self.psy2).status_code)
        self.assertEqual(403, self._answer(req, "accept", user=self.sw).status_code)
        self.assertEqual(403, self._answer(req, "accept", user=self.admin).status_code)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.assigned_psychologist)

    def test_a_request_is_answered_once(self):
        req = self._ask(self.psy)
        self.assertEqual(200, self._answer(req, "accept").status_code)
        res = self._answer(req, "decline", reason="Too many cases.")
        self.assertEqual(409, res.status_code)
        self.assertIn("already accepted", res.data["detail"])

    def test_a_withdrawn_request_cannot_be_accepted(self):
        req = self._ask(self.psy)
        self._as(self.sw).post(f"/api/assignment-requests/{req.id}/withdraw/")
        res = self._answer(req, "accept")
        self.assertEqual(409, res.status_code)
        self.assertIn("withdrawn by Editha Pascua", res.data["detail"])
        self.child.refresh_from_db()
        self.assertIsNone(self.child.assigned_psychologist)


class DeclineTest(AssignmentBase):
    def test_declining_needs_a_reason(self):
        req = self._ask(self.psy)
        res = self._answer(req, "decline", reason="   ")
        self.assertEqual(400, res.status_code)
        self.assertIn("reason", res.data)
        req.refresh_from_db()
        self.assertEqual(AssignmentRequest.PENDING, req.status)

    def test_declining_leaves_the_child_as_it_was_and_tells_the_social_worker_why(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        res = self._answer(req, "decline", user=self.psy2,
                           reason="On leave for most of October.")
        self.assertEqual(200, res.status_code, res.data)
        self.child.refresh_from_db()
        self.assertEqual(self.psy, self.child.assigned_psychologist)
        record = self._as(self.sw).get(f"/api/children/{self.child.id}/").data
        self.assertIsNone(record["pending_assignment"])
        self.assertEqual(("Jose Rizal", "On leave for most of October."),
                         (record["declined_assignment"]["psychologist_name"],
                          record["declined_assignment"]["reason"]))
        self.assertTrue(ActivityLog.objects.filter(
            action=ActivityLog.DECLINED, recipient=self.sw, entity_id=self.child.id).exists())

    def test_the_reason_is_for_the_social_worker_not_the_psychologist_holding_the_child(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        self._answer(req, "decline", user=self.psy2, reason="Conflict of interest.")
        record = self._as(self.psy).get(f"/api/children/{self.child.id}/").data
        self.assertIsNone(record["declined_assignment"])
        self.assertIsNone(record["pending_assignment"])

    def test_a_decline_stops_showing_once_someone_else_is_asked(self):
        req = self._ask(self.psy)
        self._answer(req, "decline", reason="Full caseload.")
        self._ask(self.psy2)
        record = self._as(self.sw).get(f"/api/children/{self.child.id}/").data
        self.assertIsNone(record["declined_assignment"])
        self.assertEqual(self.psy2.id, record["pending_assignment"]["psychologist"])


class WhoIsAskedTest(AssignmentBase):
    def test_one_question_per_child_asking_another_withdraws_the_first(self):
        first = self._ask(self.psy)
        self._ask(self.psy2)
        first.refresh_from_db()
        self.assertEqual(AssignmentRequest.WITHDRAWN, first.status)
        self.assertEqual(1, AssignmentRequest.objects.filter(
            child=self.child, status=AssignmentRequest.PENDING).count())
        # The first psychologist is told what happened rather than 404ed:
        # the request was theirs, and it vanishing unexplained is the worse
        # answer.
        res = self._answer(first, "accept")
        self.assertEqual(409, res.status_code)
        self.assertIn("withdrawn", res.data["detail"])

    def test_the_database_holds_one_pending_request_per_child(self):
        AssignmentRequest.objects.create(child=self.child, psychologist=self.psy)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssignmentRequest.objects.create(child=self.child, psychologist=self.psy2)

    def test_resending_without_the_history_choice_keeps_it(self):
        """A PATCH that names the psychologist already asked and says nothing
        about history must not quietly turn a fresh start into a handover."""
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2, assignee_sees_history=False)
        self._ask(self.psy2)
        req.refresh_from_db()
        self.assertFalse(req.carry_history)

    def test_a_closed_case_cannot_be_offered(self):
        """Nobody is asked to take a terminated case. The records endpoint
        already refuses to update one (it is reopened first), so this pins
        that rather than adding a second check behind it."""
        self.child.status = Child.INACTIVE
        self.child.save()
        res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                      {"psychologist": self.psy.id}, format="json")
        self.assertEqual(404, res.status_code)
        self.assertFalse(AssignmentRequest.objects.exists())

    def test_a_filter_on_the_url_does_not_break_an_answer(self):
        req = self._ask(self.psy)
        res = self._as(self.psy).post(
            f"/api/assignment-requests/{req.id}/accept/?status=pending")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("accepted", res.data["status"])

    def test_resending_the_psychologist_already_asked_changes_nothing(self):
        req = self._ask(self.psy)
        with patch("children.assignment.send_assignment_notification") as mail:
            again = self._ask(self.psy)
        self.assertEqual(req.pk, again.pk)
        mail.assert_not_called()

    def test_choosing_the_psychologist_who_holds_the_child_withdraws_the_question(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        self._ask(self.psy)
        req.refresh_from_db()
        self.assertEqual(AssignmentRequest.WITHDRAWN, req.status)
        self.child.refresh_from_db()
        self.assertEqual(self.psy, self.child.assigned_psychologist)

    def test_leave_unassigned_withdraws_the_question_and_clears_the_assignment(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                      {"psychologist": None}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        req.refresh_from_db()
        self.child.refresh_from_db()
        self.assertEqual(AssignmentRequest.WITHDRAWN, req.status)
        self.assertIsNone(self.child.assigned_psychologist)

    def test_an_edit_without_the_field_leaves_the_question_open(self):
        req = self._ask(self.psy)
        self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                {"medical_notes": "Asthma."}, format="json")
        req.refresh_from_db()
        self.assertEqual(AssignmentRequest.PENDING, req.status)

    def test_a_psychologist_editing_their_child_does_not_touch_a_pending_transfer(self):
        """Their edit form resends `psychologist` as themselves. Read as
        "the holder" it would withdraw the question the SW asked."""
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        res = self._as(self.psy).patch(
            f"/api/children/{self.child.id}/",
            {"psychologist": self.psy.id, "medical_notes": "Asthma."}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        req.refresh_from_db()
        self.assertEqual(AssignmentRequest.PENDING, req.status)

    def test_only_an_active_psychologist_can_be_asked(self):
        res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                      {"psychologist": self.other_sw.id}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("psychologist", res.data)
        self.psy2.status = "archived"
        self.psy2.save()
        res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                      {"psychologist": self.psy2.id}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertFalse(AssignmentRequest.objects.exists())

    def test_a_holder_archived_since_still_passes_an_unrelated_edit(self):
        self.child.assigned_psychologist = self.psy2
        self.child.save()
        self.psy2.status = "archived"
        self.psy2.save()
        res = self._as(self.sw).patch(
            f"/api/children/{self.child.id}/",
            {"psychologist": self.psy2.id, "medical_notes": "Asthma."}, format="json")
        self.assertEqual(200, res.status_code, res.data)


class WithdrawTest(AssignmentBase):
    def test_the_social_worker_withdraws_their_own(self):
        req = self._ask(self.psy)
        res = self._as(self.sw).post(f"/api/assignment-requests/{req.id}/withdraw/")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("withdrawn", res.data["status"])
        self.assertTrue(ActivityLog.objects.filter(
            action=ActivityLog.WITHDRAWN, recipient=self.psy).exists())

    def test_nobody_else_does(self):
        req = self._ask(self.psy)
        self.assertEqual(404, self._as(self.other_sw).post(
            f"/api/assignment-requests/{req.id}/withdraw/").status_code)
        self.assertEqual(403, self._as(self.psy).post(
            f"/api/assignment-requests/{req.id}/withdraw/").status_code)
        self.assertEqual(200, self._as(self.admin).post(
            f"/api/assignment-requests/{req.id}/withdraw/").status_code)

    def test_terminating_the_case_withdraws_the_question(self):
        self.child.assigned_psychologist = self.psy
        self.child.save()
        req = self._ask(self.psy2)
        res = self._as(self.admin).post(f"/api/children/{self.child.id}/terminate/", {
            "reason_category": "Services completed", "note": "Done."}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        req.refresh_from_db()
        self.assertEqual(AssignmentRequest.WITHDRAWN, req.status)


class QueueScopingTest(AssignmentBase):
    def test_each_role_sees_its_own_requests(self):
        theirs = Child.objects.create(first_name="Ben", last_name="Lim", fullname="Ben Lim",
                                      social_worker=self.other_sw)
        mine = self._ask(self.psy)
        other = self._ask(self.psy2, by=self.other_sw, child=theirs)
        ids = lambda user: {r["id"] for r in self._as(user).get("/api/assignment-requests/").data}  # noqa: E731
        self.assertEqual({mine.id}, ids(self.psy))
        self.assertEqual({other.id}, ids(self.psy2))
        self.assertEqual({mine.id}, ids(self.sw))
        self.assertEqual({other.id}, ids(self.other_sw))
        self.assertEqual({mine.id, other.id}, ids(self.admin))

    def test_a_social_worker_does_not_see_another_workers_assignment_events(self):
        theirs = Child.objects.create(first_name="Ben", last_name="Lim", fullname="Ben Lim",
                                      social_worker=self.other_sw)
        self._ask(self.psy2, by=self.other_sw, child=theirs)
        labels = {e["entity_label"] for e in self._as(self.sw).get("/api/activity/").data}
        self.assertNotIn("Ben Lim", labels)


class NoticesTest(AssignmentBase):
    def test_asking_mails_and_texts_the_psychologist_asked(self):
        with patch("children.assignment.send_assignment_notification") as mail, \
             patch("children.assignment.notify_new_assignment") as sms:
            self._ask(self.psy)
        mail.assert_called_once_with(self.child, self.psy)
        sms.assert_called_once_with(self.child, self.psy)

    def test_the_text_is_a_question_and_still_one_plain_segment(self):
        from accounts import sms_notifications
        self.psy.phone, self.psy.phone_verified = "+639171234567", True
        self.psy.save()
        sent = []
        with patch.object(sms_notifications, "queue_sms",
                          side_effect=lambda n, text, d: sent.append(text) or True):
            sms_notifications.notify_new_assignment(self.child, self.psy)
        self.assertEqual(1, len(sent))
        self.assertIn("accept or decline", sent[0])
        self.assertIn(f"C-{self.child.id:04d}", sent[0])
        self.assertNotIn("Ana", sent[0])
        self.assertTrue(fits_one_segment(sent[0]))

    def test_the_bell_tells_the_psychologist(self):
        self._ask(self.psy)
        feed = self._as(self.psy).get("/api/activity/").data
        self.assertEqual([("requested", "Assignment", self.child.id)],
                         [(e["action"], e["entity_type"], e["entity_id"]) for e in feed])


class OpeningsAfterAcceptingTest(AssignmentBase):
    """"From my availability": what is offered can be booked, by the rule the
    booking endpoint runs - the calendar's lesson, applied again."""

    def setUp(self):
        super().setUp()
        give_referral(self.child, self.sw)
        tomorrow = timezone.localdate() + timedelta(days=1)
        for offset in range(7):
            day = tomorrow + timedelta(days=offset)
            AvailabilityBlock.objects.create(psychologist=self.psy, weekday=day.weekday(),
                                             start_time=time(9), end_time=time(11), capacity=2)
        req = self._ask(self.psy)
        self._answer(req, "accept")

    def test_everything_offered_can_actually_be_booked(self):
        res = self._as(self.psy).get(f"/api/availability/openings/?child={self.child.id}")
        self.assertEqual(200, res.status_code, res.data)
        offered = res.data["openings"]
        self.assertEqual(6, len(offered))
        # Two a day, and not two overlapping times of the same morning.
        self.assertEqual({"09:00", "10:00"}, {o["start"] for o in offered})
        client = self._as(self.psy)
        for o in offered:
            booked = client.post("/api/appointments/", {
                "child": self.child.id, "psychologist": self.psy.id,
                "start": f"{o['date']}T{o['start']}:00",
                "duration_minutes": 60, "purpose": "pre_assessment"}, format="json")
            self.assertEqual(201, booked.status_code, (o, booked.data))
            # Each offer is tested on its own: the child cannot hold two of
            # them, and that is not what "bookable" means here.
            Appointment.objects.filter(pk=booked.data["id"]).delete()

    def test_a_taken_time_is_not_offered(self):
        first = self._as(self.psy).get(
            f"/api/availability/openings/?child={self.child.id}").data["openings"][0]
        other = Child.objects.create(first_name="Ben", last_name="Lim", fullname="Ben Lim",
                                     assigned_psychologist=self.psy, social_worker=self.sw)
        give_referral(other, self.sw)
        start = timezone.make_aware(
            timezone.datetime.fromisoformat(f"{first['date']}T{first['start']}"))
        Appointment.objects.create(child=other, psychologist=self.psy, start=start,
                                   duration_minutes=60)
        again = self._as(self.psy).get(
            f"/api/availability/openings/?child={self.child.id}").data["openings"]
        self.assertNotIn((first["date"], first["start"]),
                         {(o["date"], o["start"]) for o in again})

    def test_the_first_offer_books(self):
        first = self._as(self.psy).get(
            f"/api/availability/openings/?child={self.child.id}").data["openings"][0]
        booked = self._as(self.psy).post("/api/appointments/", {
            "child": self.child.id, "psychologist": self.psy.id,
            "start": f"{first['date']}T{first['start']}:00",
            "duration_minutes": 60, "purpose": "pre_assessment"}, format="json")
        self.assertEqual(201, booked.status_code, booked.data)

    def test_no_referral_means_nothing_offered_and_says_why(self):
        self.child.case_referrals.all().delete()
        res = self._as(self.psy).get(f"/api/availability/openings/?child={self.child.id}")
        self.assertEqual([], res.data["openings"])
        self.assertIn("no case referral on file", res.data["reason"])

    def test_no_hours_posted_says_so(self):
        AvailabilityBlock.objects.filter(psychologist=self.psy).delete()
        res = self._as(self.psy).get(f"/api/availability/openings/?child={self.child.id}")
        self.assertEqual([], res.data["openings"])
        self.assertIn("no availability posted", res.data["reason"])
