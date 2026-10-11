from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import RemarkNote

User = get_user_model()


class BriefTestBase(APITestCase):
    """Shared fixtures only — no tests. Task 10 extends this too, and a
    subclass that inherited these tests would re-run POST cases against the
    read-only `latest` URL."""

    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.other = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.mine = Child.objects.create(fullname="Maria Santos",
                                         assigned_psychologist=self.psy)
        self.theirs = Child.objects.create(fullname="Juan Dela Cruz",
                                           assigned_psychologist=self.other)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()


class BriefTest(BriefTestBase):
    def _url(self, child):
        return f"/api/assistant/brief/child/{child.id}/"

    def test_psychologist_gets_a_brief_for_their_own_child(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Brief."):
            res = self.client.post(self._url(self.mine))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], "Brief.")
        job = AssistantJob.objects.get()
        self.assertEqual(job.input_ref, f"child:{self.mine.id}")
        self.assertEqual(job.job_type, "brief")

    def test_psychologist_gets_404_for_another_psychologists_child(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Brief."):
            res = self.client.post(self._url(self.theirs))
        self.assertEqual(res.status_code, 404)
        self.assertFalse(AssistantJob.objects.exists())

    def test_missing_child_is_404(self):
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.post("/api/assistant/brief/child/99999/").status_code, 404)

    def test_503_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.post(self._url(self.mine)).status_code, 503)

    def test_no_history_psychologist_brief_excludes_other_authors_remarks(self):
        """Carry-history control: assignee_sees_history=False must keep the
        previous psychologist's remarks out of the prompt fed to the model."""
        self.mine.assignee_sees_history = False
        self.mine.save()
        RemarkNote.objects.create(child=self.mine, author=self.other,
                                  text="OTHER AUTHOR REMARK")
        RemarkNote.objects.create(child=self.mine, author=self.psy,
                                  text="MY OWN REMARK")
        self.client.force_authenticate(self.psy)
        captured = {}

        def fake_generate(prompt, system=None):
            captured["prompt"] = prompt
            return "Brief."

        with patch.object(services.OllamaClient, "generate", side_effect=fake_generate):
            res = self.client.post(self._url(self.mine))
        self.assertEqual(res.status_code, 200)
        self.assertIn("MY OWN REMARK", captured["prompt"])
        self.assertNotIn("OTHER AUTHOR REMARK", captured["prompt"])

    def test_history_flag_true_still_includes_other_authors_remarks(self):
        """assignee_sees_history defaults to True and must stay unaffected."""
        assert self.mine.assignee_sees_history is True
        RemarkNote.objects.create(child=self.mine, author=self.other,
                                  text="OTHER AUTHOR REMARK")
        self.client.force_authenticate(self.psy)
        captured = {}

        def fake_generate(prompt, system=None):
            captured["prompt"] = prompt
            return "Brief."

        with patch.object(services.OllamaClient, "generate", side_effect=fake_generate):
            res = self.client.post(self._url(self.mine))
        self.assertEqual(res.status_code, 200)
        self.assertIn("OTHER AUTHOR REMARK", captured["prompt"])


from datetime import datetime, time, timedelta

from django.utils import timezone


class LatestBriefTest(BriefTestBase):
    """Extends the fixture base, NOT BriefTest — inheriting BriefTest's cases
    would replay its POST tests against this read-only URL."""

    def _url(self, child):
        return f"/api/assistant/brief/child/{child.id}/latest/"

    def _make_brief(self, child, *, ok=True, days_ago=0):
        job = AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{child.id}",
            output_text="Yesterday's brief" if days_ago else "Today's brief",
            ok=ok, created_by=self.psy)
        if days_ago:
            AssistantJob.objects.filter(pk=job.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago))
        return job

    def test_returns_todays_brief(self):
        self._make_brief(self.mine)
        self.client.force_authenticate(self.psy)
        res = self.client.get(self._url(self.mine))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], "Today's brief")

    def test_404_when_none_today(self):
        self._make_brief(self.mine, days_ago=1)
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.get(self._url(self.mine)).status_code, 404)

    def test_ignores_failed_jobs(self):
        self._make_brief(self.mine, ok=False)
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.get(self._url(self.mine)).status_code, 404)

    def test_another_psychologists_child_is_404(self):
        self._make_brief(self.theirs)
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.get(self._url(self.theirs)).status_code, 404)

    def test_works_with_the_assistant_switched_off(self):
        """It only reads history, so it must not 503."""
        self._make_brief(self.mine)
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.get(self._url(self.mine)).status_code, 200)

    def test_returns_newest_when_two_briefs_today(self):
        """Guards the .first() ordering dependency on AssistantJob.Meta.ordering.

        Both timestamps are anchored to a fixed mid-day time on today's local
        date, not offset from now() — offsetting by hours(2) would cross the
        local date boundary for any run between 00:00 and 02:00, which would
        make the today-only filter exclude the earlier row and let this test
        pass with only one row in the queryset (the wrong reason).
        """
        today = timezone.localdate()
        midday = timezone.make_aware(datetime.combine(today, time(12, 0)))
        earlier = AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{self.mine.id}",
            output_text="earlier brief", ok=True, created_by=self.psy)
        AssistantJob.objects.filter(pk=earlier.pk).update(created_at=midday)
        later = AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{self.mine.id}",
            output_text="later brief", ok=True, created_by=self.psy)
        AssistantJob.objects.filter(pk=later.pk).update(
            created_at=midday + timedelta(hours=2))
        self.client.force_authenticate(self.psy)
        res = self.client.get(self._url(self.mine))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], "later brief")
