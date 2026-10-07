"""The Add Record form of 24 Sep 2026: Child's Profile with the Category first,
a street address, middle name, date found, the renamed and retired values, the
one-date rule, and every question that applies answered before a record saves.

The rules live in children/intake.py and, for the browser, in
frontend/src/config/caseData.js. The first class here pins the two together,
because the failure when they drift is quiet: the form lets somebody through
and the server refuses, or the form asks for something the server would take
blank.
"""
import json
import re
import shutil
import tempfile
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role, User
from children import intake
from children.models import Child, middle_initial_of
from children.tests.payloads import complete
from children.tests.test_child_collab import make_user
from locations.models import Barangay, Municipality, Province

CASE_DATA_JS = (Path(__file__).resolve().parents[3]
                / "frontend" / "src" / "config" / "caseData.js")


def _js_array(source, name):
    match = re.search(r"export\s+const\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\];",
                      source, re.S)
    return re.findall(r"'([^']+)'", match.group(1)) if match else None


def _js_map(source, name, aliases):
    """`export const NAME = { key: [...] | ALIAS, ... }` as a dict."""
    match = re.search(r"export\s+const\s+" + re.escape(name) + r"\s*=\s*\{(.*?)\n\};",
                      source, re.S)
    if match is None:
        return None
    out = {}
    for line in match.group(1).splitlines():
        entry = re.match(r"\s*'?([^':]+?)'?\s*:\s*(\[.*?\]|\w+),?\s*$", line)
        if not entry:
            continue
        key, value = entry.groups()
        out[key] = (re.findall(r"'([^']+)'", value) if value.startswith("[")
                    else aliases[value])
    return out


class TheFormAndTheServerAgreeTest(SimpleTestCase):
    def setUp(self):
        self.assertTrue(CASE_DATA_JS.exists(), f"{CASE_DATA_JS} has moved; this test is pinned to it")
        self.js = CASE_DATA_JS.read_text(encoding="utf-8")

    def test_the_categories(self):
        self.assertEqual(intake.ALL_CATEGORIES, _js_array(self.js, "CASE_CATEGORIES"))
        self.assertEqual(intake.ALL_CATEGORIES, [c for c, _ in Child.CASE_CATEGORY_CHOICES])

    def test_which_categories_each_case_type_offers(self):
        js = _js_map(self.js, "CASE_CATEGORY_OPTIONS",
                     {"CASE_CATEGORIES": _js_array(self.js, "CASE_CATEGORIES")})
        self.assertEqual(intake.CATEGORY_OPTIONS, js)
        self.assertEqual(sorted(intake.CATEGORY_OPTIONS), sorted(c for c, _ in Child.CASE_TYPE_CHOICES))

    def test_the_questions_each_case_type_asks(self):
        self.assertEqual(intake.CASE_TYPE_FIELDS, _js_map(self.js, "CASE_TYPE_FIELDS", {}))

    def test_the_lists_that_changed(self):
        self.assertEqual(["Marital", "Non-Marital", "Child", "Unknown"],
                         _js_array(self.js, "BIRTH_STATUSES"))
        self.assertEqual(_js_array(self.js, "BIRTH_STATUSES"),
                         [c for c, _ in Child.BIRTH_STATUS_CHOICES])
        adoption = _js_array(self.js, "TYPES_OF_ADOPTION")
        self.assertEqual(adoption, [c for c, _ in Child.TYPE_OF_ADOPTION_CHOICES])
        self.assertIn("Relative (Without 2-yr custody)", adoption)
        self.assertEqual(adoption.index("Domestic Relative") + 1,
                         adoption.index("Relative (Without 2-yr custody)"))
        # The SCSR's order, with the agency's own "Relative (Without 2-yr
        # custody)" after Domestic Relative.
        self.assertEqual(["Regular", "Domestic Relative", "Relative (Without 2-yr custody)",
                          "Step-parent", "Adult", "SIBRA", "ICA Relative", "IP", "Foster-Adopt"],
                         adoption)

    def test_the_health_conditions(self):
        self.assertEqual(intake.HEALTH_CONDITIONS, _js_array(self.js, "HEALTH_CONDITIONS"))
        self.assertEqual(intake.HEALTH_CONDITIONS,
                         [c for c, _ in Child.HEALTH_CONDITION_CHOICES])
        self.assertIn(f"export const SPECIAL_NEEDS = '{intake.SPECIAL_NEEDS}';", self.js)
        self.assertIn(f"export const ALIAS_CATEGORY = '{intake.ALIAS_CATEGORY}';", self.js)

    def test_the_special_needs_are_asked_only_with_special_needs(self):
        asked = intake.required_fields("Foster Care", "", intake.SPECIAL_NEEDS)
        self.assertIn("special_needs", asked)
        self.assertNotIn("special_needs", intake.required_fields("Foster Care", "", "Healthy"))
        self.assertIn("special_needs", re.search(
            r"export const requiredFields = .*?\n\};?\n", self.js, re.S).group(0))

    def test_what_is_always_required(self):
        self.assertEqual(intake.ALWAYS_REQUIRED, _js_array(self.js, "ALWAYS_REQUIRED"))

    def test_what_a_case_type_change_asks_again(self):
        # The edit form uses it to tell a blank the save will refuse from an
        # old one it will not.
        self.assertEqual(sorted(intake.DYNAMIC), sorted(_js_array(self.js, "DYNAMIC") or []))

    def test_the_referral_sources(self):
        self.assertEqual(["RACCO", "LGU", "CCA", "RCF"], _js_array(self.js, "REFERRAL_SOURCES"))
        self.assertEqual(_js_array(self.js, "REFERRAL_SOURCES"),
                         [c for c, _ in Child.REFERRAL_SOURCE_CHOICES])

    def test_which_case_types_record_a_placement_and_which_an_admission(self):
        placed = re.search(r"if \(\[(.*?)\]\.includes\(caseType\)\) return PLACEMENT", self.js)
        admitted = re.search(r"if \(\[(.*?)\]\.includes\(caseType\)\) return ADMISSION", self.js)
        self.assertIsNotNone(placed, "dateFieldFor changed shape; update this test")
        self.assertIsNotNone(admitted, "dateFieldFor changed shape; update this test")
        for case_type in re.findall(r"'([^']+)'", placed.group(1)):
            self.assertEqual(intake.PLACEMENT, intake.date_field_for(case_type))
        for case_type in re.findall(r"'([^']+)'", admitted.group(1)):
            self.assertEqual(intake.ADMISSION, intake.date_field_for(case_type))
        self.assertIn("typeOfAdoption === 'Regular' ? ADMISSION : PLACEMENT", self.js)


class TheDateRuleTest(SimpleTestCase):
    def test_adoption_goes_by_the_type_of_adoption(self):
        self.assertEqual(intake.ADMISSION, intake.date_field_for("Adoption", "Regular"))
        for placed in ("Domestic Relative", "Relative (Without 2-yr custody)", "Step-parent",
                       "Adult", "IP", "Foster-Adopt", "SIBRA", "ICA Relative"):
            self.assertEqual(intake.PLACEMENT, intake.date_field_for("Adoption", placed), placed)
        self.assertIsNone(intake.date_field_for("Adoption", ""))

    def test_the_other_tracks(self):
        for placed in ("Foster Care", "Kinship Care", "Family Tracing & Reunification"):
            self.assertEqual(intake.PLACEMENT, intake.date_field_for(placed), placed)
        for admitted in ("Residential Care", "Independent Living"):
            self.assertEqual(intake.ADMISSION, intake.date_field_for(admitted), admitted)


class MiddleNameTest(TestCase):
    def test_a_whole_middle_name_shows_as_its_initial(self):
        self.assertEqual("D.", middle_initial_of("Dela Cruz"))
        self.assertEqual("D.", middle_initial_of("delos Santos"))
        self.assertEqual("U.", middle_initial_of("Uy"))

    def test_an_initial_already_on_record_keeps_its_shape(self):
        # What every record from before 24 Sep 2026 holds.
        for held, shown in (("R", "R."), ("R.", "R."), ("DC", "DC."), ("DC.", "DC.")):
            self.assertEqual(shown, middle_initial_of(held), held)

    def test_none(self):
        self.assertEqual("", middle_initial_of(""))
        self.assertEqual("", middle_initial_of("   "))

    def test_the_display_name(self):
        child = Child(first_name="Mika", middle_name="Dela Cruz", last_name="Santos")
        child.save()
        self.assertEqual("Mika D. Santos", child.fullname)


class _Staff(APITestCase):
    def setUp(self):
        self.staff = make_user("intake@t.ph", Role.STAFF)
        self.client.force_authenticate(self.staff)

    def post(self, **over):
        return self.client.post("/api/children/", complete(**over), format="json")


class CreatingARecordTest(_Staff):
    def test_a_complete_record_saves_with_every_new_field(self):
        r = self.post(date_found="2016-02-01", landmark="Beside the chapel",
                      legal_status="With IVC")
        self.assertEqual(201, r.status_code, r.data)
        for field, value in (("middle_name", "Dela Cruz"), ("house_number", "12"),
                             ("street", "Rizal St."), ("landmark", "Beside the chapel"),
                             ("date_found", "2016-02-01"), ("fullname", "Mika D. Santos")):
            self.assertEqual(value, r.data[field], field)

    def test_every_required_answer_is_required(self):
        for field in intake.required_fields("Foster Care"):
            r = self.post(**{field: ""})
            self.assertEqual(400, r.status_code, f"{field} left blank was accepted")
            self.assertIn(field, r.data)

    def test_the_answers_that_do_not_always_exist_are_optional(self):
        r = self.post(middle_name="", legal_status="", landmark="", date_found=None)
        self.assertEqual(201, r.status_code, r.data)

    def test_an_adoption_asks_for_its_type_then_the_date_that_type_records(self):
        r = self.post(case_type="Adoption", date_of_placement_to_custodian=None)
        self.assertEqual(400, r.status_code)
        self.assertIn("type_of_adoption", r.data)

        r = self.post(case_type="Adoption", type_of_adoption="Regular",
                      date_of_placement_to_custodian=None)
        self.assertEqual(400, r.status_code)
        self.assertEqual({"date_of_admission"}, set(r.data))

        r = self.post(case_type="Adoption", type_of_adoption="Regular",
                      date_of_placement_to_custodian=None, date_of_admission="2026-03-01")
        self.assertEqual(201, r.status_code, r.data)

        r = self.post(case_type="Adoption", last_name="Reyes",
                      type_of_adoption="Relative (Without 2-yr custody)")
        self.assertEqual(201, r.status_code, r.data)

    def test_residential_care_records_the_admission_and_no_custodian(self):
        r = self.post(case_type="Residential Care", custodian_name="",
                      date_of_placement_to_custodian=None)
        self.assertEqual(400, r.status_code)
        self.assertEqual({"date_of_admission"}, set(r.data))
        r = self.post(case_type="Residential Care", custodian_name="",
                      date_of_placement_to_custodian=None, date_of_admission="2026-03-01")
        self.assertEqual(201, r.status_code, r.data)

    def test_a_category_the_case_type_does_not_offer_is_refused(self):
        r = self.post(case_type="Independent Living", case_category="Surrendered",
                      custodian_name="", date_of_placement_to_custodian=None,
                      date_of_admission="2026-03-01")
        self.assertEqual(400, r.status_code)
        self.assertIn("case_category", r.data)

    def test_renamed_and_retired_values_cannot_be_picked(self):
        for field, value in (("case_category", "Orphan"), ("birth_status", "N/A")):
            r = self.post(**{field: value})
            self.assertEqual(400, r.status_code, value)
            self.assertIn(field, r.data)
        r = self.post(case_type="Adoption", type_of_adoption="Stepparent")
        self.assertEqual(400, r.status_code)
        self.assertIn("type_of_adoption", r.data)

    def test_the_choices_offered_again_on_7_oct_are_new_picks(self):
        """Birth status Child, and the adoption types SIBRA and ICA Relative
        (SCSR Part I), were retired on 24 Sep 2026 and are offered again."""
        r = self.post(birth_status="Child", last_name="Pick1")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("Child", r.data["birth_status"])
        for n, kind in enumerate(("SIBRA", "ICA Relative")):
            r = self.post(case_type="Adoption", type_of_adoption=kind, last_name=f"Pick{n + 2}")
            self.assertEqual(201, r.status_code, (kind, r.data))
            self.assertEqual(kind, r.data["type_of_adoption"])
            # Only a Regular adoption records the admission; these, the placement.
            self.assertEqual(intake.PLACEMENT, intake.date_field_for("Adoption", kind))
        r = self.post(case_type="Adoption", type_of_adoption="SIBRA",
                      date_of_placement_to_custodian=None, last_name="NoDate")
        self.assertEqual(400, r.status_code)
        self.assertEqual({"date_of_placement_to_custodian"}, set(r.data))

    def test_health_condition_is_always_asked(self):
        for blank in ("", None):
            r = self.post(health_condition=blank)
            self.assertEqual(400, r.status_code, blank)
            self.assertIn("health_condition", r.data)
        r = self.post(health_condition="Sickly")
        self.assertEqual(400, r.status_code)
        self.assertIn("health_condition", r.data)
        r = self.post(health_condition="Healthy", last_name="Fine")
        self.assertEqual(201, r.status_code, r.data)

    def test_the_special_needs_are_required_only_for_special_needs(self):
        r = self.post(health_condition="With special needs", last_name="Needs1")
        self.assertEqual(400, r.status_code)
        self.assertEqual({"special_needs"}, set(r.data))
        r = self.post(health_condition="With special needs", special_needs="   ", last_name="Needs2")
        self.assertEqual(400, r.status_code, "spaces are a blank")
        r = self.post(health_condition="With special needs", special_needs="Asthma",
                      last_name="Needs3")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("Asthma", r.data["special_needs"])
        # Healthy takes none: what was typed beside it is not kept.
        r = self.post(health_condition="Healthy", special_needs="Asthma", last_name="Needs4")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("", r.data["special_needs"])

    def test_current_whereabouts_is_asked(self):
        r = self.post(current_placement="")
        self.assertEqual(400, r.status_code)
        self.assertIn("current_placement", r.data)
        r = self.post(current_placement="With the maternal aunt", last_name="Where")
        self.assertEqual(201, r.status_code, r.data)

    def test_the_alias_is_optional_and_saved(self):
        r = self.post(case_category="Without Known Parents", alias="Bunso", last_name="Alias1")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("Bunso", r.data["alias"])
        r = self.post(last_name="Alias2")
        self.assertEqual(201, r.status_code, "no alias is fine")
        self.assertEqual("", r.data["alias"])

    def test_the_new_values_can(self):
        r = self.post(case_category="Orphaned", birth_status="Unknown")
        self.assertEqual(201, r.status_code, r.data)

    def test_educational_placement_is_asked_and_referral_source_is_a_pick(self):
        r = self.post(education_level="")
        self.assertEqual(400, r.status_code)
        self.assertIn("education_level", r.data)
        r = self.post(education_level="Not in school", referral_source="LGU")
        self.assertEqual(201, r.status_code, r.data)
        r = self.post(last_name="Tan", referral_source="MSWDO San Fernando")
        self.assertEqual(400, r.status_code, "typed text is no longer a new pick")
        self.assertIn("referral_source", r.data)
        r = self.post(last_name="Uy", referral_source="")
        self.assertEqual(201, r.status_code, "Referral Source stays optional")

    def test_a_typed_referral_source_on_record_survives_an_edit(self):
        old = Child.objects.create(social_worker=self.staff, 
            first_name="Old", last_name="Referral", birth_date=date(2015, 5, 5),
            gender="Male", case_type="Residential Care", case_category="Dependent",
            referral_source="MSWDO San Fernando", current_placement="Bahay Kalinga")
        r = self.client.put(f"/api/children/{old.id}/", {
            "birth_date": "2015-05-05", "gender": "Male", "case_type": "Residential Care",
            "case_category": "Dependent", "referral_source": "MSWDO San Fernando",
            "medical_notes": "Checked.",
        }, format="json")
        self.assertEqual(200, r.status_code, r.data)
        old.refresh_from_db()
        # Whereabouts and health condition did not exist when this record was
        # made: its blanks are not held against an unrelated edit, and what it
        # held is kept.
        self.assertEqual(("MSWDO San Fernando", "Bahay Kalinga"),
                         (old.referral_source, old.current_placement))

    def test_the_custodian_is_typed_in(self):
        """Free text - staff write who the child lives with now (the present
        custodian since 29 Sep 2026; "Previous Custodian" before). Still
        required where the case asks it, and a blank is a blank however many
        spaces it is typed with."""
        r = self.post(custodian_name="Rosa Dela Cruz (maternal aunt), Brgy. Catbangen")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("Rosa Dela Cruz (maternal aunt), Brgy. Catbangen", r.data["custodian_name"])
        r = self.post(last_name="Reyes", custodian_name="Relatives")
        self.assertEqual(201, r.status_code, "any wording is accepted")
        for blank in ("", "   "):
            r = self.post(last_name="Cruz", custodian_name=blank)
            self.assertEqual(400, r.status_code, repr(blank))
            self.assertIn("custodian_name", r.data)
        r = self.post(last_name="Lim", custodian_name="x" * 151)
        self.assertEqual(400, r.status_code)
        self.assertIn("custodian_name", r.data)

    def test_no_date_in_the_future_or_before_the_child_was_born(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        for field in ("date_found", "date_of_placement_to_custodian"):
            for bad in (tomorrow, "2015-12-31"):
                r = self.post(**{field: bad})
                self.assertEqual(400, r.status_code, f"{field}={bad}")
                self.assertIn(field, r.data)


class EditingARecordTest(_Staff):
    def setUp(self):
        super().setUp()
        self.child = Child.objects.get(pk=self.post().data["id"])

    def put(self, **over):
        body = complete(**over)
        for f in ("first_name", "middle_name", "last_name"):
            body.pop(f)
        return self.client.put(f"/api/children/{self.child.id}/", body, format="json")

    def test_an_answer_cannot_be_taken_away(self):
        for field in ("house_number", "street", "birth_status", "case_category",
                      "custodian_name", "date_of_placement_to_custodian"):
            r = self.put(**{field: "" if field != "date_of_placement_to_custodian" else None})
            self.assertEqual(400, r.status_code, field)
            self.assertIn(field, r.data)

    def test_changing_the_case_type_asks_its_questions_again(self):
        r = self.put(case_type="Residential Care", custodian_name="",
                     date_of_placement_to_custodian=None)
        self.assertEqual(400, r.status_code)
        self.assertEqual({"date_of_admission"}, set(r.data))
        r = self.put(case_type="Residential Care", custodian_name="",
                     date_of_placement_to_custodian=None, date_of_admission="2026-03-02")
        self.assertEqual(200, r.status_code, r.data)

    def test_moving_the_birth_date_past_a_recorded_date_is_refused(self):
        """Dates were checked only when they changed, so a birth date moved
        past a date already on the record went through without a word."""
        self.assertEqual(200, self.put(date_found="2016-06-01").status_code)
        r = self.put(birth_date="2017-01-01", date_found="2016-06-01")
        self.assertEqual(400, r.status_code)
        self.assertIn("date_found", r.data)
        # A date already wrong on an older record is not held against an edit
        # that leaves the birth date alone.
        Child.objects.filter(pk=self.child.pk).update(date_found=date(2015, 1, 1))
        r = self.put(date_found="2015-01-01", medical_notes="Seen.")
        self.assertEqual(200, r.status_code, r.data)

    def test_a_date_the_case_does_not_show_is_not_held_against_a_moved_birth_date(self):
        # Foster Care shows the placement; an older record may also hold an
        # admission date, which nothing on screen lets anybody correct.
        Child.objects.filter(pk=self.child.pk).update(date_of_admission=date(2016, 3, 1))
        r = self.put(birth_date="2016-06-01", date_of_admission="2016-03-01")
        self.assertEqual(200, r.status_code, r.data)

    def test_a_record_from_before_the_rule_can_still_be_edited(self):
        """The blanks it already had are not held against an unrelated edit."""
        old = Child.objects.create(social_worker=self.staff, 
            first_name="Old", last_name="Record", birth_date=date(2015, 5, 5),
            gender="Male", case_type="Foster Care", case_category="Dependent")
        r = self.client.put(f"/api/children/{old.id}/", {
            "birth_date": "2015-05-05", "gender": "Male", "case_type": "Foster Care",
            "case_category": "Dependent", "house_number": "", "street": "",
            "medical_notes": "Seen by the nurse.",
        }, format="json")
        self.assertEqual(200, r.status_code, r.data)

    def test_a_retired_value_on_record_survives_an_unrelated_edit(self):
        # "N/A" and "Stepparent" are the values a later list renamed away.
        old = Child.objects.create(social_worker=self.staff, 
            first_name="Old", last_name="Adoption", birth_date=date(2015, 5, 5),
            gender="Male", case_type="Adoption", case_category="Surrendered",
            birth_status="N/A", type_of_adoption="Stepparent")
        r = self.client.put(f"/api/children/{old.id}/", {
            "birth_date": "2015-05-05", "gender": "Male", "case_type": "Adoption",
            "case_category": "Surrendered", "birth_status": "N/A",
            "type_of_adoption": "Stepparent", "education_level": "Grade 5",
        }, format="json")
        self.assertEqual(200, r.status_code, r.data)
        old.refresh_from_db()
        self.assertEqual(("N/A", "Stepparent", "Grade 5"),
                         (old.birth_status, old.type_of_adoption, old.education_level))

    def test_an_older_blank_whereabouts_and_health_are_kept_through_an_unrelated_edit(self):
        old = Child.objects.create(social_worker=self.staff,
            first_name="Old", last_name="Blanks", birth_date=date(2015, 5, 5),
            gender="Male", case_type="Foster Care", case_category="Dependent",
            education_level="Grade 5")
        body = {"birth_date": "2015-05-05", "gender": "Male", "case_type": "Foster Care",
                "case_category": "Dependent", "education_level": "Grade 6"}
        r = self.client.put(f"/api/children/{old.id}/", body, format="json")
        self.assertEqual(200, r.status_code, r.data)
        old.refresh_from_db()
        self.assertEqual(("", "", "Grade 6"),
                         (old.current_placement, old.health_condition, old.education_level))
        # Once it holds an answer, the answer cannot be taken away.
        self.assertEqual(200, self.client.patch(
            f"/api/children/{old.id}/", {"current_placement": "With an aunt",
                                         "health_condition": "Healthy"}, format="json").status_code)
        for field in ("current_placement", "health_condition"):
            r = self.client.patch(f"/api/children/{old.id}/", {field: ""}, format="json")
            self.assertEqual(400, r.status_code, field)
            self.assertIn(field, r.data)

    def test_an_answer_cannot_be_taken_away_whereabouts_and_health(self):
        for field in ("current_placement", "health_condition"):
            r = self.put(**{field: ""})
            self.assertEqual(400, r.status_code, field)
            self.assertIn(field, r.data)

    def test_special_needs_on_an_edit(self):
        r = self.put(health_condition="With special needs")
        self.assertEqual(400, r.status_code)
        self.assertEqual({"special_needs"}, set(r.data))
        r = self.put(health_condition="With special needs", special_needs="Hearing loss")
        self.assertEqual(200, r.status_code, r.data)
        # Back to Healthy drops them.
        r = self.put(health_condition="Healthy", special_needs="Hearing loss")
        self.assertEqual(200, r.status_code, r.data)
        self.child.refresh_from_db()
        self.assertEqual(("Healthy", ""), (self.child.health_condition, self.child.special_needs))

    def test_an_alias_survives_a_change_of_category(self):
        """Hidden by the form when the category is not Without Known Parents,
        never deleted (the same rule as the case-type answers)."""
        self.assertEqual(200, self.put(case_category="Without Known Parents",
                                       alias="Bunso").status_code)
        r = self.put(case_category="Neglected", alias="Bunso")
        self.assertEqual(200, r.status_code, r.data)
        self.child.refresh_from_db()
        self.assertEqual("Bunso", self.child.alias)
        # And an edit that leaves it out does not take it either.
        r = self.client.patch(f"/api/children/{self.child.id}/",
                              {"medical_notes": "Seen."}, format="json")
        self.assertEqual(200, r.status_code, r.data)
        self.child.refresh_from_db()
        self.assertEqual("Bunso", self.child.alias)

    def test_the_name_including_the_middle_name_stays_locked(self):
        r = self.client.patch(f"/api/children/{self.child.id}/",
                              {"middle_name": "Reyes"}, format="json")
        self.assertEqual(400, r.status_code)
        self.assertIn("middle_name", r.data)


class RenamingMigrationTest(TestCase):
    """children 0021 moves the renamed values; the retired ones stay put."""

    def test_forwards(self):
        from importlib import import_module
        from django.apps import apps
        migration = import_module("children.migrations.0021_rename_orphan_and_na")
        kept = Child.objects.create(fullname="Kept", birth_status="Child", type_of_adoption="SIBRA")
        guess = Child.objects.create(fullname="Guess", type_of_adoption="Relative")
        moved = Child.objects.create(fullname="Moved", case_category="Orphan", birth_status="N/A",
                                     type_of_adoption="Stepparent")
        migration.forwards(apps, None)
        for child in (moved, kept, guess):
            child.refresh_from_db()
        self.assertEqual(("Orphaned", "Unknown", "Step-parent"),
                         (moved.case_category, moved.birth_status, moved.type_of_adoption))
        self.assertEqual(("Child", "SIBRA"), (kept.birth_status, kept.type_of_adoption))
        self.assertEqual("Relative", guess.type_of_adoption)


class AnOlderDemoFixtureStillLoadsTest(TestCase):
    def test_upgraded_on_the_way_in(self):
        psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        path = Path(tempfile.mkdtemp()) / "old.json"
        path.write_text(json.dumps([{
            "model": "children.child", "pk": 950,
            "fields": {"fullname": "Ana R. Cruz", "first_name": "Ana", "middle_initial": "R",
                       "last_name": "Cruz", "case_category": "Orphan", "birth_status": "N/A",
                       "assigned_psychologist": psy.pk,
                       "created_at": "2026-08-01T00:00:00Z",
                       "updated_at": "2026-08-01T00:00:00Z"},
        }]), encoding="utf-8")
        out = StringIO()
        call_command("import_demo_data", fixture=str(path), stdout=out)
        child = Child.objects.get(pk=950)
        self.assertEqual(("R", "Orphaned", "Unknown"),
                         (child.middle_name, child.case_category, child.birth_status))
        self.assertIn("upgraded", out.getvalue())
        # The fixture predates both questions; the import answers them, or the
        # record would be one the form refuses to create.
        self.assertIn(child.health_condition, intake.HEALTH_CONDITIONS)
        self.assertTrue(child.current_placement.strip())


MEDIA = Path(tempfile.gettempdir()) / "intake-seeder-media"


@override_settings(DEBUG=True, MEDIA_ROOT=str(MEDIA))
class TheSeederFillsTheFormTest(TestCase):
    """Seeded data must satisfy the rules the endpoint enforces (CLAUDE.md)."""

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_every_seeded_child_is_a_record_the_form_would_save(self):
        province = Province.objects.create(psgc_code="013300000", name="La Union")
        town = Municipality.objects.create(psgc_code="013314000", name="San Fernando",
                                           province=province)
        Barangay.objects.create(psgc_code="013314001", name="Catbangen", municipality=town)
        call_command("seed_demo_data", children=18, stdout=StringIO())
        children = list(Child.objects.all())
        self.assertEqual(18, len(children))
        self.assertTrue(any(c.health_condition == intake.SPECIAL_NEEDS for c in children),
                        "the demo should show a child with special needs")
        for child in children:
            for field in intake.required_fields(child.case_type, child.type_of_adoption,
                                               child.health_condition):
                self.assertTrue(str(getattr(child, field) or "").strip(),
                                f"{child.fullname} ({child.case_type}) has no {field}")
            self.assertIn(child.case_category, intake.CATEGORY_OPTIONS[child.case_type])
            self.assertIn(child.birth_status, [c for c, _ in Child.BIRTH_STATUS_CHOICES])
            self.assertIn(child.health_condition, intake.HEALTH_CONDITIONS)
            if child.health_condition == intake.SPECIAL_NEEDS:
                self.assertTrue(child.special_needs.strip(), child.fullname)
            if child.type_of_adoption:
                self.assertIn(child.type_of_adoption,
                              [c for c, _ in Child.TYPE_OF_ADOPTION_CHOICES])
            other = ({intake.ADMISSION, intake.PLACEMENT}
                     - {intake.date_field_for(child.case_type, child.type_of_adoption)})
            for field in other:
                self.assertIsNone(getattr(child, field), f"{child.fullname} has both dates")


class TheAddressHoldsTogetherTest(_Staff):
    """A slow list in the form offered one province's municipalities under
    another, and nothing on the server said no: Ilocos Sur / Adams / Alibago,
    three provinces, saved (29 Sep 2026)."""

    def setUp(self):
        super().setUp()
        from locations.models import Barangay, Municipality, Province
        self.norte = Province.objects.create(psgc_code="0128", name="Ilocos Norte")
        self.sur = Province.objects.create(psgc_code="0129", name="Ilocos Sur")
        self.adams = Municipality.objects.create(psgc_code="012801", name="Adams", province=self.norte)
        self.bantay = Municipality.objects.create(psgc_code="012903", name="Bantay", province=self.sur)
        self.pob = Barangay.objects.create(psgc_code="012801001", name="Adams (Pob.)", municipality=self.adams)
        self.bulag = Barangay.objects.create(psgc_code="012903002", name="Bulag", municipality=self.bantay)

    @staticmethod
    def address(province, municipality, barangay):
        return {"psgc_province": province.psgc_code, "province": province.name,
                "psgc_municipality": municipality.psgc_code, "municipality": municipality.name,
                "psgc_barangay": barangay.psgc_code, "barangay": barangay.name}

    def test_an_address_that_holds_together_saves(self):
        r = self.post(**self.address(self.norte, self.adams, self.pob))
        self.assertEqual(201, r.status_code, r.data)

    def test_a_municipality_from_another_province_is_refused(self):
        r = self.post(**self.address(self.sur, self.adams, self.pob))
        self.assertEqual(400, r.status_code)
        self.assertIn("Adams is not in Ilocos Sur", str(r.data["municipality"]))

    def test_a_barangay_from_another_municipality_is_refused(self):
        r = self.post(**self.address(self.sur, self.bantay, self.pob))
        self.assertEqual(400, r.status_code)
        self.assertIn("Adams (Pob.) is not in Bantay", str(r.data["barangay"]))

    def test_an_unknown_code_is_refused(self):
        r = self.post(**{**self.address(self.norte, self.adams, self.pob), "psgc_barangay": "999999999"})
        self.assertEqual(400, r.status_code)
        self.assertIn("barangay", r.data)

    def test_the_names_are_the_codes_own(self):
        r = self.post(**{**self.address(self.norte, self.adams, self.pob),
                         "province": "Ilocos N.", "municipality": "adams", "barangay": "Poblacion"})
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual(("Ilocos Norte", "Adams", "Adams (Pob.)"),
                         (r.data["province"], r.data["municipality"], r.data["barangay"]))

    def test_an_address_saved_before_the_check_survives_an_unrelated_edit(self):
        child = Child.objects.get(pk=self.post().data["id"])
        Child.objects.filter(pk=child.pk).update(**self.address(self.sur, self.adams, self.pob))
        body = complete(**self.address(self.sur, self.adams, self.pob), medical_notes="Seen.")
        for f in ("first_name", "middle_name", "last_name"):
            body.pop(f)
        r = self.client.put(f"/api/children/{child.id}/", body, format="json")
        self.assertEqual(200, r.status_code, r.data)
