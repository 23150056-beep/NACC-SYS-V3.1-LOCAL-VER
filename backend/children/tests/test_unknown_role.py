"""A role the code admits but names nowhere, at the children's own doors.

Five decisions here ended in an `else` that served the ISA's path (Add Record,
the edit that writes the psychologist, the closure reasons) or a psychologist's
(the custodian). Each names who takes each branch now and refuses the rest.
Some sit behind an earlier gate (a permission class, the queryset), so those
tests widen the one list that gate reads - a role that gets in and is in no
other list is the shape of the Officer - or call the method directly.
"""
from datetime import date
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.probe import admit, make_probe
from children import custodian, termination
from children.models import AssignmentRequest, Child, TerminationRecord
from children.tests.payloads import complete
from children.views import ChildViewSet

User = get_user_model()


class _Base(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345", role=roles[role])
        self.admin = make("admin@t.ph", Role.ADMINISTRATOR)
        self.sw = make("sw@t.ph", Role.STAFF)
        self.psy = make("psy@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(first_name="Ana", last_name="Cruz",
                                          birth_date=date(2016, 1, 10),
                                          assigned_psychologist=self.psy,
                                          social_worker=self.sw)
        admit(self)
        self.probe = make_probe()

    def as_(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c


class ClosureReasons(_Base):
    def test_nothing_is_offered(self):
        self.assertEqual(([], None), termination.reasons_for("Probe", self.child))

    def test_nothing_is_accepted(self):
        for reason in TerminationRecord.CASE_OUTCOMES + TerminationRecord.CLINICAL:
            self.assertEqual("Select a termination reason.",
                             termination.refusal("Probe", self.child, reason), reason)

    def test_the_control_the_administrator_is_offered_the_outcomes(self):
        offered, _ = termination.reasons_for(Role.ADMINISTRATOR, self.child)
        self.assertEqual(TerminationRecord.CASE_OUTCOMES, [r["value"] for r in offered])

    def test_the_endpoints_refuse_a_role_that_can_see_the_record(self):
        admit(self, "SEES_EVERY_RECORD")
        url = f"/api/children/{self.child.pk}/"
        self.assertEqual(403, self.as_(self.probe).get(url + "closure-reasons/").status_code)
        res = self.as_(self.probe).post(url + "terminate/", {
            "reason": TerminationRecord.CASE_OUTCOMES[0], "summary": "closing"},
            format="json")
        self.assertEqual(403, res.status_code)
        self.child.refresh_from_db()
        self.assertEqual(Child.ACTIVE, self.child.status)


class AddRecord(_Base):
    def test_the_permission_class_refuses_it(self):
        res = self.as_(self.probe).post("/api/children/", complete(), format="json")
        self.assertEqual(403, res.status_code)

    # A role that keeps records and oversees nothing - KEEPS_RECORDS widened,
    # OVERSEES_CASEWORK not: the Officer's shape between WP3 and WP4. Adding a
    # record is the KEEPS_RECORDS question; naming its social worker is
    # validate_social_worker's, which reads OVERSEES_CASEWORK.
    SW_REFUSAL = "Only an administrator can move a record to another social worker."

    def test_a_record_keeper_who_oversees_nothing_adds_a_record_that_belongs_to_nobody(self):
        admit(self, "KEEPS_RECORDS")
        res = self.as_(self.probe).post("/api/children/", complete(), format="json")
        self.assertEqual(201, res.status_code, res.data)
        made = Child.objects.get(pk=res.data["id"])
        self.assertIsNone(made.social_worker)
        self.assertEqual(2, Child.objects.count())

    def test_naming_a_social_worker_is_refused_with_the_serializers_sentence(self):
        admit(self, "KEEPS_RECORDS")
        res = self.as_(self.probe).post(
            "/api/children/", complete(social_worker=self.sw.pk), format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual([self.SW_REFUSAL], res.data["social_worker"])
        self.assertEqual(1, Child.objects.count())

    def test_an_explicit_null_social_worker_passes_for_a_record_keeper(self):
        """WP3 decision: on a NEW record an explicit null is the same as leaving
        the key out - the record belongs to nobody. Naming a holder (the test
        above) and any change on an edit stay with OVERSEES_CASEWORK."""
        admit(self, "KEEPS_RECORDS")
        res = self.as_(self.probe).post(
            "/api/children/", complete(social_worker=None), format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertIsNone(Child.objects.get(pk=res.data["id"]).social_worker)

    def test_the_control_the_social_worker_and_the_isa_can(self):
        self.assertEqual(201, self.as_(self.sw).post(
            "/api/children/", complete(), format="json").status_code)
        self.assertEqual(201, self.as_(self.admin).post(
            "/api/children/", complete(first_name="Dina"), format="json").status_code)

    def test_the_method_refuses_before_it_reads_anything(self):
        view = ChildViewSet()
        view.request = SimpleNamespace(user=self.probe)
        with self.assertRaises(PermissionDenied):
            view.perform_create(SimpleNamespace(validated_data={}))


class AnEdit(_Base):
    def test_the_permission_class_refuses_it(self):
        res = self.as_(self.probe).patch(f"/api/children/{self.child.pk}/",
                                         {"first_name": "Anna"}, format="json")
        self.assertIn(res.status_code, (403, 404))

    def test_the_method_refuses_a_role_that_reaches_it(self):
        view = ChildViewSet()
        view.request = SimpleNamespace(user=self.probe)
        serializer = SimpleNamespace(validated_data={"assigned_psychologist": self.psy},
                                     instance=self.child)
        with self.assertRaises(PermissionDenied):
            view.perform_update(serializer)


class TheCustodian(_Base):
    def test_a_change_is_refused(self):
        attrs = {"custodian_name": "Rosa Dela Cruz"}
        refused = custodian.apply(attrs, self.child, self.probe, "Probe")
        self.assertIn("custodian_name", refused)

    def test_the_case_type_exception_is_the_psychologists_alone(self):
        self.child.custodian_name = "Rosa Dela Cruz"
        self.child.case_type = "Foster Care"
        attrs = {"case_type": "Residential Care", "custodian_name": ""}
        refused = custodian.apply(dict(attrs), self.child, self.probe, "Probe")
        self.assertIn("custodian_name", refused)
        self.assertEqual({}, custodian.apply(dict(attrs), self.child, self.psy,
                                             Role.PSYCHOLOGIST))


class TheRequestList(_Base):
    def setUp(self):
        super().setUp()
        AssignmentRequest.objects.create(child=self.child, psychologist=self.psy,
                                         requested_by=self.sw)

    def test_the_list_is_empty(self):
        res = self.as_(self.probe).get("/api/assignment-requests/")
        self.assertEqual(200, res.status_code)
        self.assertEqual([], list(res.data))

    def test_the_control_the_administrator_sees_it(self):
        self.assertEqual(1, len(self.as_(self.admin).get("/api/assignment-requests/").data))

    def test_check_duplicate_is_refused(self):
        res = self.as_(self.probe).get("/api/children/check-duplicate/", {
            "first_name": "Ana", "last_name": "Cruz", "birth_date": "2016-01-10"})
        self.assertEqual(403, res.status_code)
