"""Who the chat eval asks as.

It used to ask everything as one psychologist, so staff and administrators
were never measured, and the schedule answered both of them "Nothing recorded"
- including to the questions the panel itself suggests to them - without the
eval ever noticing. These pin that every role with an account is asked.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Role
from assistant.management.commands.ai_eval import eval_callers
from children.models import Child

User = get_user_model()


class EvalCallersTest(TestCase):
    def _user(self, role, email, **extra):
        role_obj, _ = Role.objects.get_or_create(role_name=role)
        return User.objects.create_user(
            email=email, username=email, password="pass1234", role=role_obj, **extra)

    def test_every_role_with_an_account_is_asked(self):
        psy = self._user(Role.PSYCHOLOGIST, "p@racco1.gov.ph")
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=psy)
        self._user(Role.STAFF, "s@racco1.gov.ph")
        self._user(Role.ADMINISTRATOR, "a@racco1.gov.ph")

        roles = [role for role, _ in eval_callers()]
        self.assertEqual([Role.PSYCHOLOGIST, Role.STAFF, Role.ADMINISTRATOR], roles)

    def test_the_officer_is_asked_when_it_has_an_account(self):
        psy = self._user(Role.PSYCHOLOGIST, "p@racco1.gov.ph")
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=psy)
        self._user(Role.STAFF, "s@racco1.gov.ph")
        self._user(Role.OFFICER, "o@racco1.gov.ph")
        self.assertEqual([Role.PSYCHOLOGIST, Role.STAFF, Role.OFFICER],
                         [role for role, _ in eval_callers()])

    def test_the_report_says_which_role_was_not_measured(self):
        """EVERY_ROLE drives the "NOT MEASURED" lines, so the Officer is named
        when no Officer account exists to ask as."""
        from io import StringIO

        from assistant.management.commands.ai_eval import Command
        from assistant.services import AIUnavailable

        psy = self._user(Role.PSYCHOLOGIST, "p@racco1.gov.ph")
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=psy)

        class Down:
            def choose_tool(self, *args, **kwargs):
                raise AIUnavailable("down")

        def report(command):
            command.stdout = StringIO()
            command.client = Down()
            command._chat(1)
            return command.stdout.getvalue()

        self.assertIn("Officer        none — NOT MEASURED", report(Command()))
        self._user(Role.OFFICER, "o@racco1.gov.ph")
        out = report(Command())
        self.assertNotIn("Officer        none", out)
        self.assertIn("Caller: Officer        o@racco1.gov.ph", out)

    def test_the_psychologist_is_one_with_a_caseload(self):
        # Otherwise "found nothing" measures an empty caseload, not the tool.
        self._user(Role.PSYCHOLOGIST, "idle@racco1.gov.ph")
        busy = self._user(Role.PSYCHOLOGIST, "busy@racco1.gov.ph")
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=busy)

        callers = dict(eval_callers())
        self.assertEqual(busy, callers[Role.PSYCHOLOGIST].user)

    def test_a_role_without_an_active_account_is_left_out_not_fatal(self):
        # The report names the missing role; the other roles still run.
        self._user(Role.ADMINISTRATOR, "a@racco1.gov.ph")
        self._user(Role.STAFF, "gone@racco1.gov.ph", status=User.ARCHIVED)

        self.assertEqual([Role.ADMINISTRATOR], [r for r, _ in eval_callers()])
