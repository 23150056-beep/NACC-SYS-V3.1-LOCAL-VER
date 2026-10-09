"""The same child cannot be added twice, by any route (found 9 Oct 2026).

A new record is refused when one already exists - any social worker's, active
or closed - with the same first name, last name AND birth date. A name alone
is not enough, because two children share a common one. The sentence says what
the duplicate check already tells this requester, and no more.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from children import duplicates
from children.models import Child
from children.tests.payloads import complete

User = get_user_model()
BORN = "2016-01-10"


class OneRecordPerChildTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        cls.sw = make("sw@t.ph", "Editha", "Pascua", Role.STAFF)
        cls.other_sw = make("sw2@t.ph", "Rosa", "Santos", Role.STAFF)
        cls.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        cls.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        cls.libai = Child.objects.create(
            first_name="Libai", last_name="Cramm", birth_date=BORN,
            case_type="Adoption", case_category="Without Known Parents",
            social_worker=cls.sw)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _add(self, user=None, **over):
        body = {"first_name": "Libai", "last_name": "Cramm", "birth_date": BORN, **over}
        return self._as(user or self.sw).post("/api/children/", complete(**body), format="json")

    def _refused(self, res, sentence):
        self.assertEqual(400, res.status_code, res.data)
        self.assertEqual({"detail": sentence}, res.data)
        self.assertEqual(1, Child.objects.count())

    def _ref(self, child=None):
        return f"C-{(child or self.libai).pk:04d}"

    # --- Refused -------------------------------------------------------------

    def test_the_requesters_own_record_is_named_by_its_case_reference(self):
        self._refused(self._add(), f"This child already has a record ({self._ref()}). "
                                   "Open it instead of adding a second one.")

    def test_names_are_compared_trimmed_and_without_regard_to_case(self):
        res = self._add(first_name="  LIBAI ", last_name="cramm")
        self._refused(res, f"This child already has a record ({self._ref()}). "
                           "Open it instead of adding a second one.")

    def test_a_tilde_is_a_letter_not_a_difference(self):
        """SQLite folds only ASCII, so this is why the comparison is not left
        to the database: PEÑA and Peña are one surname."""
        nino = Child.objects.create(first_name="Niño", last_name="Peña", birth_date=BORN,
                                    social_worker=self.sw)
        res = self._add(first_name="NIÑO", last_name="PEÑA")
        self.assertEqual(400, res.status_code, res.data)
        self.assertIn(self._ref(nino), res.data["detail"])
        # Composed or decomposed, the same letter.
        res = self._add(first_name="Niño", last_name="Peña")
        self.assertEqual(400, res.status_code, res.data)

    def test_another_social_workers_active_record_names_who_holds_it_and_no_id(self):
        res = self._add(user=self.other_sw)
        self._refused(res, "A record for this child is already held by Editha Pascua. "
                           "Ask the ISA (Administrator) to transfer it to you.")
        # A sentence and nothing else: no id to open, no case reference.
        self.assertNotIn(self._ref(), res.data["detail"])

    def test_a_record_with_nobody_holding_it_says_so(self):
        Child.objects.filter(pk=self.libai.pk).update(social_worker=None)
        self._refused(self._add(user=self.other_sw),
                      "A record for this child already exists and is not with a social "
                      "worker yet. Ask the ISA (Administrator) to assign it to you.")

    def test_a_closed_record_is_pointed_at_the_reopen_warning(self):
        Child.objects.filter(pk=self.libai.pk).update(status=Child.INACTIVE)
        closed = ("This child has a closed record. Reopen it from the warning above "
                  "instead of adding a new one.")
        self._refused(self._add(), closed)                       # its own holder
        self._refused(self._add(user=self.other_sw), closed)     # someone else's
        self._refused(self._add(user=self.admin), closed)        # the ISA

    def test_the_isa_is_told_the_reference_of_any_active_record(self):
        self._refused(self._add(user=self.admin),
                      f"This child already has a record ({self._ref()}). "
                      "Open it instead of adding a second one.")

    def test_an_active_record_is_what_is_reported_when_there_is_also_a_closed_one(self):
        Child.objects.create(first_name="Libai", last_name="Cramm", birth_date=BORN,
                             status=Child.INACTIVE, social_worker=self.other_sw)
        res = self._add()
        self.assertEqual(400, res.status_code)
        self.assertIn(self._ref(), res.data["detail"])

    # --- Allowed ---------------------------------------------------------------

    def test_the_same_name_with_another_birth_date_is_another_child(self):
        res = self._add(birth_date="2015-06-30")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(2, Child.objects.count())

    def test_the_same_birth_date_with_another_name_is_another_child(self):
        for over in ({"first_name": "Bea"}, {"last_name": "Dizon"}):
            res = self._add(**over)
            self.assertEqual(201, res.status_code, res.data)

    def test_a_name_alone_is_never_enough(self):
        """Two children share a common name. The birth date has to match."""
        Child.objects.create(first_name="Maria", last_name="Santos", birth_date="2014-03-03",
                             social_worker=self.other_sw)
        res = self._add(first_name="Maria", last_name="Santos", birth_date="2013-03-03")
        self.assertEqual(201, res.status_code, res.data)

    def test_the_fullname_only_door_stays_exempt(self):
        """Older integrations and tests create a child from a fullname alone;
        there is neither name parts nor, usually, a birth date to compare."""
        res = self._as(self.sw).post(
            "/api/children/", {"fullname": "Libai Cramm", "birth_date": BORN}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        res = self._as(self.sw).post("/api/children/", {"fullname": "Libai Cramm"},
                                     format="json")
        self.assertEqual(201, res.status_code, res.data)

    def test_an_edit_is_not_held_to_it(self):
        """Two records for one child that already exist stay editable: the
        rule is for adding."""
        twin = Child.objects.create(first_name="Libai", last_name="Cramm", birth_date=BORN,
                                    social_worker=self.sw, referral_reason="a")
        res = self._as(self.sw).patch(f"/api/children/{twin.pk}/", {"referral_reason": "b"},
                                      format="json")
        self.assertEqual(200, res.status_code, res.data)

    # --- The rule is one function ------------------------------------------------

    def test_same_child_needs_all_three(self):
        self.assertEqual([self.libai], duplicates.same_child("libai", "CRAMM", BORN))
        self.assertEqual([], duplicates.same_child("Libai", "Cramm", None))
        self.assertEqual([], duplicates.same_child("", "Cramm", BORN))
        self.assertEqual([], duplicates.same_child("Libai", "  ", BORN))
        self.assertEqual([], duplicates.same_child("Libai", "Cramm", BORN, exclude=self.libai.pk))
