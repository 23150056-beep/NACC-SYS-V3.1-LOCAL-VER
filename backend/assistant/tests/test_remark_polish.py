from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services
from assistant.models import AssistantJob, AssistantSetting

User = get_user_model()
URL = "/api/assistant/polish-remark/"


class RemarkPolishTest(APITestCase):
    def setUp(self):
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=self.psy_role)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)

    def test_returns_draft_and_job_id(self):
        with patch.object(services.OllamaClient, "generate",
                          return_value="The child arrived late."):
            res = self.client.post(URL, {"text": "kid late again"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], "The child arrived late.")
        self.assertEqual(res.data["disclaimer"], services.DISCLAIMER)
        self.assertTrue(AssistantJob.objects.filter(
            id=res.data["job_id"], job_type="remark_polish").exists())

    def test_blank_text_is_rejected(self):
        res = self.client.post(URL, {"text": "   "}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_missing_text_is_rejected(self):
        self.assertEqual(self.client.post(URL, {}, format="json").status_code, 400)

    def test_non_string_text_is_rejected(self):
        """A non-string JSON value (e.g. {"text": 5}) must 400, not 500."""
        res = self.client.post(URL, {"text": 5}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_503_when_master_switch_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        res = self.client.post(URL, {"text": "note"}, format="json")
        self.assertEqual(res.status_code, 503)

    def test_503_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        res = self.client.post(URL, {"text": "note"}, format="json")
        self.assertEqual(res.status_code, 503)

    def test_503_when_runtime_unreachable(self):
        err = services.AIUnavailable("Local AI runtime unreachable: refused")
        with patch.object(services.OllamaClient, "generate", side_effect=err):
            res = self.client.post(URL, {"text": "note"}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(AssistantJob.objects.get().ok)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(URL, {"text": "n"}, format="json").status_code,
                      (401, 403))


class RemarkPolishDriftGuardTest(APITestCase):
    """Remark polish is instructed to return clear professional English. Against
    Taglish case notes it has returned Tagalog instead — measured at 4/4, once
    garbled badly enough to lose the original meaning ("settling in well"
    vanished; "recreasyon" is a word in neither language).

    A draft that drifts is worse than no draft, so it is rejected rather than
    shown, and the audit records it as a failure so the usage table tells an
    administrator it is happening.
    """

    def setUp(self):
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=self.psy_role)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)

    def test_a_drifted_draft_is_rejected_not_returned(self):
        drifted = "Nakikisalamuhan ng anak sa recreasyon."
        with patch.object(services.OllamaClient, "generate", return_value=drifted):
            res = self.client.post(URL, {"text": "Settling in well."}, format="json")
        self.assertEqual(res.status_code, 422)
        self.assertNotIn("draft", res.data)
        self.assertIn("English", res.data["detail"])

    def test_a_rejected_draft_is_audited_as_a_failure(self):
        drifted = "Nag-aalala pa rin tungkol sa school."
        with patch.object(services.OllamaClient, "generate", return_value=drifted):
            self.client.post(URL, {"text": "Still worried."}, format="json")
        job = AssistantJob.objects.get()
        self.assertFalse(job.ok)
        self.assertIn("drift", job.error)

    def test_clean_english_is_returned_unchanged(self):
        clean = "The child is settling in well and mixing with the others."
        with patch.object(services.OllamaClient, "generate", return_value=clean):
            res = self.client.post(URL, {"text": "settling ok"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], clean)
        self.assertTrue(AssistantJob.objects.get().ok)


class RemarkPolishRolesTest(APITestCase):
    """Polishing is for the people who write case notes: the assigned
    psychologist and an administrator. It used to ask only that the caller be
    signed in, so a social worker's account could send any text and be handed
    what the model made of it, unchecked; the screen just never showed them the
    button. Refused for who is asking, before the switch and before the model.
    """

    def setUp(self):
        roles = {n: Role.objects.create(role_name=n)
                 for n in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}
        self.users = {
            n: User.objects.create_user(
                email=f"{n[:1].lower()}@racco1.gov.ph", username=n.lower(),
                password="pass1234", role=r) for n, r in roles.items()}
        self.norole = User.objects.create_user(
            email="n@racco1.gov.ph", username="n", password="pass1234")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def _post(self, user):
        self.client.force_authenticate(user)
        with patch.object(services.OllamaClient, "generate",
                          return_value="The child arrived late.") as generate:
            res = self.client.post(URL, {"text": "kid late again"}, format="json")
        return res, generate

    def test_a_social_worker_is_refused_and_nothing_reaches_the_model(self):
        res, generate = self._post(self.users[Role.STAFF])
        self.assertEqual(403, res.status_code)
        self.assertEqual("Polishing is for the people who write case notes.",
                         res.data["detail"])
        self.assertNotIn("draft", res.data)
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    def test_an_account_with_no_role_is_refused_too(self):
        res, generate = self._post(self.norole)
        self.assertEqual(403, res.status_code)
        generate.assert_not_called()

    def test_the_psychologist_and_the_administrator_are_not_affected(self):
        for role in (Role.PSYCHOLOGIST, Role.ADMINISTRATOR):
            with self.subTest(role=role):
                res, _ = self._post(self.users[role])
                self.assertEqual(200, res.status_code)
                self.assertEqual("The child arrived late.", res.data["draft"])

    def test_the_refusal_is_for_who_is_asking_so_the_switch_does_not_change_it(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(403, self._post(self.users[Role.STAFF])[0].status_code)
        # The psychologist is told it is unavailable, as before.
        self.assertEqual(503, self._post(self.users[Role.PSYCHOLOGIST])[0].status_code)
