from unittest.mock import patch

from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child

User = get_user_model()


class UserManagementTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)

    def _auth(self, email, password):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": password}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_admin_can_create_user(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post("/api/users/", {
            "email": "new@racco1.gov.ph", "username": "newbie",
            "first_name": "New", "last_name": "Bie",
            "role": self.staff_role.id, "password": "pass1234"})
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(User.objects.filter(email="new@racco1.gov.ph").exists())

    def test_staff_cannot_create_user(self):
        self._auth("staff@racco1.gov.ph", "staff1234")
        resp = self.client.post("/api/users/", {
            "email": "x@racco1.gov.ph", "username": "x", "role": self.staff_role.id})
        self.assertEqual(resp.status_code, 403)

    def test_archive_sets_status_and_blocks_login(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post(f"/api/users/{self.staff.id}/archive/")
        self.assertEqual(resp.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.status, User.ARCHIVED)
        self.assertFalse(self.staff.is_active)
        # archived user can no longer authenticate
        login = self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "staff1234"})
        self.assertEqual(login.status_code, 401)

    def test_list_excludes_archived_by_default(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        self.client.post(f"/api/users/{self.staff.id}/archive/")
        resp = self.client.get("/api/users/")
        emails = [u["email"] for u in resp.data]
        self.assertIn("admin@racco1.gov.ph", emails)
        self.assertNotIn("staff@racco1.gov.ph", emails)

    def test_list_includes_archived_when_asked(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        self.client.post(f"/api/users/{self.staff.id}/archive/")
        resp = self.client.get("/api/users/?include_archived=true")
        emails = [u["email"] for u in resp.data]
        self.assertIn("staff@racco1.gov.ph", emails)

    def test_reactivate_restores_the_account_and_forces_a_password_change(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        self.client.post(f"/api/users/{self.staff.id}/archive/")
        # No include_archived= here on purpose: the detail route has to reach a
        # deactivated user by id or reactivate/ is unusable.
        resp = self.client.post(f"/api/users/{self.staff.id}/reactivate/")
        self.assertEqual(resp.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.status, User.ACTIVE)
        self.assertTrue(self.staff.is_active)
        self.assertTrue(self.staff.must_change_password)
        login = self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "staff1234"})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data["user"]["must_change_password"])

    def test_last_administrator_cannot_be_deactivated(self):
        """The agency's recovery account cannot be deactivated while it is the
        only one. Nothing in the product can mint a replacement — approve/
        reactivate both refuse administrators and creating one needs an
        administrator — so allowing this locks everyone out permanently."""
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post(f"/api/users/{self.admin.id}/archive/")
        self.assertEqual(resp.status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.status, User.ACTIVE)
        self.assertTrue(self.admin.is_active)

    def test_last_administrator_cannot_be_deactivated_by_editing_status(self):
        """archive/ is not the only door: `status` is writable on the ordinary
        update endpoint, so the rule has to live where both paths pass."""
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.patch(f"/api/users/{self.admin.id}/",
                                 {"status": "archived"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.status, User.ACTIVE)
        self.assertTrue(self.admin.is_active)

    def test_administrator_can_be_deactivated_while_another_remains(self):
        """The guard is about the LAST administrator, not administrators in
        general — the handover flow depends on being able to archive one."""
        self._auth("admin@racco1.gov.ph", "admin1234")
        other = User.objects.create_user(
            email="admin2@racco1.gov.ph", username="admin2",
            password="admin1234", role=self.admin_role)
        resp = self.client.post(f"/api/users/{other.id}/archive/")
        self.assertEqual(resp.status_code, 200)
        other.refresh_from_db()
        self.assertEqual(other.status, User.ARCHIVED)

    def test_reactivate_on_an_active_user_is_400(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post(f"/api/users/{self.staff.id}/reactivate/")
        self.assertEqual(resp.status_code, 400)

    def test_reactivate_refuses_administrators(self):
        """A deactivated admin comes back only as a brand-new account."""
        self._auth("admin@racco1.gov.ph", "admin1234")
        other = User.objects.create_user(
            email="admin2@racco1.gov.ph", username="admin2",
            password="admin1234", role=self.admin_role)
        self.client.post(f"/api/users/{other.id}/archive/")
        resp = self.client.post(f"/api/users/{other.id}/reactivate/")
        self.assertEqual(resp.status_code, 400)
        other.refresh_from_db()
        self.assertEqual(other.status, User.ARCHIVED)

    def test_staff_cannot_reactivate(self):
        other = User.objects.create_user(
            email="left@racco1.gov.ph", username="left", password="left1234",
            role=self.staff_role, status=User.ARCHIVED, is_active=False)
        self._auth("staff@racco1.gov.ph", "staff1234")
        resp = self.client.post(f"/api/users/{other.id}/reactivate/")
        self.assertEqual(resp.status_code, 403)
        other.refresh_from_db()
        self.assertEqual(other.status, User.ARCHIVED)

    def test_admin_can_list_roles(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.get("/api/roles/")
        self.assertEqual(resp.status_code, 200)
        names = [r["role_name"] for r in resp.data]
        self.assertIn("Administrator", names)
        self.assertIn("Staff", names)


class CreateUserTempPasswordTest(APITestCase):
    """User creation issues a server-generated temporary password, returned
    exactly once, and forces a change at first login — the admin never
    chooses another user's password (same rule as reset-password)."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)

    def _auth(self, email, password):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": password}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _login(self, email, password):
        return self.client.post("/api/auth/login/", {"email": email, "password": password})

    @patch("accounts.views.send_temporary_password_notification")
    def test_create_returns_temp_password_and_forces_change(self, mock_send):
        mock_send.return_value = True
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post("/api/users/", {
            "email": "new@racco1.gov.ph", "first_name": "New", "last_name": "Staff",
            "role": self.staff_role.id})
        self.assertEqual(resp.status_code, 201)
        temp_password = resp.data["temp_password"]
        self.assertTrue(temp_password)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[0].email, "new@racco1.gov.ph")
        self.assertEqual(mock_send.call_args.args[1], temp_password)

        user = User.objects.get(email="new@racco1.gov.ph")
        self.assertTrue(user.must_change_password)

        login = self._login("new@racco1.gov.ph", temp_password)
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data["user"]["must_change_password"])

    def test_client_supplied_password_on_create_is_ignored(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post("/api/users/", {
            "email": "new@racco1.gov.ph", "role": self.staff_role.id,
            "password": "AdminPicked9"})
        self.assertEqual(resp.status_code, 201)
        # The admin-typed password must NOT work — only the generated temp does.
        self.assertEqual(self._login("new@racco1.gov.ph", "AdminPicked9").status_code, 401)
        self.assertEqual(
            self._login("new@racco1.gov.ph", resp.data["temp_password"]).status_code, 200)

    def test_client_supplied_password_on_update_is_ignored(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        target = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="staffPass1",
            role=self.staff_role)
        resp = self.client.put(f"/api/users/{target.id}/", {
            "email": "s@racco1.gov.ph", "password": "Hijacked99"})
        self.assertEqual(resp.status_code, 200)
        # The existing password still works; the admin-typed one does not.
        self.assertEqual(self._login("s@racco1.gov.ph", "staffPass1").status_code, 200)
        self.assertEqual(self._login("s@racco1.gov.ph", "Hijacked99").status_code, 401)


class PsychologistListTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)
        self.staff = User.objects.create_user(email="s@racco1.gov.ph", username="s", password="pass1234", role=self.staff_role)
        self.psy = User.objects.create_user(email="p@racco1.gov.ph", username="p", password="pass1234",
                                            first_name="Levi", last_name="Makalaya", role=self.psy_role)
        Child.objects.create(fullname="A", assigned_psychologist=self.psy)
        Child.objects.create(fullname="B", assigned_psychologist=self.psy)
        Child.objects.create(fullname="C", assigned_psychologist=self.psy, status=Child.ARCHIVED)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {"email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_staff_can_list_psychologists_with_caseload(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.get("/api/psychologists/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["name"], "Levi Makalaya")
        self.assertEqual(resp.data[0]["caseload"], 2)  # archived child not counted

    def test_admin_can_list_psychologists(self):
        self._auth("a@racco1.gov.ph")
        self.assertEqual(self.client.get("/api/psychologists/").status_code, 200)

    def test_psychologist_forbidden(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(self.client.get("/api/psychologists/").status_code, 403)


class PendingAccountStateTest(APITestCase):
    """A pending account exists so an administrator has something to approve.
    Until then it must reach nothing at all — these tests are the guard on
    that, because every hole here is an unapproved stranger inside a system
    holding child case files."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.pending = User.objects.create_user(
            email="hopeful@gmail.com", username="hopeful@gmail.com",
            password="hopeful1234", status=User.PENDING,
            requested_role=self.psych_role)

    def _auth_admin(self):
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_status_drives_is_active(self):
        """is_active is derived, never set by hand — the two cannot drift."""
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_active)
        self.pending.status = User.ACTIVE
        self.pending.save(update_fields=["status", "updated_at"])
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_active)

    def test_pending_account_cannot_log_in_with_a_password(self):
        resp = self.client.post("/api/auth/login/", {
            "email": "hopeful@gmail.com", "password": "hopeful1234"})
        self.assertEqual(resp.status_code, 401)

    def test_pending_account_holds_no_role(self):
        self.assertIsNone(self.pending.role)
        self.assertEqual(self.pending.requested_role, self.psych_role)

    def test_requested_role_does_not_grant_anything(self):
        """The claim is inert. Even asking to be a Psychologist, a pending
        account authenticates nowhere — so no permission check can be reached
        that might consult it."""
        resp = self.client.post("/api/auth/login/", {
            "email": "hopeful@gmail.com", "password": "hopeful1234"})
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn("access", resp.data)

    def test_pending_account_is_not_offered_as_a_psychologist(self):
        self._auth_admin()
        resp = self.client.get("/api/psychologists/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("hopeful@gmail.com", [p["name"] for p in resp.data])

    def test_pending_account_cannot_be_issued_a_password(self):
        """Otherwise an admin could hand out a working credential for an
        account nobody has approved."""
        self._auth_admin()
        resp = self.client.post(f"/api/users/{self.pending.id}/reset-password/")
        self.assertEqual(resp.status_code, 400)

    def test_pending_account_cannot_be_reactivated(self):
        """reactivate/ is the undo for a deactivation, not a back door around
        approval."""
        self._auth_admin()
        resp = self.client.post(f"/api/users/{self.pending.id}/reactivate/")
        self.assertEqual(resp.status_code, 400)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, User.PENDING)

    def test_pending_account_is_visible_in_the_directory(self):
        """Not hidden: an administrator cannot approve what they cannot see."""
        self._auth_admin()
        resp = self.client.get("/api/users/")
        emails = [u["email"] for u in resp.data]
        self.assertIn("hopeful@gmail.com", emails)
        row = next(u for u in resp.data if u["email"] == "hopeful@gmail.com")
        self.assertEqual(row["status"], User.PENDING)
        self.assertIsNone(row["role"])


class AccessRequestApprovalTest(APITestCase):
    """Approving is the only access control this system has: it is the single
    human decision between a stranger with a Gmail address and child case
    records. These tests guard the ways that decision could be bypassed."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.request_user = User.objects.create_user(
            email="hopeful@gmail.com", username="hopeful@gmail.com",
            status=User.PENDING, requested_role=self.psych_role,
            google_sub="sub-hopeful",
            # A Google request, and Google verified the address before this
            # system ever saw it - see accounts/email_verification.py. Approval
            # now refuses an address nobody has proved, so the fixture has to
            # say which door this came through rather than leaving it blank.
            email_verified=True)

    def _auth(self, email="admin@racco1.gov.ph", password="admin1234"):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": password}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def approve(self, role_id=None):
        body = {} if role_id is None else {"role": role_id}
        return self.client.post(f"/api/users/{self.request_user.id}/approve/", body)

    def test_approve_grants_the_role_the_admin_chose(self):
        self._auth()
        resp = self.approve(self.staff_role.id)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.status, User.ACTIVE)
        self.assertEqual(self.request_user.role, self.staff_role)
        self.assertTrue(self.request_user.is_active)

    def test_the_admins_choice_overrides_the_claim(self):
        """Asked to be a Psychologist, approved as Staff. What the admin
        submitted wins, always."""
        self._auth()
        self.approve(self.staff_role.id)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.role, self.staff_role)
        self.assertEqual(self.request_user.requested_role, self.psych_role)

    def test_approving_without_a_role_is_refused(self):
        """The dangerous default. Falling back to requested_role here would
        turn every omitted field into self-assignment."""
        self._auth()
        resp = self.approve()
        self.assertEqual(resp.status_code, 400)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.status, User.PENDING)
        self.assertIsNone(self.request_user.role)

    def test_cannot_approve_someone_as_administrator(self):
        """Otherwise the single-admin handover rule is reachable through a
        dropdown on the approval screen."""
        self._auth()
        resp = self.approve(self.admin_role.id)
        self.assertEqual(resp.status_code, 400)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.status, User.PENDING)
        self.assertIsNone(self.request_user.role)

    def test_approving_records_both_roles_in_the_audit_trail(self):
        self._auth()
        self.approve(self.staff_role.id)
        entry = ActivityLog.objects.filter(entity_id=self.request_user.id).first()
        self.assertIn("Staff", entry.entity_label)
        self.assertIn("Psychologist", entry.entity_label)

    def test_cannot_approve_an_account_that_is_not_pending(self):
        self._auth()
        self.approve(self.staff_role.id)
        again = self.approve(self.psych_role.id)
        self.assertEqual(again.status_code, 400)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.role, self.staff_role)

    def test_decline_archives_so_the_address_cannot_re_request(self):
        self._auth()
        resp = self.client.post(f"/api/users/{self.request_user.id}/decline/")
        self.assertEqual(resp.status_code, 200)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.status, User.ARCHIVED)
        self.assertFalse(self.request_user.is_active)

    def test_declined_request_cannot_then_be_approved(self):
        self._auth()
        self.client.post(f"/api/users/{self.request_user.id}/decline/")
        resp = self.approve(self.staff_role.id)
        self.assertEqual(resp.status_code, 400)

    def test_staff_cannot_approve(self):
        staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)
        self._auth(staff.email, "staff1234")
        resp = self.approve(self.staff_role.id)
        self.assertEqual(resp.status_code, 403)
        self.request_user.refresh_from_db()
        self.assertEqual(self.request_user.status, User.PENDING)

    def test_staff_cannot_decline(self):
        staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)
        self._auth(staff.email, "staff1234")
        resp = self.client.post(f"/api/users/{self.request_user.id}/decline/")
        self.assertEqual(resp.status_code, 403)

    def test_approved_account_still_has_no_password(self):
        """Approval grants a role, not a credential. A Google sign-up gets in
        through Google — this door must never mint a working password."""
        self._auth()
        self.approve(self.staff_role.id)
        self.request_user.refresh_from_db()
        self.assertFalse(self.request_user.has_usable_password())


class UserActivityHistoryTest(APITestCase):
    """"What has this person done?" is the question an agency accountable
    under RA 10173 has to answer about an account. Both directions matter:
    what they did, and what was done to their account."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)

    def _auth(self, email, password):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": password}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_history_includes_what_they_did(self):
        self._auth("staff@racco1.gov.ph", "staff1234")   # writes a login entry
        self.client.credentials()
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.get(f"/api/users/{self.staff.id}/activity/")
        self.assertEqual(resp.status_code, 200)
        logins = [e for e in resp.data if e["action"] == "login"]
        self.assertTrue(logins)
        self.assertTrue(logins[0]["by_them"])

    def test_history_includes_what_was_done_to_them(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        self.client.post(f"/api/users/{self.staff.id}/archive/")
        resp = self.client.get(f"/api/users/{self.staff.id}/activity/")
        archived = [e for e in resp.data if e["action"] == "archived"]
        self.assertTrue(archived)
        # Done TO them, by someone else — the drawer renders this differently
        # from "she archived a case", which is the same verb the other way.
        self.assertFalse(archived[0]["by_them"])

    def test_history_excludes_other_peoples_entries(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        other = User.objects.create_user(
            email="other@racco1.gov.ph", username="other", password="other1234",
            role=self.staff_role)
        self.client.post(f"/api/users/{other.id}/archive/")
        resp = self.client.get(f"/api/users/{self.staff.id}/activity/")
        self.assertFalse([e for e in resp.data if e.get("entity_id") == other.id])

    def test_staff_cannot_read_someone_elses_history(self):
        self._auth("staff@racco1.gov.ph", "staff1234")
        resp = self.client.get(f"/api/users/{self.admin.id}/activity/")
        self.assertEqual(resp.status_code, 403)


class RolelessAccountTest(APITestCase):
    """An active account with no role must not exist, and must reach nothing
    if it somehow does.

    This matters because of how role checks are written across the codebase:
    "if psychologist ... else everything". That was safe while every active
    account had a role. A roleless one lands in the *else* branch of each,
    which on the activity stream means the full audit trail with child names
    in it. Rather than invert a dozen call sites, the state is blocked at
    creation, at reactivation, and at the door."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)

    def _auth(self, email, password):
        resp = self.client.post("/api/auth/login/", {"email": email, "password": password})
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + resp.data["access"])
        return resp

    def test_creating_a_user_without_a_role_is_refused(self):
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post("/api/users/", {
            "email": "norole@racco1.gov.ph", "first_name": "No", "last_name": "Role"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("role", resp.data)
        self.assertFalse(User.objects.filter(email="norole@racco1.gov.ph").exists())

    def test_a_roleless_account_cannot_reach_the_api(self):
        """The backstop. Whatever produces this state in future, the door is shut."""
        stray = User.objects.create_user(
            email="stray@gmail.com", username="stray@gmail.com", password="stray1234")
        self.assertIsNone(stray.role)
        self.assertTrue(stray.is_active)
        login = self.client.post("/api/auth/login/", {
            "email": "stray@gmail.com", "password": "stray1234"})
        token = login.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        # The activity stream is the one that mattered: its else-branch is the
        # full audit trail, and audit entries carry child names.
        self.assertEqual(self.client.get("/api/activity/").status_code, 401)
        self.assertEqual(self.client.get("/api/children/").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_a_declined_request_cannot_be_reactivated(self):
        """Declining archives the account, and reactivate/ takes archived
        accounts — so without this guard, declining and then clicking
        Reactivate turns a refused stranger into an active user."""
        declined = User.objects.create_user(
            email="chancer@gmail.com", username="chancer@gmail.com",
            status=User.ARCHIVED, google_sub="sub-chancer")
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post(f"/api/users/{declined.id}/reactivate/")
        self.assertEqual(resp.status_code, 400)
        declined.refresh_from_db()
        self.assertEqual(declined.status, User.ARCHIVED)
        self.assertFalse(declined.is_active)

    def test_a_real_colleague_can_still_be_reactivated(self):
        """The guard must not break the case reactivate/ exists for."""
        colleague = User.objects.create_user(
            email="left@racco1.gov.ph", username="left", password="left1234",
            role=self.staff_role, status=User.ARCHIVED)
        self._auth("admin@racco1.gov.ph", "admin1234")
        resp = self.client.post(f"/api/users/{colleague.id}/reactivate/")
        self.assertEqual(resp.status_code, 200)
        colleague.refresh_from_db()
        self.assertEqual(colleague.status, User.ACTIVE)


class RoleChangeTest(APITestCase):
    """Roles used to be frozen once assigned, which left a mis-assignment with
    no fix except deleting the person and starting again. They can be corrected
    now — except in the two directions that would break the single-admin rule."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role, first_name="Ana", last_name="Reyes")
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def put(self, user, **extra):
        body = {"email": user.email, "first_name": user.first_name,
                "last_name": user.last_name}
        body.update(extra)
        # JSON, not multipart: the browser sends JSON, and a null role only
        # survives the trip in that encoding.
        return self.client.put(f"/api/users/{user.id}/", body, format="json")

    def test_a_role_can_be_corrected(self):
        resp = self.put(self.staff, role=self.psych_role.id)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, self.psych_role)

    def test_the_change_is_recorded_with_both_roles(self):
        self.put(self.staff, role=self.psych_role.id)
        labels = [e.entity_label for e in
                  ActivityLog.objects.filter(entity_id=self.staff.id)]
        self.assertTrue(any("Staff" in l and "Psychologist" in l for l in labels),
                        f"neither role named in the audit trail: {labels}")

    def test_nobody_can_be_promoted_into_administrator(self):
        """That path exists only through account creation, which runs the
        handover. Reaching it by edit would leave two live administrators."""
        resp = self.put(self.staff, role=self.admin_role.id)
        self.assertEqual(resp.status_code, 400)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, self.staff_role)

    def test_an_administrator_cannot_be_demoted(self):
        """It is the agency's recovery account, and there is only one."""
        resp = self.put(self.admin, role=self.staff_role.id)
        self.assertEqual(resp.status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, self.admin_role)

    def test_editing_other_fields_without_touching_the_role_still_works(self):
        resp = self.put(self.staff, first_name="Anna", role=self.staff_role.id)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.first_name, "Anna")
        self.assertEqual(self.staff.role, self.staff_role)

    def test_an_unchanged_administrator_can_still_be_edited(self):
        """The demotion guard must not block ordinary edits to an admin."""
        resp = self.put(self.admin, first_name="Reynold", role=self.admin_role.id)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, "Reynold")

    def test_a_role_cannot_be_emptied(self):
        """A roleless active account is refused at sign-in, so clearing the
        field would lock the person out with no explanation. Deactivation is
        the way to remove access, and it says so."""
        resp = self.put(self.staff, role=None)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Deactivate", str(resp.data))
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, self.staff_role)

    def test_leaving_the_role_out_of_the_payload_is_not_a_removal(self):
        """Only a present-and-empty value means "remove"; a partial edit that
        never mentions the role must not be read as one."""
        resp = self.client.patch(
            f"/api/users/{self.staff.id}/", {"first_name": "Anna"})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, self.staff_role)
        self.assertEqual(self.staff.first_name, "Anna")

    def test_a_pending_request_can_still_be_saved_without_a_role(self):
        """Someone awaiting approval genuinely has none — the guard is about
        taking a role away, not about accounts that never had one."""
        applicant = User.objects.create_user(
            email="applicant@gmail.com", username="applicant@gmail.com",
            status=User.PENDING, first_name="Jo", last_name="Cruz")
        resp = self.put(applicant, role=None)
        self.assertEqual(resp.status_code, 200, resp.data)
