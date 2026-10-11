from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant.models import AssistantJob

User = get_user_model()
URL = "/api/assistant/metrics/"


class MetricsTest(APITestCase):
    def setUp(self):
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)

    def _job(self, **kw):
        kw.setdefault("job_type", "remark_polish")
        kw.setdefault("latency_ms", 1000)
        aged = kw.pop("days_ago", 0)
        job = AssistantJob.objects.create(**kw)
        if aged:
            AssistantJob.objects.filter(pk=job.pk).update(
                created_at=timezone.now() - timedelta(days=aged))
        return job

    def _row(self, data, job_type):
        return next(r for r in data["features"] if r["job_type"] == job_type)

    def test_psychologist_is_refused(self):
        self.client.force_authenticate(self.psy)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_counts_runs_errors_and_outcomes(self):
        self._job(ok=True, outcome=AssistantJob.ACCEPTED, latency_ms=1000)
        self._job(ok=True, outcome=AssistantJob.EDITED, latency_ms=3000)
        self._job(ok=False, error="unreachable", latency_ms=500)
        self.client.force_authenticate(self.admin)
        row = self._row(self.client.get(URL).data, "remark_polish")
        self.assertEqual(row["runs"], 3)
        self.assertEqual(row["ok"], 2)
        self.assertEqual(row["errors"], 1)
        self.assertEqual(row["accepted"], 1)
        self.assertEqual(row["edited"], 1)
        self.assertEqual(row["avg_latency_ms"], 1500)

    def test_excludes_jobs_older_than_the_window(self):
        self._job(ok=True, days_ago=45)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self._row(self.client.get(URL).data, "remark_polish")["runs"], 0)

    def test_every_feature_appears_even_with_no_runs(self):
        self.client.force_authenticate(self.admin)
        types = {r["job_type"] for r in self.client.get(URL).data["features"]}
        self.assertEqual(types, {"brief", "doc_intelligence", "remark_polish",
                                 "census_narrative", "chat",
                                 "self_report", "case_brief"})

    def test_works_with_the_assistant_switched_off(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(URL).status_code, 200)
