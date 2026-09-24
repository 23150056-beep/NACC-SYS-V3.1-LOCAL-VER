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
        self.assertEqual(["Marital", "Non-Marital", "Unknown"], _js_array(self.js, "BIRTH_STATUSES"))
        self.assertEqual(_js_array(self.js, "BIRTH_STATUSES"),
                         [c for c, _ in Child.BIRTH_STATUS_CHOICES])
        adoption = _js_array(self.js, "TYPES_OF_ADOPTION")
        self.assertEqual(adoption, [c for c, _ in Child.TYPE_OF_ADOPTION_CHOICES])
        self.assertIn("Relative (Without 2-yr custody)", adoption)
        self.assertEqual(adoption.index("Domestic Relative") + 1,
                         adoption.index("Relative (Without 2-yr custody)"))
        for retired in ("SIBRA", "ICA Relative"):
            self.assertNotIn(retired, adoption)

    def test_what_is_always_required(self):
        self.assertEqual(intake.ALWAYS_REQUIRED, _js_array(self.js, "ALWAYS_REQUIRED"))

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
        self.client.force_authenticate(make_user("intake@t.ph", Role.STAFF))

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
        r = self.post(case_type="Residential Care", surrendered_by="",
                      date_of_placement_to_custodian=None)
        self.assertEqual(400, r.status_code)
        self.assertEqual({"date_of_admission"}, set(r.data))
        r = self.post(case_type="Residential Care", surrendered_by="",
                      date_of_placement_to_custodian=None, date_of_admission="2026-03-01")
        self.assertEqual(201, r.status_code, r.data)

    def test_a_category_the_case_type_does_not_offer_is_refused(self):
        r = self.post(case_type="Independent Living", case_category="Surrendered",
                      surrendered_by="", date_of_placement_to_custodian=None,
                      date_of_admission="2026-03-01")
        self.assertEqual(400, r.status_code)
        self.assertIn("case_category", r.data)

    def test_renamed_and_retired_values_cannot_be_picked(self):
        for field, value in (("case_category", "Orphan"), ("birth_status", "N/A"),
                             ("birth_status", "Child")):
            r = self.post(**{field: value})
            self.assertEqual(400, r.status_code, value)
            self.assertIn(field, r.data)
        for retired in ("SIBRA", "ICA Relative"):
            r = self.post(case_type="Adoption", type_of_adoption=retired)
            self.assertEqual(400, r.status_code, retired)
            self.assertIn("type_of_adoption", r.data)

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
        old = Child.objects.create(
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
        # Current Whereabouts left the form, and what it held is kept.
        self.assertEqual(("MSWDO San Fernando", "Bahay Kalinga"),
                         (old.referral_source, old.current_placement))

    def test_the_previous_custodian_is_typed_in(self):
        """Free text since 24 Sep 2026 - staff write who actually had the
        child. Still required where the case asks it, and a blank is a blank
        however many spaces it is typed with."""
        r = self.post(surrendered_by="Rosa Dela Cruz (maternal aunt), Brgy. Catbangen")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("Rosa Dela Cruz (maternal aunt), Brgy. Catbangen", r.data["surrendered_by"])
        r = self.post(last_name="Reyes", surrendered_by="Relatives")
        self.assertEqual(201, r.status_code, "a value from the old list is still fine")
        for blank in ("", "   "):
            r = self.post(last_name="Cruz", surrendered_by=blank)
            self.assertEqual(400, r.status_code, repr(blank))
            self.assertIn("surrendered_by", r.data)
        r = self.post(last_name="Lim", surrendered_by="x" * 151)
        self.assertEqual(400, r.status_code)
        self.assertIn("surrendered_by", r.data)

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
                      "surrendered_by", "date_of_placement_to_custodian"):
            r = self.put(**{field: "" if field != "date_of_placement_to_custodian" else None})
            self.assertEqual(400, r.status_code, field)
            self.assertIn(field, r.data)

    def test_changing_the_case_type_asks_its_questions_again(self):
        r = self.put(case_type="Residential Care", surrendered_by="",
                     date_of_placement_to_custodian=None)
        self.assertEqual(400, r.status_code)
        self.assertEqual({"date_of_admission"}, set(r.data))
        r = self.put(case_type="Residential Care", surrendered_by="",
                     date_of_placement_to_custodian=None, date_of_admission="2026-03-02")
        self.assertEqual(200, r.status_code, r.data)

    def test_a_record_from_before_the_rule_can_still_be_edited(self):
        """The blanks it already had are not held against an unrelated edit."""
        old = Child.objects.create(
            first_name="Old", last_name="Record", birth_date=date(2015, 5, 5),
            gender="Male", case_type="Foster Care", case_category="Dependent")
        r = self.client.put(f"/api/children/{old.id}/", {
            "birth_date": "2015-05-05", "gender": "Male", "case_type": "Foster Care",
            "case_category": "Dependent", "house_number": "", "street": "",
            "medical_notes": "Seen by the nurse.",
        }, format="json")
        self.assertEqual(200, r.status_code, r.data)

    def test_a_retired_value_on_record_survives_an_unrelated_edit(self):
        old = Child.objects.create(
            first_name="Old", last_name="Adoption", birth_date=date(2015, 5, 5),
            gender="Male", case_type="Adoption", case_category="Surrendered",
            birth_status="Child", type_of_adoption="SIBRA")
        r = self.client.put(f"/api/children/{old.id}/", {
            "birth_date": "2015-05-05", "gender": "Male", "case_type": "Adoption",
            "case_category": "Surrendered", "birth_status": "Child",
            "type_of_adoption": "SIBRA", "education_level": "Grade 5",
        }, format="json")
        self.assertEqual(200, r.status_code, r.data)
        old.refresh_from_db()
        self.assertEqual(("Child", "SIBRA", "Grade 5"),
                         (old.birth_status, old.type_of_adoption, old.education_level))

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
        for child in children:
            for field in intake.required_fields(child.case_type, child.type_of_adoption):
                self.assertTrue(str(getattr(child, field) or "").strip(),
                                f"{child.fullname} ({child.case_type}) has no {field}")
            self.assertIn(child.case_category, intake.CATEGORY_OPTIONS[child.case_type])
            self.assertIn(child.birth_status, [c for c, _ in Child.BIRTH_STATUS_CHOICES])
            if child.type_of_adoption:
                self.assertIn(child.type_of_adoption,
                              [c for c, _ in Child.TYPE_OF_ADOPTION_CHOICES])
            other = ({intake.ADMISSION, intake.PLACEMENT}
                     - {intake.date_field_for(child.case_type, child.type_of_adoption)})
            for field in other:
                self.assertIsNone(getattr(child, field), f"{child.fullname} has both dates")
