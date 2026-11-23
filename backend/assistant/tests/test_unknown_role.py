"""A role the code admits but names nowhere, asking the chatbot.

`list_unassigned_children` ended in an `else` that told every other role
"Every active child has a psychologist assigned" - a claim about the agency
made from a query that could see nothing of it.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from accounts.models import Role
from accounts.tests.probe import admit, make_probe
from assistant import tools
from children.models import Child

User = get_user_model()
CLAIM = "Every active child has a psychologist assigned."


class UnassignedChildren(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass12345", role=role)
        admit(self)
        self.probe = make_probe()

    def ask(self, user):
        req = APIRequestFactory().get("/api/assistant/ask/")
        req.user = user
        return tools.REGISTRY["list_unassigned_children"]["resolve"](req, {})

    def test_the_unknown_role_sees_no_child_and_is_told_nothing_about_the_agency(self):
        Child.objects.create(fullname="Maria Santos")
        out = self.ask(self.probe)
        self.assertEqual([], out["items"])
        self.assertNotEqual(CLAIM, out["empty"])
        self.assertEqual("", out["empty"])

    def test_the_control_the_administrator_is_told_the_agency_sentence(self):
        out = self.ask(self.admin)
        self.assertEqual([], out["items"])
        self.assertEqual(CLAIM, out["empty"])
