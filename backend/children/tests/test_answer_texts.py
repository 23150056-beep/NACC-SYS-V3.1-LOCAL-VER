"""The social worker is TEXTED the psychologist's answer, as well as emailed
(owner's request, 28 Oct 2026: a decline on the hosted demo reached nobody by
phone).

The text is fired exactly where the answer email is decided - a decline at once,
an acceptance by whoever wins the `answer_mailed_at` claim (the psychologist's
booking, the dialog's call, or the sweep) - so it goes once per answer and not
for a superseded acceptance. It does not depend on the social worker's EMAIL
switch or on the email limit. It carries the case reference and nothing else.

`queue_sms`, the one seam in sms_notifications, is patched to collect what would
have been sent; no gateway is ever touched. The clock is pinned by the fixture.
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache

from accounts import sms_catalogue
from accounts.email_notifications import mail_overdue_answers
from accounts.models import EmailOptOut
from children.models import AssignmentRequest, Child
from children.tests.payloads import planted_text
from children.tests.test_answer_email import AnswerMailBase

NUMBER = "09171234567"
REASON = "Quillon caseload is already full until March"


class AnswerTextBase(AnswerMailBase):
    def setUp(self):
        super().setUp()
        self.sw.phone, self.sw.phone_verified = NUMBER, True
        self.sw.save()
        self.texts_sent = []
        seam = patch("accounts.sms_notifications.queue_sms",
                     side_effect=lambda *a, **kw: self.texts_sent.append((a, kw)) or True)
        seam.start()
        self.addCleanup(seam.stop)

    def kinds(self):
        return [a[2] for a, _ in self.texts_sent]

    def bodies(self):
        return "\n".join(a[1] for a, _ in self.texts_sent)

    def decline(self, req, reason=REASON):
        with self.outbox():
            res = self._as(self.psy).post(
                f"/api/assignment-requests/{req.pk}/decline/", {"reason": reason},
                format="json")
        self.assertEqual(200, res.status_code, res.data)


class DeclineTextTest(AnswerTextBase):
    def test_a_decline_texts_the_social_worker_once(self):
        req = self.ask()
        self.decline(req)
        self.assertEqual(["case_declined"], self.kinds())
        (args, kwargs), = self.texts_sent
        self.assertEqual(NUMBER, args[0])
        self.assertEqual(f"answer:{req.pk}", kwargs["subject"])
        self.assertEqual(self.psy, kwargs["requested_by"])
        self.assertEqual(
            f"NACC SYS: the psychologist declined case {self.ref}. Sign in to see "
            "why and ask another.", args[1])

    def test_no_text_without_a_verified_number(self):
        self.sw.phone_verified = False
        self.sw.save()
        self.decline(self.ask())
        self.assertEqual([], self.texts_sent)
        self.sw.phone, self.sw.phone_verified = "", True
        self.sw.save()
        self.decline(self.ask())
        self.assertEqual([], self.texts_sent)

    def test_a_refused_decline_texts_nobody(self):
        req = self.ask()
        res = self._as(self.psy).post(
            f"/api/assignment-requests/{req.pk}/decline/", {"reason": ""}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual([], self.texts_sent)

    def test_the_email_switch_and_the_email_limit_do_not_stop_it(self):
        EmailOptOut.objects.create(user=self.sw, kind="case_declined")
        with patch("accounts.email_notifications._over_limit", return_value=True):
            self.decline(self.ask())
        self.assertEqual(["case_declined"], self.kinds())

    def test_no_text_for_a_holder_who_is_not_a_social_worker(self):
        req = self.ask()
        Child.objects.filter(pk=self.child.pk).update(social_worker=self.admin)
        self.admin.phone, self.admin.phone_verified = "09181234567", True
        self.admin.save()
        self.decline(req)
        self.assertEqual([], self.texts_sent)


class AcceptanceTextTest(AnswerTextBase):
    def test_accepting_alone_texts_nobody_yet(self):
        self.accepted()
        self.assertEqual([], self.texts_sent)

    def test_accepting_and_booking_is_one_combined_text(self):
        req = self.accepted()
        with self.outbox():
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["case_accepted_booked"], self.kinds())
        (args, kwargs), = self.texts_sent
        self.assertEqual(
            f"NACC SYS: the psychologist accepted case {self.ref} and booked the "
            "first session, Wed 21 Oct, 9:30 AM.", args[1])
        self.assertEqual(f"answer:{req.pk}", kwargs["subject"])
        # The dialog and the sweep afterwards add nothing.
        with self.outbox():
            self.assertEqual({"mailed": False}, self.mail_answer(req).data)
        self.grow_old(req, 30)
        cache.clear()
        mail_overdue_answers()
        self.assertEqual(["case_accepted_booked"], self.kinds())

    def test_the_dialog_ending_without_a_booking_texts_once(self):
        req = self.accepted()
        with self.outbox():
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
            self.assertEqual({"mailed": False}, self.mail_answer(req).data)
        self.assertEqual(["case_accepted"], self.kinds())
        self.assertEqual(
            f"NACC SYS: the psychologist accepted case {self.ref}. Sign in to see "
            "the next steps.", self.texts_sent[0][0][1])

    def test_the_sweep_ten_minutes_on_texts_once(self):
        req = self.accepted()
        self.grow_old(req, 11)
        cache.clear()
        with self.outbox():
            self.assertEqual(200, self._as(self.sw).get("/api/activity/").status_code)
        self.assertEqual(["case_accepted"], self.kinds())
        cache.clear()
        with self.outbox():
            self._as(self.sw).get("/api/activity/")
            self.mail_answer(req)
        self.assertEqual(["case_accepted"], self.kinds())

    def test_a_fresh_acceptance_is_left_to_the_dialog(self):
        self.accepted()
        cache.clear()
        mail_overdue_answers()
        self.assertEqual([], self.texts_sent)

    def test_racing_claims_send_one_text(self):
        """The dialog and the sweep each hold a stale copy and both try: the
        claim has one winner, and only the winner texts."""
        req = self.accepted()
        self.grow_old(req, 30)
        stale = [AssignmentRequest.objects.get(pk=req.pk) for _ in range(2)]
        from accounts.email_notifications import claim_answer_mail, text_answer
        for r in stale:
            if claim_answer_mail(r):
                text_answer(r)
        self.assertEqual(["case_accepted"], self.kinds())

    def test_a_superseded_acceptance_texts_nothing(self):
        old = self.accepted(psychologist=self.psy)
        self.accepted(psychologist=self.psy2)
        with self.outbox():
            self.assertEqual({"mailed": True}, self.mail_answer(old).data)
        self.assertEqual([], self.texts_sent)
        newer = AssignmentRequest.objects.filter(
            child=self.child, status=AssignmentRequest.ACCEPTED).exclude(pk=old.pk).get()
        self.grow_old(newer, 30)
        AssignmentRequest.objects.filter(pk=newer.pk).update(answer_mailed_at=None)
        cache.clear()
        with self.outbox():
            mail_overdue_answers()
        self.assertEqual(["case_accepted"], self.kinds())

    def test_the_email_switch_does_not_stop_the_text(self):
        EmailOptOut.objects.create(user=self.sw, kind="case_accepted")
        req = self.accepted()
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["case_accepted_booked"], self.kinds())
        self.assertFalse(self.owed(req))

    def test_a_text_is_not_queued_twice_when_the_email_could_not_be_prepared(self):
        """The claim goes back when the combined email fails to prepare, and the
        dialog then carries the acceptance - one text in all."""
        req = self.accepted()
        with patch("accounts.email_notifications._send_answer",
                   side_effect=RuntimeError("boom")):
            with self.outbox():
                self.book(self.psy)
        self.assertEqual([], self.texts_sent)
        self.assertTrue(self.owed(req))
        with self.outbox():
            self.mail_answer(req)
        self.assertEqual(["case_accepted"], self.kinds())


class NothingAboutTheCaseTest(AnswerTextBase):
    def test_no_text_names_the_child_the_psychologist_or_the_reason(self):
        planted = planted_text()
        Child.objects.filter(pk=self.child.pk).update(**planted)
        self.decline(self.ask())
        self.accepted()
        with self.outbox():
            self.book(self.psy)
        self.assertEqual(["case_declined", "case_accepted_booked"], self.kinds())
        text = self.bodies()
        for token in ("Zyxwanita", "Qorvath", "Brunhilda", "Vexley", "Marivic", "Bulan",
                      "Quillon", "caseload", self.sw.first_name):
            self.assertNotIn(token, text)
        for token in planted_text().values():
            self.assertNotIn(token, text)


class CatalogueTest(AnswerTextBase):
    def test_the_two_acceptances_share_one_allowance(self):
        self.assertEqual({"case_accepted", "case_accepted_booked"},
                         set(sms_catalogue.kinds_in_group("case_accepted")))

    def test_the_wording_never_puts_the_network_dropped_pair_together(self):
        for key in ("case_declined", "case_accepted", "case_accepted_booked"):
            self.assertNotIn("expires in", sms_catalogue.render_sample(key))
            self.assertTrue(sms_catalogue.render_sample(key).startswith("NACC SYS: "))
