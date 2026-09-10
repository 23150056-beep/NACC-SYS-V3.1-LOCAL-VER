"""The Google door's requests were verified before this system saw them.

`email_verified` arrived defaulting to False. For a typed address that is
correct - nothing has proved it. For an account that came through Google it is
simply wrong: Google checked the address, which is the entire difference
between the two doors and the reason the queue has always recorded which one a
request used.

Migration 0010 records that. These tests pin both halves: what it fixes, and
what it deliberately leaves alone.
"""
import importlib

from django.apps import apps as live_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Role

User = get_user_model()

MIGRATION = "accounts.migrations.0010_google_requests_are_already_verified"


class GoogleRequestsAreAlreadyVerifiedTest(TestCase):
    def setUp(self):
        self.forward = importlib.import_module(MIGRATION).Migration.operations[0].code
        self.staff_role = Role.objects.create(role_name=Role.STAFF)

    def _make(self, email, google_sub=None):
        user = User(email=email, username=email, status=User.PENDING,
                    google_sub=google_sub)
        user.set_unusable_password()
        user.save()
        User.objects.filter(pk=user.pk).update(email_verified=False)
        return User.objects.get(pk=user.pk)

    def test_a_google_request_is_marked_verified(self):
        user = self._make("g@racco1.gov.ph", google_sub="google-1")
        self.forward(live_apps, None)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_a_typed_sign_up_is_left_unverified(self):
        # Nothing verified it. Marking it would invent the fact the new check
        # exists to establish.
        user = self._make("typed@racco1.gov.ph")
        self.forward(live_apps, None)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_an_empty_google_sub_does_not_count(self):
        user = self._make("blank@racco1.gov.ph", google_sub="")
        self.forward(live_apps, None)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_running_it_again_changes_nothing(self):
        user = self._make("g2@racco1.gov.ph", google_sub="google-2")
        self.forward(live_apps, None)
        self.forward(live_apps, None)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_an_already_active_google_account_is_untouched_in_every_other_way(self):
        user = self._make("g3@racco1.gov.ph", google_sub="google-3")
        User.objects.filter(pk=user.pk).update(status=User.ACTIVE, role=self.staff_role)
        self.forward(live_apps, None)
        user.refresh_from_db()
        self.assertEqual(User.ACTIVE, user.status)
        self.assertEqual(self.staff_role, user.role)
        self.assertTrue(user.email_verified)
