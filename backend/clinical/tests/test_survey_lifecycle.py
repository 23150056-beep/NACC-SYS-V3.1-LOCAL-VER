"""The survey link is answered once; answering and deleting are guarded."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import AgencyFormTemplate, OpinionnaireInvite, SelfReportFlag
from clinical.survey_lifecycle import INVITE_DAYS, INVITE_LIFECYCLE
from clinical import care_gaps

User = get_user_model()
Q = "How are you feeling this week?"


class SurveyLifecycleTest(APITestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        self.child = Child.objects.create(fullname="Maria Santos",
                                          assigned_psychologist=self.psy)
        self.template = AgencyFormTemplate.objects.create(title="S", fields=[{"label": Q}])
        self.invite = OpinionnaireInvite.objects.create(
            child=self.child, template=self.template,
            expires_at=timezone.now() + timedelta(days=INVITE_DAYS))
        self.url = f"/api/opinionnaire/{self.invite.token}/submit/"

    def _submit(self, answers):
        with patch("clinical.views.start_model_check") as check:
            self.check = check
            return self.client.post(self.url, {"answers": answers}, format="json")

    def test_table_is_sound(self):
        self.assertEqual([], INVITE_LIFECYCLE.check_table())

    def test_one_constant_for_expiry_and_reminder(self):
        self.assertEqual(7, INVITE_DAYS)
        self.assertEqual(INVITE_DAYS, care_gaps.SURVEY_UNANSWERED_DAYS)

    def test_lost_race_is_409_and_flags_nothing(self):
        # Somebody else answers between the read and the write.
        real = OpinionnaireInvite.objects.filter

        def other_device_first(*a, **kw):
            if kw.get("status") == OpinionnaireInvite.PENDING and "expires_at__gt" in kw:
                real(pk=self.invite.pk).update(status=OpinionnaireInvite.SUBMITTED)
            return real(*a, **kw)
        with patch("clinical.views.OpinionnaireInvite.objects.filter",
                   side_effect=other_device_first):
            res = self._submit({Q: "Lagi akong umiiyak sa gabi."})
        self.assertEqual(409, res.status_code)
        self.assertEqual("These answers were already sent.", res.json()["detail"])
        self.assertEqual(0, SelfReportFlag.objects.count())
        self.check.assert_not_called()
        self.invite.refresh_from_db()
        self.assertEqual({}, self.invite.answers)

    def test_expiring_during_the_submit_is_gone_not_thanked(self):
        # The link is open at the read and expires before the write.
        real = OpinionnaireInvite.objects.filter

        def expires_first(*a, **kw):
            if kw.get("status") == OpinionnaireInvite.PENDING and "expires_at__gt" in kw:
                real(pk=self.invite.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            return real(*a, **kw)
        with patch("clinical.views.OpinionnaireInvite.objects.filter",
                   side_effect=expires_first):
            res = self._submit({Q: "ok"})
        self.assertEqual(410, res.status_code)
        self.check.assert_not_called()
        self.invite.refresh_from_db()
        self.assertEqual(OpinionnaireInvite.PENDING, self.invite.status)
        self.assertEqual({}, self.invite.answers)

    def test_second_send_is_gone(self):
        self.assertEqual(200, self._submit({Q: "ok"}).status_code)
        self.assertEqual(410, self._submit({Q: "again"}).status_code)

    def test_answers_for_questions_that_no_longer_exist_keep_the_link(self):
        res = self._submit({"An old question": "x"})
        self.assertEqual(400, res.status_code)
        self.assertIn("questions changed", res.json()["detail"])
        self.invite.refresh_from_db()
        self.assertEqual(OpinionnaireInvite.PENDING, self.invite.status)

    def test_expired_between_read_and_write_is_not_taken(self):
        OpinionnaireInvite.objects.filter(pk=self.invite.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(410, self._submit({Q: "x"}).status_code)

    def test_delete_submitted_refused_pending_allowed(self):
        self.client.force_authenticate(self.psy)
        done = OpinionnaireInvite.objects.create(
            child=self.child, template=self.template, status=OpinionnaireInvite.SUBMITTED,
            expires_at=timezone.now() + timedelta(days=1))
        res = self.client.delete(f"/api/opinionnaire-invites/{done.pk}/")
        self.assertEqual(409, res.status_code)
        self.assertTrue(OpinionnaireInvite.objects.filter(pk=done.pk).exists())
        res = self.client.delete(f"/api/opinionnaire-invites/{self.invite.pk}/")
        self.assertEqual(204, res.status_code)
