"""The model runtime is on this machine unless the deployment says otherwise.

The drafting features send case notes, whole reports and a child's own answers
to the address saved in Settings, and the ISA can edit it. These hold the rule
at every place an address can arrive - the Settings API, the Django admin, and
the moment a client is built - and the audit trail of anyone moving it.
"""
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from activity.models import ActivityLog
from assistant import services
from assistant.admin import AssistantSettingForm
from assistant.models import AssistantSetting
from assistant.runtime import (LOOPBACK_HOSTS, REMOTE_RUNTIME_REFUSED, runtime_host,
                              runtime_url_refusal)
from assistant.services import NullClient, OllamaClient, get_ai_client

User = get_user_model()
URL = "/api/assistant/settings/"

ON_THIS_MACHINE = (
    "http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434",
    "https://localhost", "http://127.0.0.1", "HTTP://LOCALHOST:8080/",
)
ELSEWHERE = (
    "http://192.168.1.20:11434", "http://10.0.0.5", "https://ollama.example.com",
    "http://api.openai.com/v1", "http://0.0.0.0:11434", "http://127.0.0.2:11434",
    # Hosts that only LOOK like this machine.
    "http://localhost.example.com:11434", "http://127.0.0.1.example.com",
    "http://localhost:11434.example.com",
    # Addresses a URL library and a browser would read differently.
    "http://localhost@example.com", "http://localhost:11434@example.com",
    "http://user:pass@localhost:11434", "http://example.com\\@localhost",
    "http://example.com#@localhost", "http://exa mple.com", "http://localhost:abc",
    "ftp://localhost", "localhost:11434", "//example.com", "",
)


class WhichAddressesTest(TestCase):
    def test_this_machine_is_the_three_loopback_names(self):
        self.assertEqual({"localhost", "127.0.0.1", "::1"}, set(LOOPBACK_HOSTS))

    def test_this_machine_is_accepted_whatever_the_port(self):
        for url in ON_THIS_MACHINE:
            with self.subTest(url):
                self.assertIsNone(runtime_url_refusal(url))

    def test_everything_else_is_refused_with_one_sentence(self):
        for url in ELSEWHERE:
            with self.subTest(url):
                self.assertEqual(REMOTE_RUNTIME_REFUSED, runtime_url_refusal(url))

    def test_the_sentence_is_one_sentence_that_says_what_to_do(self):
        self.assertEqual(1, REMOTE_RUNTIME_REFUSED.count(". ") + 1)
        self.assertTrue(REMOTE_RUNTIME_REFUSED.endswith("."))
        self.assertIn("this machine", REMOTE_RUNTIME_REFUSED)

    @override_settings(ASSISTANT_ALLOW_REMOTE_RUNTIME=True)
    def test_the_deployment_can_allow_another_machine(self):
        for url in ELSEWHERE[:3] + ON_THIS_MACHINE[:2]:
            with self.subTest(url):
                self.assertIsNone(runtime_url_refusal(url))

    def test_the_default_is_that_it_cannot(self):
        from django.conf import settings
        self.assertFalse(settings.ASSISTANT_ALLOW_REMOTE_RUNTIME)

    def test_a_host_is_read_without_port_path_or_credentials(self):
        self.assertEqual("ollama.lan", runtime_host("http://user:secret@Ollama.LAN:9/x?y#z"))
        self.assertEqual("::1", runtime_host("http://[::1]:11434"))
        self.assertEqual("", runtime_host("not a url"))
        self.assertEqual("", runtime_host(None))


class SettingsApiTest(APITestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(role_name=Role.ADMINISTRATOR)
        self.isa = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=role,
            first_name="Ada", last_name="Lim")
        self.client.force_authenticate(self.isa)
        AssistantSetting.load()

    def put(self, **body):
        return self.client.put(URL, body, format="json")

    def test_a_runtime_on_this_machine_can_be_saved(self):
        res = self.put(ollama_url="http://127.0.0.1:9999")
        self.assertEqual(200, res.status_code)
        self.assertEqual("http://127.0.0.1:9999", AssistantSetting.load().ollama_url)

    def test_another_machine_is_refused_and_nothing_is_saved(self):
        res = self.put(ollama_url="http://192.168.1.20:11434")
        self.assertEqual(400, res.status_code)
        self.assertEqual({"ollama_url": [REMOTE_RUNTIME_REFUSED]}, res.data)
        self.assertEqual("http://localhost:11434", AssistantSetting.load().ollama_url)

    def test_a_refused_address_does_not_let_the_other_fields_through(self):
        res = self.put(ollama_url="http://example.com", model_name="other-model",
                       enabled=False)
        self.assertEqual(400, res.status_code)
        cfg = AssistantSetting.load()
        self.assertEqual("qwen2.5:3b-instruct", cfg.model_name)
        self.assertTrue(cfg.enabled)

    def test_every_lookalike_is_refused_through_the_api_too(self):
        for url in ELSEWHERE:
            if not url:
                continue          # a blank is the field's own "required" refusal
            with self.subTest(url):
                self.assertEqual(400, self.put(ollama_url=url).status_code)
        self.assertEqual("http://localhost:11434", AssistantSetting.load().ollama_url)

    @override_settings(ASSISTANT_ALLOW_REMOTE_RUNTIME=True)
    def test_the_deployment_can_allow_it(self):
        res = self.put(ollama_url="http://ollama.lan:11434")
        self.assertEqual(200, res.status_code)
        self.assertEqual("http://ollama.lan:11434", AssistantSetting.load().ollama_url)

    def test_an_address_saved_before_the_rule_does_not_stop_the_assistant_being_switched_off(self):
        AssistantSetting.objects.filter(pk=1).update(ollama_url="http://ollama.lan:11434")
        # The Settings screen sends the address with every save.
        res = self.put(ollama_url="http://ollama.lan:11434", enabled=False)
        self.assertEqual(200, res.status_code)
        self.assertFalse(AssistantSetting.load().enabled)

    def test_but_it_cannot_be_changed_to_another_remote_one(self):
        AssistantSetting.objects.filter(pk=1).update(ollama_url="http://ollama.lan:11434")
        self.assertEqual(400, self.put(ollama_url="http://other.lan:11434").status_code)
        self.assertEqual(200, self.put(ollama_url="http://localhost:11434").status_code)


class TheClientIsBuiltOnlyForAnAllowedAddressTest(TestCase):
    def setUp(self):
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def use(self, url):
        AssistantSetting.objects.filter(pk=1).update(ollama_url=url)

    def test_this_machine_gets_a_client(self):
        for url in ON_THIS_MACHINE:
            with self.subTest(url):
                self.use(url)
                self.assertIsInstance(get_ai_client(), OllamaClient)

    def test_an_address_that_arrived_by_another_door_is_not_used(self):
        # Saved from the Django admin, a shell, or before this rule existed.
        self.use("http://ollama.lan:11434")
        for allow_hosted in (False, True):
            with self.subTest(allow_hosted=allow_hosted):
                client = get_ai_client(allow_hosted=allow_hosted)
                self.assertIsInstance(client, NullClient)
                with self.assertRaisesMessage(services.AIUnavailable, REMOTE_RUNTIME_REFUSED):
                    client.generate("a child's own words")

    def test_nothing_is_sent_to_it(self):
        self.use("http://ollama.lan:11434")
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(services.AIUnavailable):
                services.run_job("remark_polish", "case notes")
        urlopen.assert_not_called()

    @override_settings(ASSISTANT_ALLOW_REMOTE_RUNTIME=True)
    def test_unless_the_deployment_allows_it(self):
        self.use("http://ollama.lan:11434")
        client = get_ai_client()
        self.assertIsInstance(client, OllamaClient)
        self.assertEqual("http://ollama.lan:11434", client.base_url)

    def test_the_off_switch_still_outranks_it(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.use("http://ollama.lan:11434")
        client = get_ai_client()
        self.assertIsInstance(client, NullClient)
        self.assertEqual(services.SWITCHED_OFF, client.reason)

    def test_the_administrators_probe_says_why(self):
        role, _ = Role.objects.get_or_create(role_name=Role.ADMINISTRATOR)
        isa = User.objects.create_user(email="a@racco1.gov.ph", username="a",
                                       password="pass1234", role=role)
        self.use("http://ollama.lan:11434")
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(isa)
        res = client.post("/api/assistant/check/")
        self.assertFalse(res.data["ok"])
        self.assertEqual(REMOTE_RUNTIME_REFUSED, res.data["detail"])


class TheTrailTest(APITestCase):
    """Moving the runtime or the model is a security event, with the ISA's name
    and the hosts alone."""

    def setUp(self):
        role, _ = Role.objects.get_or_create(role_name=Role.ADMINISTRATOR)
        self.isa = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=role,
            first_name="Ada", last_name="Lim")
        self.client.force_authenticate(self.isa)

    def entries(self):
        return list(ActivityLog.objects.filter(entity_type="Assistant")
                    .order_by("id"))

    def put(self, **body):
        return self.client.put(URL, body, format="json")

    def test_a_new_address_is_logged_as_hosts_only(self):
        self.put(ollama_url="http://127.0.0.1:9999/some/path")
        (entry,) = self.entries()
        self.assertEqual(ActivityLog.SECURITY, entry.category)
        self.assertEqual(ActivityLog.UPDATED, entry.action)
        self.assertEqual(self.isa, entry.actor)
        self.assertEqual("runtime address: localhost -> 127.0.0.1", entry.entity_label)
        for private in ("9999", "11434", "some/path"):
            self.assertNotIn(private, entry.entity_label)

    def test_a_new_model_is_logged_old_to_new(self):
        self.put(model_name="llama3.2:3b")
        (entry,) = self.entries()
        self.assertEqual(ActivityLog.SECURITY, entry.category)
        self.assertEqual(self.isa, entry.actor)
        self.assertEqual("model: qwen2.5:3b-instruct -> llama3.2:3b", entry.entity_label)

    def test_changing_both_writes_both(self):
        self.put(ollama_url="http://[::1]:11434", model_name="llama3.2:3b")
        self.assertEqual(
            ["runtime address: localhost -> ::1",
             "model: qwen2.5:3b-instruct -> llama3.2:3b"],
            [e.entity_label for e in self.entries()])

    def test_the_actor_is_named_in_the_trail(self):
        self.put(model_name="llama3.2:3b")
        self.assertEqual("Ada Lim", self.entries()[0].actor_label)

    def test_saving_what_was_already_there_writes_nothing(self):
        self.put(ollama_url="http://localhost:11434", model_name="qwen2.5:3b-instruct",
                 enabled=True)
        self.assertEqual([], self.entries())

    def test_switching_the_assistant_off_is_not_a_move(self):
        self.put(enabled=False)
        self.assertEqual([], self.entries())

    def test_a_refused_change_writes_nothing(self):
        self.put(ollama_url="http://example.com", model_name="llama3.2:3b")
        self.assertEqual([], self.entries())

    @override_settings(ASSISTANT_ALLOW_REMOTE_RUNTIME=True)
    def test_a_credential_in_an_allowed_address_never_reaches_the_trail(self):
        self.put(ollama_url="http://user:hunter2@ollama.lan:11434/api")
        (entry,) = self.entries()
        self.assertEqual("runtime address: localhost -> ollama.lan", entry.entity_label)
        self.assertNotIn("hunter2", entry.entity_label)

    def test_only_the_isa_sees_it(self):
        role, _ = Role.objects.get_or_create(role_name=Role.STAFF)
        sw = User.objects.create_user(email="s@racco1.gov.ph", username="s",
                                      password="pass1234", role=role)
        self.put(model_name="llama3.2:3b")
        self.client.force_authenticate(sw)
        res = self.client.get("/api/activity/")
        self.assertEqual(200, res.status_code)
        self.assertNotIn("model:", res.content.decode())
        self.client.force_authenticate(self.isa)
        self.assertIn("model:", self.client.get("/api/activity/").content.decode())


class TheDjangoAdminIsNotADoorAroundItTest(TestCase):
    def setUp(self):
        self.model_admin = admin.site._registry[AssistantSetting]
        self.superuser = User.objects.create_superuser(
            email="root@racco1.gov.ph", username="root", password="pass1234")

    def form(self, **data):
        cfg = AssistantSetting.load()
        payload = {"enabled": "on", "ollama_url": cfg.ollama_url,
                   "model_name": cfg.model_name}
        payload.update(data)
        return AssistantSettingForm(payload, instance=cfg)

    def test_the_admin_uses_the_same_rule(self):
        self.assertIs(AssistantSettingForm, self.model_admin.form)

    def test_another_machine_is_refused_by_the_form(self):
        form = self.form(ollama_url="http://ollama.lan:11434")
        self.assertFalse(form.is_valid())
        self.assertEqual([REMOTE_RUNTIME_REFUSED], form.errors["ollama_url"])

    def test_this_machine_is_accepted(self):
        self.assertTrue(self.form(ollama_url="http://127.0.0.1:1234").is_valid())

    def test_an_address_left_alone_is_not_judged(self):
        AssistantSetting.objects.filter(pk=1).update(ollama_url="http://ollama.lan:11434")
        self.assertTrue(self.form(model_name="x").is_valid())

    def test_saving_from_the_admin_is_logged_with_the_superuser(self):
        form = self.form(ollama_url="http://127.0.0.1:11434", model_name="llama3.2:3b")
        self.assertTrue(form.is_valid(), form.errors)
        request = RequestFactory().post("/admin/")
        request.user = self.superuser
        self.model_admin.save_model(request, form.instance, form, True)
        labels = [e.entity_label for e in ActivityLog.objects.filter(entity_type="Assistant")
                  .order_by("id")]
        self.assertEqual(["runtime address: localhost -> 127.0.0.1",
                          "model: qwen2.5:3b-instruct -> llama3.2:3b"], labels)
        self.assertEqual(self.superuser, ActivityLog.objects.first().actor)
