"""WP2 of the RACC Officer plan: the role's row exists, the ISA can grant it,
nobody can claim it, a change away from Staff cannot strand a caseload, and a
holder who cannot hold records is listed and can be moved.

The Officer is in no capability list yet, so the backstop still refuses an
Officer everywhere (last class). Its row is always fetched with get_or_create
here: the migration made it, and a TransactionTestCase flush would remove it.
"""
import importlib
from datetime import date
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from accounts import capabilities
from accounts.models import Role
from accounts.tests.test_google_login import GOOGLE_CLIENT_ID, VERIFY, claims
from children import notifications
from children.models import Child

User = get_user_model()

PASSWORD = "Sup3r-secret-2026"


def role(name):
    return Role.objects.get_or_create(role_name=name)[0]


def person(email, role_name, **extra):
    return User.objects.create_user(
        email=email, username=email.split("@")[0], password=PASSWORD,
        first_name=email.split("@")[0].title(), last_name="Tester",
        role=role(role_name) if role_name else None, **extra)


def child(first, worker, **fields):
    return Child.objects.create(first_name=first, last_name="Cruz",
                                birth_date=date(2015, 3, 2), social_worker=worker, **fields)


class TheOfficersRowExists(TestCase):
    """A TestCase: the row is the migration's, kept by the rollback of each test."""

    def test_the_row_is_there_after_migrate_and_only_that_row(self):
        self.assertEqual(["Officer"], list(Role.objects.values_list("role_name", flat=True)))

    def test_the_four_names(self):
        self.assertEqual(("Administrator", "Psychologist", "Staff", "Officer"), Role.NAMES)

    def test_the_data_step_is_idempotent_and_creates_nothing_else(self):
        step = importlib.import_module("accounts.migrations.0020_officer_role").add_officer_role
        Role.objects.create(role_name=Role.STAFF)
        step(apps, None)
        step(apps, None)
        self.assertEqual(1, Role.objects.filter(role_name="Officer").count())
        self.assertEqual({"Officer", "Staff"}, set(Role.objects.values_list("role_name", flat=True)))
        Role.objects.filter(role_name="Officer").delete()
        step(apps, None)
        self.assertTrue(Role.objects.filter(role_name="Officer").exists())

    def test_seed_initial_data_seeds_all_four_and_twice_changes_nothing(self):
        from django.core.management import call_command
        call_command("seed_initial_data", stdout=mock.MagicMock())
        call_command("seed_initial_data", stdout=mock.MagicMock())
        self.assertEqual(set(Role.NAMES), set(Role.objects.values_list("role_name", flat=True)))
        self.assertEqual(4, Role.objects.count())


class OnlyAKnownRoleCanBeAssigned(APITestCase):
    def setUp(self):
        self.admin = person("admin@racco1.gov.ph", Role.ADMINISTRATOR)
        self.probe = Role.objects.create(role_name="Probe")
        self.client.force_authenticate(self.admin)

    def pending(self, email="new@gmail.com"):
        return User.objects.create_user(
            email=email, username=email, status=User.PENDING, email_verified=True,
            google_sub="sub-" + email)

    def test_the_role_list_is_the_four_rows_and_not_a_stray_one(self):
        for name in Role.NAMES:
            role(name)
        res = self.client.get("/api/roles/")
        self.assertEqual(200, res.status_code)
        self.assertEqual(sorted(Role.NAMES), sorted(r["role_name"] for r in res.data))
        self.assertNotIn("Probe", [r["role_name"] for r in res.data])

    def test_approval_grants_officer_and_refuses_a_stray_row_and_administrator(self):
        user = self.pending()
        res = self.client.post(f"/api/users/{user.pk}/approve/", {"role": self.probe.pk})
        self.assertEqual(400, res.status_code)
        user.refresh_from_db()
        self.assertEqual(User.PENDING, user.status)
        res = self.client.post(f"/api/users/{user.pk}/approve/",
                               {"role": role(Role.ADMINISTRATOR).pk})
        self.assertEqual(400, res.status_code)
        with mock.patch("accounts.views.send_access_approved_notification", return_value=True):
            res = self.client.post(f"/api/users/{user.pk}/approve/", {"role": role("Officer").pk})
        self.assertEqual(200, res.status_code, res.data)
        user.refresh_from_db()
        self.assertEqual(("Officer", User.ACTIVE), (user.role.role_name, user.status))

    def test_add_user_takes_officer_and_refuses_a_stray_row(self):
        body = {"email": "o@racco1.gov.ph", "first_name": "Olga", "last_name": "Reyes"}
        res = self.client.post("/api/users/", {**body, "role": self.probe.pk}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("role", res.data)
        self.assertFalse(User.objects.filter(email="o@racco1.gov.ph").exists())
        res = self.client.post("/api/users/", {**body, "role": role("Officer").pk}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual("Officer", User.objects.get(email="o@racco1.gov.ph").role.role_name)

    def test_editing_a_role_takes_officer_and_refuses_a_stray_row(self):
        psy = person("psy@t.ph", Role.PSYCHOLOGIST)
        res = self.client.patch(f"/api/users/{psy.pk}/", {"role": self.probe.pk}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("role", res.data)
        res = self.client.patch(f"/api/users/{psy.pk}/", {"role": role("Officer").pk}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        psy.refresh_from_db()
        self.assertEqual("Officer", psy.role.role_name)


class AChangeAwayFromStaffMustNotStrandRecords(APITestCase):
    def setUp(self):
        self.admin = person("admin@racco1.gov.ph", Role.ADMINISTRATOR)
        self.sw = person("sw@t.ph", Role.STAFF)
        self.other = person("other@t.ph", Role.STAFF)
        self.client.force_authenticate(self.admin)

    def change(self, to, user=None):
        user = user or self.sw
        return self.client.patch(f"/api/users/{user.pk}/", {"role": role(to).pk}, format="json")

    def test_staff_to_officer_and_to_psychologist_are_refused_with_the_count(self):
        child("A", self.sw)
        child("B", self.sw)
        for to in ("Officer", Role.PSYCHOLOGIST):
            res = self.change(to)
            self.assertEqual(400, res.status_code, to)
            self.assertEqual(
                "This account still holds 2 active records. Move them first (Transfer records).",
                str(res.data["role"][0]))
            self.sw.refresh_from_db()
            self.assertEqual(Role.STAFF, self.sw.role.role_name)

    def test_one_record_reads_singular(self):
        child("A", self.sw)
        res = self.change("Officer")
        self.assertEqual(
            "This account still holds 1 active record. Move them first (Transfer records).",
            str(res.data["role"][0]))

    def test_allowed_once_the_records_are_moved(self):
        a = child("A", self.sw)
        self.assertEqual(400, self.change("Officer").status_code)
        moved = self.client.post("/api/children/transfer/", {
            "from_social_worker": self.sw.pk, "to_social_worker": self.other.pk,
            "children": [a.pk]}, format="json")
        self.assertEqual(200, moved.status_code, moved.data)
        res = self.change("Officer")
        self.assertEqual(200, res.status_code, res.data)
        self.sw.refresh_from_db()
        self.assertEqual("Officer", self.sw.role.role_name)

    def test_closed_records_do_not_count(self):
        child("Done", self.sw, status=Child.INACTIVE)
        self.assertEqual(200, self.change("Officer").status_code)

    def test_other_holders_records_do_not_count(self):
        child("Theirs", self.other)
        self.assertEqual(200, self.change("Psychologist").status_code)

    def test_an_unrelated_edit_of_a_staff_account_with_records_still_saves(self):
        child("A", self.sw)
        res = self.client.patch(f"/api/users/{self.sw.pk}/", {"first_name": "Rosa"}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        # The edit form resends the role it already has: that is not a change.
        res = self.client.patch(f"/api/users/{self.sw.pk}/", {
            "first_name": "Rosita", "role": role(Role.STAFF).pk}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.sw.refresh_from_db()
        self.assertEqual(("Rosita", Role.STAFF), (self.sw.first_name, self.sw.role.role_name))

    def test_an_account_that_is_not_staff_is_unaffected(self):
        psy = person("psy@t.ph", Role.PSYCHOLOGIST)
        child("A", self.other, assigned_psychologist=psy)
        self.assertEqual(200, self.change("Officer", psy).status_code)

    def test_the_lock_is_taken_inside_a_transaction_around_the_save(self):
        """The count locks the account's row; UserViewSet.update keeps its OWN
        transaction open (deeper than the test's) so the lock lasts until the
        save."""
        from django.db import connection
        baseline = len(connection.savepoint_ids)
        locked, saved = [], []
        original = User.objects.select_for_update
        original_save = User.save

        def spy():
            locked.append(len(connection.savepoint_ids))
            return original()

        def save_spy(user, *a, **kw):
            if user.pk == self.sw.pk:
                saved.append(len(connection.savepoint_ids))
            return original_save(user, *a, **kw)
        with mock.patch.object(type(User.objects), "select_for_update", side_effect=spy), \
                mock.patch.object(User, "save", save_spy):
            self.assertEqual(200, self.change("Officer").status_code)
        self.assertTrue(locked and all(d > baseline for d in locked), (baseline, locked))
        self.assertTrue(saved and all(d > baseline for d in saved), (baseline, saved))


class AHolderWhoCannotHoldRecordsIsListedAndMovable(APITestCase):
    def setUp(self):
        self.admin = person("admin@racco1.gov.ph", Role.ADMINISTRATOR)
        self.old = person("old@t.ph", Role.STAFF)
        self.new = person("new@t.ph", Role.STAFF)
        self.client.force_authenticate(self.admin)

    def queue(self):
        res = self.client.get("/api/admin/overview/")
        self.assertEqual(200, res.status_code)
        return self._find(res.data)

    def _find(self, data):
        def walk(node):
            if isinstance(node, dict):
                if node.get("key") == "archived_holder":
                    return node
                for v in node.values():
                    found = walk(v)
                    if found:
                        return found
            elif isinstance(node, list):
                for v in node:
                    found = walk(v)
                    if found:
                        return found
        return walk(data)

    def transfer(self, source, target, records):
        return self.client.post("/api/children/transfer/", {
            "from_social_worker": source.pk, "to_social_worker": target.pk,
            "children": [c.pk for c in records]}, format="json")

    def test_promote_reopen_in_place_list_and_move(self):
        held = child("Held", self.old)
        closed = child("Closed", self.old, status=Child.INACTIVE)
        # Promoted only after the active record is moved.
        self.assertEqual(200, self.transfer(self.old, self.new, [held]).status_code)
        self.assertEqual(200, self.client.patch(
            f"/api/users/{self.old.pk}/", {"role": role("Officer").pk}, format="json").status_code)
        # The ISA reopens the closed one in place: it comes back to the promoted account.
        res = self.client.post(f"/api/children/{closed.pk}/reopen/")
        self.assertEqual(200, res.status_code, res.data)
        closed.refresh_from_db()
        self.assertEqual((Child.ACTIVE, self.old.pk), (closed.status, closed.social_worker_id))

        queue = self.queue()
        self.assertEqual(1, queue["count"])
        self.assertEqual("Held by 1 account no longer a social worker", queue["hint"])
        self.assertIn("(no longer a social worker)", queue["items"][0]["sub"])

        # Moving TO the promoted account is refused; moving FROM it works.
        self.assertEqual(400, self.transfer(self.new, self.old, [held]).status_code)
        res = self.transfer(self.old, self.new, [closed])
        self.assertEqual(200, res.status_code, res.data)
        closed.refresh_from_db()
        self.assertEqual(self.new.pk, closed.social_worker_id)
        self.assertEqual(0, self.queue()["count"])

    def test_a_staff_account_changed_to_psychologist_before_the_check_is_listed_and_movable(self):
        stranded = child("Stranded", self.old)
        # Made directly: the API refuses this now.
        self.old.role = role(Role.PSYCHOLOGIST)
        self.old.save(update_fields=["role"])
        self.assertEqual(1, self.queue()["count"])
        res = self.transfer(self.old, self.new, [stranded])
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(0, self.queue()["count"])

    def test_an_account_holding_nothing_is_still_not_a_source(self):
        res = self.transfer(person("psy@t.ph", Role.PSYCHOLOGIST), self.new, [child("X", self.new)])
        self.assertEqual(400, res.status_code)
        self.assertIn("from_social_worker", res.data)

    def test_a_deactivated_target_is_still_refused(self):
        held = child("Held", self.old)
        self.new.status = User.ARCHIVED
        self.new.save()
        res = self.transfer(self.old, self.new, [held])
        self.assertEqual(400, res.status_code)
        self.assertIn("to_social_worker", res.data)
        held.refresh_from_db()
        self.assertEqual(self.old.pk, held.social_worker_id)

    def test_a_non_social_worker_target_is_still_refused(self):
        held = child("Held", self.old)
        self.new.role = role(Role.PSYCHOLOGIST)
        self.new.save(update_fields=["role"])
        self.assertEqual(400, self.transfer(self.old, self.new, [held]).status_code)
        held.refresh_from_db()
        self.assertEqual(self.old.pk, held.social_worker_id)

    def test_the_target_row_is_locked_inside_the_move(self):
        held = child("Held", self.old)
        from django.db import connection
        baseline = len(connection.savepoint_ids)
        seen = []
        original = User.objects.select_for_update

        def spy():
            seen.append(len(connection.savepoint_ids))
            return original()
        with mock.patch.object(type(User.objects), "select_for_update", side_effect=spy):
            self.assertEqual(200, self.transfer(self.old, self.new, [held]).status_code)
        self.assertEqual(1, len(seen))
        self.assertGreater(seen[0], baseline)


class TheApprovalEmailNamesTheOfficer(TestCase):
    @override_settings(SITE_URL="https://nacc.example.ph", BREVO_SENDER_NAME="NACC SYS",
                       BREVO_SENDER_EMAIL="sender@example.ph")
    def test_the_words(self):
        user = User.objects.create_user(email="ana@gmail.com", username="ana@gmail.com",
                                        first_name="Ana", last_name="Cruz", google_sub="sub-ana",
                                        email_verified=True)
        payload = notifications.build_access_approved_payload(user, role("Officer"))
        phrase = "as a Social Welfare Officer IV/OIC (RACC Officer)."
        self.assertIn(phrase, payload["textContent"])
        self.assertIn("IV/OIC (RACC Officer)", payload["htmlContent"])


class NobodyCanClaimTheOfficer(APITestCase):
    def setUp(self):
        cache.clear()
        for name in Role.NAMES:
            role(name)

    def test_it_is_not_claimable(self):
        self.assertNotIn("Officer", capabilities.CLAIMABLE_AT_SIGNUP)
        self.assertNotIn("Officer", capabilities.SIGNS_IN_WITH_GOOGLE)

    def test_the_typed_form_refuses_officer_as_it_refuses_administrator(self):
        def ask(asked, email):
            return self.client.post("/api/auth/signup/", {
                "first_name": "Maria", "last_name": "Santos", "email": email,
                "password": "a-long-enough-passphrase-9", "requested_role": asked},
                format="json")
        admin = ask(Role.ADMINISTRATOR, "a@gmail.com")
        officer = ask("Officer", "o@gmail.com")
        self.assertEqual(400, officer.status_code, officer.data)
        self.assertEqual((admin.status_code, admin.json()), (officer.status_code, officer.json()))
        self.assertFalse(User.objects.filter(email="o@gmail.com").exists())

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
    def test_the_google_door_refuses_officer_as_it_refuses_administrator(self):
        answers = {}
        for asked, email in ((Role.ADMINISTRATOR, "a@gmail.com"), ("Officer", "o@gmail.com")):
            with mock.patch(VERIFY, return_value=claims(email, sub="sub-" + email)):
                res = self.client.post("/api/auth/google/", {"credential": "tok",
                                                             "requested_role": asked}, format="json")
            answers[asked] = (res.status_code, res.json())
            user = User.objects.get(email=email)
            self.assertIsNone(user.requested_role)
            self.assertIsNone(user.role)
        self.assertEqual(answers[Role.ADMINISTRATOR], answers["Officer"])


class TheBackstopHoldsForARealOfficer(APITestCase):
    """The row exists and the account is active, but the Officer is in no list
    until WP3: refused at sign-in, at /me/ and at refresh."""

    def test_sign_in_me_and_refresh(self):
        staff = person("later@racco1.gov.ph", Role.STAFF)
        ok = self.client.post("/api/auth/login/", {"email": "later@racco1.gov.ph", "password": PASSWORD})
        self.assertEqual(200, ok.status_code)
        staff.role = role("Officer")
        staff.save(update_fields=["role"])

        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + ok.data["access"])
        me = self.client.get("/api/auth/me/")
        self.assertEqual(401, me.status_code)
        self.assertEqual("unknown_role", me.data["detail"].code)
        self.client.credentials()

        refreshed = self.client.post("/api/auth/refresh/", {"refresh": ok.data["refresh"]})
        self.assertEqual(401, refreshed.status_code)
        self.assertNotIn("access", refreshed.data)
        self.assertEqual("unknown_role", refreshed.data["detail"].code)

        res = self.client.post("/api/auth/login/", {"email": "later@racco1.gov.ph", "password": PASSWORD})
        self.assertEqual(401, res.status_code)
        self.assertEqual("unknown_role", res.data["detail"].code)
        self.assertNotIn("access", res.data)
