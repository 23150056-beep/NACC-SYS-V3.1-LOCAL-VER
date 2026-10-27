"""The Django admin is a second door onto the assistant log. It shows who, when,
which child and how it ended - and never the words, and it changes nothing."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from accounts.models import Role
from assistant.admin import AssistantJobAdmin
from assistant.models import AssistantJob
from children.models import Child

User = get_user_model()
QUESTION = "Does Maria Santos still sleep badly?"
TEXT = "A drafted brief that quotes the case notes: Maria cried at night."


class AssistantJobAdminTest(TestCase):
    def setUp(self):
        self.model_admin = admin.site._registry[AssistantJob]
        self.superuser = User.objects.create_superuser(
            email="root@racco1.gov.ph", username="root", password="pass1234")
        role, _ = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        self.child = Child.objects.create(fullname="Maria Santos",
                                          assigned_psychologist=self.psy)
        self.chat = AssistantJob.objects.create(
            job_type="chat", input_ref=QUESTION, output_text="get_child_summary(x)",
            answer=AssistantJob.DATA, created_by=self.psy)
        self.brief = AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{self.child.pk}", output_text=TEXT,
            child=self.child, created_by=self.psy)
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.superuser

    def test_it_is_the_class_registered_for_the_log(self):
        self.assertIsInstance(self.model_admin, AssistantJobAdmin)

    def test_not_even_a_superuser_may_add_change_or_delete(self):
        self.assertTrue(self.superuser.is_superuser)
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        for row in (None, self.chat, self.brief):
            self.assertFalse(self.model_admin.has_change_permission(self.request, row))
            self.assertFalse(self.model_admin.has_delete_permission(self.request, row))

    def test_it_can_still_be_looked_at(self):
        self.assertTrue(self.model_admin.has_view_permission(self.request, self.brief))
        self.assertTrue(self.model_admin.has_module_permission(self.request))

    def test_there_is_no_bulk_delete(self):
        self.assertNotIn("delete_selected", self.model_admin.get_actions(self.request))

    def test_the_words_are_in_neither_the_list_nor_the_detail(self):
        for name in ("output_text", "input_ref"):
            self.assertNotIn(name, self.model_admin.get_list_display(self.request))
            self.assertNotIn(name, self.model_admin.get_fields(self.request, self.brief))

    def test_every_field_shown_is_read_only(self):
        fields = list(self.model_admin.get_fields(self.request, self.brief))
        self.assertEqual(fields, list(self.model_admin.get_readonly_fields(self.request)))

    def test_a_reference_is_shown_for_everything_but_a_chat(self):
        self.assertEqual(f"child:{self.child.pk}", self.model_admin.reference(self.brief))
        self.assertNotIn(QUESTION, self.model_admin.reference(self.chat))
        self.assertIn("not shown", self.model_admin.reference(self.chat))

    def test_the_pages_themselves_carry_no_text(self):
        self.client.force_login(self.superuser)
        listing = self.client.get("/admin/assistant/assistantjob/")
        self.assertEqual(200, listing.status_code)
        for private in (QUESTION, TEXT, "get_child_summary", "Maria cried"):
            self.assertNotContains(listing, private)
        self.assertContains(listing, "Chatbot Question")
        for row in (self.chat, self.brief):
            detail = self.client.get(f"/admin/assistant/assistantjob/{row.pk}/change/")
            self.assertEqual(200, detail.status_code)
            for private in (QUESTION, TEXT, "get_child_summary", "Maria cried"):
                self.assertNotContains(detail, private)

    def test_the_forms_that_would_change_it_are_refused(self):
        self.client.force_login(self.superuser)
        base = "/admin/assistant/assistantjob/"
        self.assertEqual(403, self.client.get(f"{base}add/").status_code)
        self.assertEqual(403, self.client.post(f"{base}add/", {"job_type": "chat"}).status_code)
        self.assertEqual(403, self.client.post(
            f"{base}{self.chat.pk}/change/", {"outcome": "accepted"}).status_code)
        self.assertEqual(403, self.client.post(
            f"{base}{self.chat.pk}/delete/", {"post": "yes"}).status_code)
        # The bulk action is not offered, so asking for it does nothing.
        self.client.post(base, {"action": "delete_selected",
                                "_selected_action": [self.chat.pk], "post": "yes"})
        self.assertEqual(2, AssistantJob.objects.count())
        self.chat.refresh_from_db()
        self.assertEqual(AssistantJob.PENDING, self.chat.outcome)
