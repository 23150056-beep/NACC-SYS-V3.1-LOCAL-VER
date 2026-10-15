"""'Last sign-in' in Users and on the profile page, and the ISA's console, must
tell the same story.

Only the Google door used to stamp `User.last_login`, so a colleague who signs in
with a password was "Signed in today" on the console and "Last sign-in: Never"
in their own drawer. The login now stamps it, and migration 0015 brought the
accounts that signed in earlier up to date from the sign-in lines.
"""
import importlib
from datetime import datetime, timedelta, timezone as dt_timezone

from django.apps import apps
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from activity.models import ActivityLog


class PasswordSignInStampsLastLoginTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="e@t.ph", username="e", password="pass12345", role=role,
            first_name="Editha", last_name="Pascua")

    def test_a_password_sign_in_sets_it(self):
        self.assertIsNone(self.user.last_login)
        done = APIClient().post("/api/auth/login/", {"email": "e@t.ph", "password": "pass12345"},
                                format="json")
        self.assertEqual(done.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)
        self.assertIsNotNone(done.data["user"]["last_login"])

    def test_a_refused_sign_in_does_not(self):
        APIClient().post("/api/auth/login/", {"email": "e@t.ph", "password": "wrong"},
                         format="json")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.last_login)


class BackfillTest(TestCase):
    """Migration 0015, run on the real models (the fields it touches are the same
    ones)."""

    def setUp(self):
        self.stamp = importlib.import_module(
            "accounts.migrations.0015_backfill_last_login").stamp_from_the_sign_in_lines
        role = Role.objects.create(role_name=Role.STAFF)
        self.never = User.objects.create_user(email="n@t.ph", username="n", role=role)
        self.behind = User.objects.create_user(email="b@t.ph", username="b", role=role)
        self.ahead = User.objects.create_user(email="a@t.ph", username="a", role=role)
        self.quiet = User.objects.create_user(email="q@t.ph", username="q", role=role)

    def signed_in(self, user, when):
        row = ActivityLog.objects.create(
            actor=user, actor_label="x", action=ActivityLog.LOGIN, category=ActivityLog.SECURITY)
        ActivityLog.objects.filter(pk=row.pk).update(created_at=when)

    def test_it_moves_a_date_forward_and_never_back(self):
        old = datetime(2026, 9, 1, 8, 0, tzinfo=dt_timezone.utc)
        newer = old + timedelta(days=20)
        newest = newer + timedelta(days=3)
        self.signed_in(self.never, old)
        self.signed_in(self.never, newer)            # the newest line wins
        self.signed_in(self.behind, newer)
        User.objects.filter(pk=self.behind.pk).update(last_login=old)
        self.signed_in(self.ahead, old)
        User.objects.filter(pk=self.ahead.pk).update(last_login=newest)
        # Not a sign-in: nothing to stamp from.
        ActivityLog.objects.create(actor=self.quiet, actor_label="x", action=ActivityLog.UPDATED,
                                   category=ActivityLog.SECURITY, entity_label="Changed own password")

        self.stamp(apps, None)

        def last(user):
            return User.objects.get(pk=user.pk).last_login
        self.assertEqual(last(self.never), newer)
        self.assertEqual(last(self.behind), newer)
        self.assertEqual(last(self.ahead), newest)
        self.assertIsNone(last(self.quiet))


class KindColumnTest(TestCase):
    def test_a_line_written_without_a_kind_has_a_blank_one(self):
        row = ActivityLog.objects.create(actor_label="x", action=ActivityLog.UPDATED,
                                         category=ActivityLog.USER)
        row.refresh_from_db()
        self.assertEqual(row.kind, "")

    def test_a_release_that_does_not_know_the_column_can_still_insert(self):
        """The old release keeps serving while a deploy migrates. Its INSERT
        names no `kind`, so the database must supply one."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tbl_activity_log (actor_label, action, category, entity_type,"
                " entity_label, created_at) VALUES ('x', 'updated', 'user', 'User', 'y',"
                " '2026-10-14 00:00:00')")
        self.assertEqual(ActivityLog.objects.get(entity_label="y").kind, "")
