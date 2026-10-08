from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services, views
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from scheduling.models import Appointment

User = get_user_model()
URL = "/api/assistant/prefetch-briefs/"


class PrefetchTest(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.other = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.mine = Child.objects.create(fullname="Maria", assigned_psychologist=self.psy)
        self.theirs = Child.objects.create(fullname="Juan", assigned_psychologist=self.other)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)
        self.addCleanup(self._clear_in_flight)

    def _clear_in_flight(self):
        # _IN_FLIGHT is module-level state, not part of the DB transaction
        # Django rolls back between tests. Without this, a child id queued by
        # one test (with _start_prefetch_thread mocked, so nothing ever
        # removes it) stays in the set and can make a later test's reused id
        # look already in-flight.
        with views._IN_FLIGHT_LOCK:
            views._IN_FLIGHT.clear()

    def _appointment(self, child, psychologist, *, days=0):
        # Appointment has no `end` field -- only `start` and `duration_minutes`
        # (default 60, i.e. one hour), so the default stands in for the brief's
        # `end=start + timedelta(hours=1)`.
        #
        # Midday on the target day, not "an hour from now". The view matches on
        # start__date, so an hour from now lands on TOMORROW for anyone running
        # the suite after 23:00 — and then "today's appointment" is not today's
        # and these tests fail on the clock rather than on the code. Found at
        # 23:38. The view does not care whether the time is past or future.
        day = timezone.localdate() + timedelta(days=days)
        start = timezone.make_aware(datetime.combine(day, time(12, 0)))
        return Appointment.objects.create(
            child=child, psychologist=psychologist, start=start,
            status=Appointment.SCHEDULED)

    def test_queues_todays_own_appointments(self):
        self._appointment(self.mine, self.psy)
        with patch.object(views, "_start_prefetch_thread") as spawn:
            res = self.client.post(URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["queued"], [self.mine.id])
        spawn.assert_called_once()

    def test_ignores_another_psychologists_appointments(self):
        self._appointment(self.theirs, self.other)
        with patch.object(views, "_start_prefetch_thread"):
            res = self.client.post(URL)
        self.assertEqual(res.data["queued"], [])

    def test_ignores_appointments_on_other_days(self):
        self._appointment(self.mine, self.psy, days=3)
        with patch.object(views, "_start_prefetch_thread"):
            res = self.client.post(URL)
        self.assertEqual(res.data["queued"], [])

    def test_skips_children_that_already_have_a_brief_today(self):
        self._appointment(self.mine, self.psy)
        # Their own: a brief somebody else drafted is not theirs to read
        # (test_role_access), so it would not count as done.
        AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{self.mine.id}", ok=True,
            output_text="already done", created_by=self.psy)
        with patch.object(views, "_start_prefetch_thread"):
            res = self.client.post(URL)
        self.assertEqual(res.data["queued"], [])
        self.assertEqual(res.data["skipped"], [self.mine.id])

    def test_worker_generates_sequentially_and_audits(self):
        with patch.object(services.OllamaClient, "generate", return_value="Brief."):
            views._generate_briefs_now([self.mine.id], self.psy)
        job = AssistantJob.objects.get()
        self.assertEqual(job.input_ref, f"child:{self.mine.id}")
        self.assertEqual(job.created_by, self.psy)

    def test_worker_survives_a_failing_generation(self):
        """One unreachable call must not abandon the rest of the queue."""
        err = services.AIUnavailable("unreachable")
        with patch.object(services.OllamaClient, "generate", side_effect=err):
            views._generate_briefs_now([self.mine.id], self.psy)  # must not raise
        self.assertFalse(AssistantJob.objects.get().ok)

    def test_503_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(self.client.post(URL).status_code, 503)


class AdminPrefetchTest(APITestCase):
    """An administrator has no sessions of their own to prepare for, and since
    8 Oct 2026 no written brief at all: the model reads a child's notes to
    write one, and IT support has no case reason to ask for that. Prefetch is
    refused before anything is looked up, so no brief is queued for another
    psychologist's appointment, none is audited, and nothing reaches the
    model."""

    def setUp(self):
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.child = Child.objects.create(fullname="Maria", assigned_psychologist=self.psy)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.admin)
        self.addCleanup(self._clear_in_flight)

    def _clear_in_flight(self):
        with views._IN_FLIGHT_LOCK:
            views._IN_FLIGHT.clear()

    def test_administrator_prefetch_queues_nothing_for_a_psychologists_appointment(self):
        start = timezone.now() + timedelta(hours=1)
        Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=start,
            status=Appointment.SCHEDULED)
        with patch.object(views, "_start_prefetch_thread") as spawn:
            res = self.client.post(URL)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["detail"], views.WRITTEN_BRIEF_REFUSED)
        spawn.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())
