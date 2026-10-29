"""Polishing the free text of a child's record (referral reason, medical notes,
recommendation, closing summary). RemarkPolishView is the psychologist's case
note and is refused to a social worker; this is the second door, for the people
who write the record, and it is guarded because a social worker can reach it.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import guards, services
from assistant.models import AssistantJob, AssistantSetting

User = get_user_model()
URL = "/api/assistant/polish-record/"
CLEAN = "The child was referred after being found at the barangay hall."
HOSTED = {
    "ASSISTANT_ALLOW_HOSTED_MODEL": True,
    "ASSISTANT_MODEL_URL": "https://api.example.invalid/v1",
    "ASSISTANT_MODEL_TOKEN": "a-token",
    "ASSISTANT_MODEL_NAME": "@cf/meta/llama-4-scout-17b-16e-instruct",
}


class RecordPolishTest(APITestCase):
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

    def _post(self, user, text="found at the hall", model=CLEAN):
        self.client.force_authenticate(user)
        with patch.object(services.OllamaClient, "generate",
                          return_value=model) as generate:
            res = self.client.post(URL, {"text": text}, format="json")
        return res, generate

    def test_the_people_who_write_a_record_get_a_draft_and_a_job(self):
        for role in (Role.STAFF, Role.ADMINISTRATOR, Role.PSYCHOLOGIST):
            with self.subTest(role=role):
                res, _ = self._post(self.users[role])
                self.assertEqual(200, res.status_code)
                self.assertEqual(CLEAN, res.data["draft"])
                self.assertEqual(services.DISCLAIMER, res.data["disclaimer"])
                job = AssistantJob.objects.get(id=res.data["job_id"])
                self.assertEqual("record_polish", job.job_type)
                self.assertEqual("record:draft", job.input_ref)
                self.assertIsNone(job.child)

    def test_an_account_with_no_role_is_refused_before_the_model_and_the_job(self):
        res, generate = self._post(self.norole)
        self.assertEqual(403, res.status_code)
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    def test_the_refusal_is_for_who_is_asking_so_the_switch_does_not_change_it(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(403, self._post(self.norole)[0].status_code)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.post(URL, {"text": "n"}, format="json").status_code,
                      (401, 403))

    def test_blank_missing_and_non_string_text_is_a_400(self):
        self.client.force_authenticate(self.users[Role.STAFF])
        for body in ({"text": "   "}, {}, {"text": 5}):
            with self.subTest(body=body):
                res = self.client.post(URL, body, format="json")
                self.assertEqual(400, res.status_code)
                self.assertEqual("Nothing to polish.", res.data["detail"])
        self.assertFalse(AssistantJob.objects.exists())

    def test_a_draft_that_invents_a_name_date_and_number_is_refused_for_every_role(self):
        invented = "it was reported by Villanueva on 14 March that 77 children were seen"
        for role in (Role.STAFF, Role.ADMINISTRATOR, Role.PSYCHOLOGIST):
            with self.subTest(role=role):
                AssistantJob.objects.all().delete()
                res, _ = self._post(self.users[role], model=invented)
                self.assertEqual(422, res.status_code)
                self.assertEqual({"detail": guards.REFUSAL_SENTENCE}, res.data)
                job = AssistantJob.objects.get()
                self.assertFalse(job.ok)
                self.assertTrue(job.error.startswith("refused:"))
                self.assertLessEqual(len(job.error), 255)

    def test_each_kind_of_invention_is_refused_alone(self):
        for model in ("Seen by Villanueva last week.", "Seen on 14 March.",
                      "Seen 77 times."):
            with self.subTest(model=model):
                AssistantJob.objects.all().delete()
                res, _ = self._post(self.users[Role.STAFF], text="was seen",
                                    model=model)
                self.assertEqual(422, res.status_code)

    def test_a_draft_that_only_uses_what_was_typed_passes(self):
        res, _ = self._post(self.users[Role.STAFF],
                            text="seen 3 times since 14 March by Villanueva",
                            model="Seen 3 times since 14 March by Villanueva.")
        self.assertEqual(200, res.status_code)

    def test_a_drifted_draft_is_rejected_and_audited(self):
        res, _ = self._post(self.users[Role.STAFF], text="Settling in well.",
                            model="Nakikisalamuhan ng anak sa recreasyon.")
        self.assertEqual(422, res.status_code)
        self.assertNotIn("draft", res.data)
        self.assertIn("English", res.data["detail"])
        job = AssistantJob.objects.get()
        self.assertFalse(job.ok)
        self.assertIn("drift", job.error)

    def test_503_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(503, self._post(self.users[Role.STAFF])[0].status_code)

    def test_503_when_the_runtime_is_unreachable(self):
        self.client.force_authenticate(self.users[Role.STAFF])
        err = services.AIUnavailable("Local AI runtime unreachable: refused")
        with patch.object(services.OllamaClient, "generate", side_effect=err):
            res = self.client.post(URL, {"text": "note"}, format="json")
        self.assertEqual(503, res.status_code)
        self.assertFalse(AssistantJob.objects.get().ok)

    def _post_hosted(self, user, text="found at the hall", model=CLEAN, url=URL):
        self.client.force_authenticate(user)
        with patch.object(services.OpenAICompatibleClient, "generate",
                          return_value=model) as hosted, \
                patch.object(services.OllamaClient, "generate") as local:
            res = self.client.post(url, {"text": text}, format="json")
        return res, hosted, local

    @override_settings(**HOSTED)
    def test_a_hosted_deployment_polishes_with_the_hosted_model(self):
        # Owner's decision, 29 Oct 2026: record polish may use the hosted model.
        for role in (Role.STAFF, Role.ADMINISTRATOR, Role.PSYCHOLOGIST):
            with self.subTest(role=role):
                res, hosted, local = self._post_hosted(self.users[role])
                self.assertEqual(200, res.status_code)
                self.assertEqual(CLEAN, res.data["draft"])
                hosted.assert_called_once()
                local.assert_not_called()

    @override_settings(**HOSTED)
    def test_the_guard_still_refuses_a_hosted_draft_that_invents(self):
        res, hosted, _ = self._post_hosted(
            self.users[Role.STAFF], model="Seen by Villanueva on 14 March, 77 times.")
        self.assertEqual(422, res.status_code)
        self.assertEqual({"detail": guards.REFUSAL_SENTENCE}, res.data)
        hosted.assert_called_once()

    @override_settings(**HOSTED)
    def test_remark_polish_still_refuses_a_hosted_model(self):
        # The exception is for record polish alone; it must not leak.
        res, hosted, local = self._post_hosted(
            self.users[Role.PSYCHOLOGIST], url="/api/assistant/polish-remark/")
        self.assertEqual(503, res.status_code)
        self.assertEqual(services.HOSTED_DRAFTING_REFUSED, res.data["detail"])
        hosted.assert_not_called()
        local.assert_not_called()
