from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase
from accounts.models import Role, User
from children.models import Child, TerminationRecord
from children.tests.payloads import complete


def make_user(email, role_name, **kw):
    role, _ = Role.objects.get_or_create(role_name=role_name)
    username = kw.pop("username", email.split("@")[0])
    return User.objects.create_user(email=email, username=username, password="pass12345", role=role, **kw)


class PsychologistEditTests(APITestCase):
    def setUp(self):
        self.admin = make_user("a@t.ph", Role.ADMINISTRATOR)
        self.staff = make_user("s@t.ph", Role.STAFF)
        self.psych = make_user("p@t.ph", Role.PSYCHOLOGIST)
        self.other_psych = make_user("p2@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(social_worker=self.staff, fullname="Mika Santos",
                                          assigned_psychologist=self.psych)

    def patch(self, user, child, data):
        self.client.force_authenticate(user)
        return self.client.patch(f"/api/children/{child.id}/", data, format="json")

    def test_assigned_psychologist_can_edit(self):
        r = self.patch(self.psych, self.child, {"education_level": "Grade 4"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.child.refresh_from_db()
        self.assertEqual(self.child.education_level, "Grade 4")

    def test_unassigned_psychologist_cannot_edit(self):
        r = self.patch(self.other_psych, self.child, {"education_level": "x"})
        # queryset scoping means the record 404s for them
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_psychologist_cannot_reassign(self):
        r = self.patch(self.psych, self.child, {"psychologist": self.other_psych.id})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_psychologist_cannot_create(self):
        self.client.force_authenticate(self.psych)
        r = self.client.post("/api/children/", {"fullname": "New Kid"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_fullname_locked_on_update_for_everyone(self):
        r = self.patch(self.staff, self.child, {"fullname": "Renamed"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_recommendation_field_roundtrip(self):
        r = self.patch(self.staff, self.child, {"recommendation": "Refer for art therapy."})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["recommendation"], "Refer for art therapy.")


class ConcurrencyPresenceTests(APITestCase):
    def setUp(self):
        self.staff = make_user("s2@t.ph", Role.STAFF)
        self.psych = make_user("p3@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(social_worker=self.staff, fullname="Ana Cruz",
                                          assigned_psychologist=self.psych)

    def test_stale_write_conflicts(self):
        self.client.force_authenticate(self.staff)
        stale = "2000-01-01T00:00:00+00:00"
        r = self.client.patch(f"/api/children/{self.child.id}/",
                              {"education_level": "G1", "expected_updated_at": stale},
                              format="json")
        self.assertEqual(r.status_code, 409)
        self.assertIn("current", r.data)

    def test_fresh_write_passes(self):
        self.client.force_authenticate(self.staff)
        current = self.client.get(f"/api/children/{self.child.id}/").data["updated_at"]
        r = self.client.patch(f"/api/children/{self.child.id}/",
                              {"education_level": "G1", "expected_updated_at": current},
                              format="json")
        self.assertEqual(r.status_code, 200)

    def test_presence_roundtrip(self):
        self.client.force_authenticate(self.psych)
        self.client.post(f"/api/children/{self.child.id}/presence/")
        self.client.force_authenticate(self.staff)
        r = self.client.get(f"/api/children/{self.child.id}/presence/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["others"]), 1)


class NameSplitTests(APITestCase):
    def setUp(self):
        self.staff = make_user("ns@t.ph", Role.STAFF)
        self.client.force_authenticate(self.staff)

    def test_create_composes_fullname(self):
        r = self.client.post("/api/children/", complete(
            first_name="Mika", middle_name="R", last_name="Santos"), format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["fullname"], "Mika R. Santos")

    def test_name_parts_locked_after_create(self):
        r = self.client.post("/api/children/", complete(
            first_name="Ana", last_name="Cruz"), format="json")
        r2 = self.client.patch(f"/api/children/{r.data['id']}/",
                               {"last_name": "Reyes"}, format="json")
        self.assertEqual(r2.status_code, 400)

    def test_create_without_any_name_rejected(self):
        r = self.client.post("/api/children/", {
            "birth_date": "2016-01-10",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_create_with_whitespace_only_fullname_rejected(self):
        r = self.client.post("/api/children/", {
            "fullname": "   ", "birth_date": "2016-01-10",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_create_with_whitespace_only_first_name_rejected(self):
        r = self.client.post("/api/children/", {
            "first_name": "   ", "birth_date": "2016-01-10",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def _payload(self, **over):
        return complete(**{"first_name": "Leo", "last_name": "Diaz",
                           "gender": "Male", **over})

    def test_the_payload_is_otherwise_complete(self):
        # So the two age tests below are refused for the age, not for a blank.
        r = self.client.post("/api/children/", self._payload(), format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_age_below_5_rejected(self):
        r = self.client.post("/api/children/", self._payload(birth_date="2024-01-01"), format="json")
        self.assertEqual(r.status_code, 400)

    def test_age_above_17_rejected(self):
        r = self.client.post("/api/children/", self._payload(birth_date="2000-01-01"), format="json")
        self.assertEqual(r.status_code, 400)

    def test_edit_birth_date_to_out_of_range_rejected(self):
        # A deliberate edit that changes birth_date to something outside
        # 5-17 must still be range-checked - only an UNCHANGED birth_date
        # is allowed to skip re-validation on update.
        child = Child.objects.create(social_worker=self.staff, 
            fullname="Grown Kid", first_name="Grown", last_name="Kid",
            birth_date="2016-01-10", gender="Male", case_type="Foster Care")
        r = self.client.patch(f"/api/children/{child.id}/",
                              {"birth_date": "2001-01-01"}, format="json")
        self.assertEqual(r.status_code, 400)
        child.refresh_from_db()
        self.assertEqual(str(child.birth_date), "2016-01-10")

    def test_edit_with_unchanged_out_of_range_birth_date_still_succeeds(self):
        # A legacy/long-running case whose age has since drifted outside
        # 5-17 must remain editable via the edit form's full-object PUT,
        # which always resends the existing birth_date unchanged.
        child = Child.objects.create(social_worker=self.staff, 
            fullname="Legacy Adult", first_name="Legacy", last_name="Adult",
            birth_date="2000-01-01", gender="Male", case_type="Foster Care")
        r = self.client.put(f"/api/children/{child.id}/", {
            "birth_date": "2000-01-01", "gender": "Male", "case_type": "Foster Care",
            "education_level": "Grade 6",
        }, format="json")
        self.assertEqual(r.status_code, 200)
        child.refresh_from_db()
        self.assertEqual(child.education_level, "Grade 6")

    def test_required_fields_on_create(self):
        r = self.client.post("/api/children/", {"first_name": "Solo"}, format="json")
        self.assertEqual(r.status_code, 400)
        for f in ("last_name", "birth_date", "gender", "case_type"):
            self.assertIn(f, r.data)

    def test_legacy_fullname_create_still_exempt_from_new_required_fields(self):
        # Task 1/12 back-compat path: a bare `fullname` payload is still
        # accepted without birth_date/gender/case_type — Task 13's stricter
        # per-field requirements apply to the first_name/last_name creation
        # flow, not this legacy shape (see children/tests/test_api.py and
        # activity/tests/test_activity.py, which rely on this staying true).
        r = self.client.post("/api/children/", {
            "fullname": "Legacy Kid", "case_type": "Foster Care",
        }, format="json")
        self.assertEqual(r.status_code, 201)

    def test_legacy_fullname_create_still_age_validated(self):
        # The legacy path skips the new birth_date/gender/case_type REQUIRED
        # check, but if a birth_date IS supplied it must still be in range —
        # the legacy exemption must never bypass age validation.
        r = self.client.post("/api/children/", {
            "fullname": "Legacy Kid", "birth_date": "2000-01-01",
        }, format="json")
        self.assertEqual(r.status_code, 400)


class DuplicateCheckTests(APITestCase):
    def setUp(self):
        self.staff = make_user("dc@t.ph", Role.STAFF)
        self.psych = make_user("dp@t.ph", Role.PSYCHOLOGIST)
        self.archived = Child.objects.create(social_worker=self.staff, 
            fullname="Mika R. Santos", first_name="Mika", last_name="Santos",
            birth_date="2016-01-10", status=Child.INACTIVE)

    def test_finds_archived_record(self):
        self.client.force_authenticate(self.staff)
        r = self.client.get("/api/children/check-duplicate/?first_name=mika&last_name=santos")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["matches"]), 1)
        self.assertEqual(r.data["matches"][0]["status"], "inactive")

    def test_birth_date_plus_last_name_matches(self):
        self.client.force_authenticate(self.staff)
        r = self.client.get("/api/children/check-duplicate/?last_name=santos&birth_date=2016-01-10")
        self.assertEqual(len(r.data["matches"]), 1)

    def test_psychologist_forbidden(self):
        self.client.force_authenticate(self.psych)
        r = self.client.get("/api/children/check-duplicate/?first_name=mika&last_name=santos")
        self.assertEqual(r.status_code, 403)


class TerminationPrefetchTests(APITestCase):
    """terminations must be prefetched alongside pre_assessments on
    ChildViewSet.get_queryset() - otherwise get_termination/get_terminations
    (SerializerMethodFields backed by obj.terminations.all()/.first()) issue
    one extra query per child per field, and the /children/ list query count
    scales with the number of children (N+1)."""

    def setUp(self):
        self.staff = make_user("tp@t.ph", Role.STAFF)
        self.client.force_authenticate(self.staff)

    def _make_terminated_child(self, n):
        child = Child.objects.create(social_worker=self.staff, fullname=f"Term Kid {n}", status=Child.INACTIVE)
        TerminationRecord.objects.create(
            child=child, reason_category="Services completed", note="done")
        return child

    def test_list_query_count_does_not_scale_with_terminations(self):
        self._make_terminated_child(1)
        with CaptureQueriesContext(connection) as ctx1:
            r1 = self.client.get("/api/children/?include_archived=true")
        self.assertEqual(r1.status_code, 200)
        baseline = len(ctx1.captured_queries)

        for n in range(2, 5):
            self._make_terminated_child(n)
        with CaptureQueriesContext(connection) as ctx2:
            r2 = self.client.get("/api/children/?include_archived=true")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(ctx2.captured_queries), baseline)


class IdentifyingInformationFieldsTests(APITestCase):
    """New fields matching the agency's official "I. Identifying
    Information" intake form: Place of Birth/Found, Birth Status, Legal
    Status, Date of Admission, Date of Placement to Custodian, Type of
    Adoption - plus the narrowed Category list (Surrendered/Abandoned/
    Dependent/Neglected/Without Known Parents/Orphaned) that replaced the
    prior 18-item NACC-SAMD-GF-000 list."""

    def setUp(self):
        self.staff = make_user("iif@t.ph", Role.STAFF)
        self.client.force_authenticate(self.staff)

    def _payload(self, **over):
        return complete(**{"first_name": "Ivy", "last_name": "Fields", **over})

    def test_create_and_roundtrip_all_new_fields(self):
        r = self.client.post("/api/children/", self._payload(
            case_type="Adoption",
            place_of_birth_or_found="Bauang, La Union",
            birth_status="Non-Marital",
            legal_status="With Issued CDCLAA",
            date_of_placement_to_custodian="2026-02-01",
            type_of_adoption="Foster-Adopt",
            case_category="Without Known Parents",
        ), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["place_of_birth_or_found"], "Bauang, La Union")
        self.assertEqual(r.data["birth_status"], "Non-Marital")
        self.assertEqual(r.data["legal_status"], "With Issued CDCLAA")
        self.assertEqual(r.data["date_of_placement_to_custodian"], "2026-02-01")
        self.assertEqual(r.data["type_of_adoption"], "Foster-Adopt")
        self.assertEqual(r.data["case_category"], "Without Known Parents")

    def test_only_legal_status_is_optional_since_24_sep_2026(self):
        # The six were optional until the form made every applicable question
        # mandatory. Legal status stays optional: a child new to care may not
        # have one yet. The date and type of adoption are asked per case
        # type - see children/tests/test_intake.py.
        r = self.client.post("/api/children/", self._payload(legal_status=""), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        for f in ("place_of_birth_or_found", "birth_status"):
            r = self.client.post("/api/children/", self._payload(**{f: ""}), format="json")
            self.assertEqual(r.status_code, 400, f)
            self.assertIn(f, r.data)

    def test_category_rejects_a_removed_samd_value(self):
        r = self.client.post("/api/children/", self._payload(case_category="Trafficked"), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("case_category", r.data)

    def test_category_accepts_all_six_new_values(self):
        for value in ("Surrendered", "Abandoned", "Dependent", "Neglected", "Without Known Parents", "Orphaned"):
            r = self.client.post("/api/children/", self._payload(
                first_name="Ivy", last_name=f"Fields-{value}", case_category=value,
            ), format="json")
            self.assertEqual(r.status_code, 201, f"{value} should be accepted, got {r.data}")

    def test_existing_record_with_removed_category_value_still_readable(self):
        # A record categorized before the list was narrowed (e.g. under the
        # old NACC-SAMD-GF-000 "Trafficked" option) must still round-trip
        # correctly on read/unrelated-edit - only NEW writes of a removed
        # value are rejected, existing data is not silently dropped.
        child = Child.objects.create(social_worker=self.staff, 
            fullname="Legacy Kid", first_name="Legacy", last_name="Kid",
            birth_date="2016-01-10", gender="Female", case_type="Foster Care",
            case_category="Trafficked")
        r = self.client.get(f"/api/children/{child.id}/")
        self.assertEqual(r.data["case_category"], "Trafficked")
        r2 = self.client.patch(f"/api/children/{child.id}/", {"medical_notes": "update"}, format="json")
        self.assertEqual(r2.status_code, 200)
        child.refresh_from_db()
        self.assertEqual(child.case_category, "Trafficked")

    def test_edit_form_full_put_with_unchanged_legacy_category_does_not_block_save(self):
        # The actual regression this guards against: Children.jsx's edit
        # form does a full PUT resending every field on every save,
        # including case_category unchanged (not a partial PATCH that
        # would just omit it). Without validate_case_category's
        # change-only exemption, this would 400 on a field nobody is
        # touching, permanently blocking any edit to a record that
        # predates the narrowed Category list.
        child = Child.objects.create(social_worker=self.staff, 
            fullname="Legacy Kid", first_name="Legacy", last_name="Kid",
            birth_date="2016-01-10", gender="Female", case_type="Foster Care",
            case_category="Trafficked")
        r = self.client.put(f"/api/children/{child.id}/", {
            "first_name": "Legacy", "last_name": "Kid", "birth_date": "2016-01-10",
            "gender": "Female", "case_type": "Foster Care",
            "case_category": "Trafficked",  # unchanged, resent by the full-object PUT
            "medical_notes": "unrelated update",
        }, format="json")
        self.assertEqual(r.status_code, 200)
        child.refresh_from_db()
        self.assertEqual(child.case_category, "Trafficked")
        self.assertEqual(child.medical_notes, "unrelated update")

    def test_edit_form_full_put_changing_away_from_legacy_category_still_validates(self):
        # A deliberate change AWAY from a legacy value must still be
        # checked against the current (narrowed) choice list.
        child = Child.objects.create(social_worker=self.staff, 
            fullname="Legacy Kid2", first_name="Legacy", last_name="Kid2",
            birth_date="2016-01-10", gender="Female", case_type="Foster Care",
            case_category="Trafficked")
        r = self.client.put(f"/api/children/{child.id}/", {
            "first_name": "Legacy", "last_name": "Kid2", "birth_date": "2016-01-10",
            "gender": "Female", "case_type": "Foster Care",
            "case_category": "CICL",  # a different, also-removed value
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("case_category", r.data)
