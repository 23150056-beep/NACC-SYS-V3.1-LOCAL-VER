import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from accounts.models import Role, User
from children.models import Child
from locations.models import Barangay, Municipality, Province


class SeedTest(APITestCase):
    def test_seed_loads_region_one(self):
        call_command("seed_psgc", stdout=StringIO())
        self.assertEqual(Province.objects.count(), 4)
        self.assertEqual(Municipality.objects.count(), 125)
        self.assertEqual(Barangay.objects.count(), 3265)

    def test_seeding_twice_does_not_duplicate(self):
        """Re-running against a newer PSGC release has to update in place —
        duplicating would orphan every child record pointing at a code."""
        call_command("seed_psgc", stdout=StringIO())
        call_command("seed_psgc", stdout=StringIO())
        self.assertEqual(Province.objects.count(), 4)
        self.assertEqual(Barangay.objects.count(), 3265)

    def test_la_union_has_all_twenty_lgus(self):
        call_command("seed_psgc", stdout=StringIO())
        la_union = Province.objects.get(name="La Union")
        self.assertEqual(la_union.municipalities.count(), 20)


BUNDLED = Path(__file__).resolve().parents[1] / "data" / "psgc_region1.json"


def _seed(*args):
    out = StringIO()
    call_command("seed_psgc", *args, stdout=out)
    return out.getvalue()


def _statements(queries):
    """First word of each statement a block ran, counted."""
    counts = {}
    for q in queries:
        word = q["sql"].split(None, 1)[0].upper()
        counts[word] = counts.get(word, 0) + 1
    return counts


class SeedWritesOnlyWhatChangedTest(APITestCase):
    """seed_psgc runs on every start-up of the hosted demo, with the database a
    network round trip away. It used to ask update_or_create about each of the
    3,394 places - 13,578 statements to learn that nothing had changed - so a
    waking server spent most of its wait on it. These hold the new shape."""

    def setUp(self):
        self.payload = json.loads(BUNDLED.read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _file(self, payload, name="release.json"):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def _newer_release(self):
        """The bundled release with a rename, a move and some additions."""
        p = json.loads(json.dumps(self.payload))
        p["psgc_release"] = "a newer release"
        # A municipality renamed, as when one is promoted to a city.
        p["municipalities"][0]["name"] = "City Of " + p["municipalities"][0]["name"]
        # A barangay renamed, and one moved to another municipality.
        p["barangays"][1]["name"] = p["barangays"][1]["name"] + " (Renamed)"
        p["barangays"][0]["municipality"] = p["municipalities"][1]["code"]
        # A whole new chain: its parents are made in this run and its children
        # must be able to point at them. Plus one more under an old parent.
        p["provinces"].append({"code": "9901", "name": "Test Province"})
        p["municipalities"].append(
            {"code": "990101", "name": "Test Town", "province": "9901"})
        p["barangays"].append(
            {"code": "990101001", "name": "Test Barangay", "municipality": "990101"})
        p["barangays"].append(
            {"code": "012801999", "name": "Test Purok",
             "municipality": p["municipalities"][0]["code"]})
        return p

    def test_a_first_run_creates_every_row_in_the_file(self):
        out = _seed()
        self.assertEqual(Province.objects.count(), len(self.payload["provinces"]))
        self.assertEqual(Municipality.objects.count(), len(self.payload["municipalities"]))
        self.assertEqual(Barangay.objects.count(), len(self.payload["barangays"]))
        self.assertEqual(
            (Province.objects.count(), Municipality.objects.count(), Barangay.objects.count()),
            (4, 125, 3265))
        # Each level points at the parent the file names - the parents were
        # made in the same run.
        for row in self.payload["municipalities"]:
            self.assertEqual(
                Municipality.objects.get(psgc_code=row["code"]).province.psgc_code,
                row["province"])
        by_muni = dict(Municipality.objects.values_list("psgc_code", "pk"))
        for row in self.payload["barangays"]:
            self.assertEqual(
                Barangay.objects.get(psgc_code=row["code"]).municipality_id,
                by_muni[row["municipality"]])
        self.assertIn("(4 new, 0 updated)", out)
        self.assertIn("(125 new, 0 updated)", out)
        self.assertIn("(3265 new, 0 updated)", out)

    def test_a_second_run_writes_nothing_and_uses_a_fixed_number_of_queries(self):
        _seed()
        keys = list(Barangay.objects.order_by("psgc_code").values_list("psgc_code", "pk"))
        # A savepoint, one read per table, and the release of the savepoint.
        # Nothing here depends on how many places there are.
        with self.assertNumQueries(5) as ctx:
            out = _seed()
        self.assertEqual(
            set(_statements(ctx.captured_queries)) & {"INSERT", "UPDATE", "DELETE"}, set())
        self.assertEqual(Province.objects.count(), 4)
        self.assertEqual(Municipality.objects.count(), 125)
        self.assertEqual(Barangay.objects.count(), 3265)
        self.assertEqual(
            keys, list(Barangay.objects.order_by("psgc_code").values_list("psgc_code", "pk")))
        self.assertEqual(out.count("(0 new, 0 updated)"), 3)

    def test_the_cost_of_a_repeat_run_does_not_grow_with_the_rows(self):
        small = {"psgc_release": "tiny",
                 "provinces": [{"code": "0128", "name": "Ilocos Norte"}],
                 "municipalities": [{"code": "012801", "name": "Adams", "province": "0128"}],
                 "barangays": [{"code": "012801001", "name": "Adams (Pob.)",
                                "municipality": "012801"},
                               {"code": "012801002", "name": "Bacsil",
                                "municipality": "012801"}]}
        small_file = self._file(small, "small.json")
        _seed("--file", small_file)
        with CaptureQueriesContext(connection) as few:
            _seed("--file", small_file)
        Barangay.objects.all().delete()
        Municipality.objects.all().delete()
        Province.objects.all().delete()
        _seed()
        with CaptureQueriesContext(connection) as many:
            _seed()
        self.assertEqual(len(few), len(many))

    def test_a_renamed_place_and_a_moved_barangay_are_updated_in_place(self):
        _seed()
        muni_first = Municipality.objects.get(psgc_code=self.payload["municipalities"][0]["code"])
        brgy_moved = Barangay.objects.get(psgc_code=self.payload["barangays"][0]["code"])
        brgy_renamed = Barangay.objects.get(psgc_code=self.payload["barangays"][1]["code"])
        untouched = Barangay.objects.get(psgc_code=self.payload["barangays"][5]["code"])
        keys = (muni_first.pk, brgy_moved.pk, brgy_renamed.pk, untouched.pk)
        new_home = Municipality.objects.get(psgc_code=self.payload["municipalities"][1]["code"])

        _seed("--file", self._file(self._newer_release()))

        muni_first.refresh_from_db()
        brgy_moved.refresh_from_db()
        brgy_renamed.refresh_from_db()
        untouched.refresh_from_db()
        # Same rows, same primary keys - a child pointing at the code keeps
        # pointing at the same place.
        self.assertEqual(
            keys, (muni_first.pk, brgy_moved.pk, brgy_renamed.pk, untouched.pk))
        self.assertTrue(muni_first.name.startswith("City Of "))
        self.assertTrue(brgy_renamed.name.endswith(" (Renamed)"))
        self.assertEqual(brgy_moved.municipality_id, new_home.pk)
        self.assertEqual(
            untouched.name, self.payload["barangays"][5]["name"])
        self.assertEqual(
            untouched.municipality.psgc_code, self.payload["barangays"][5]["municipality"])
        # Nothing was duplicated: the three renames and moves added no rows,
        # the additions added theirs.
        self.assertEqual(Province.objects.count(), 5)
        self.assertEqual(Municipality.objects.count(), 126)
        self.assertEqual(Barangay.objects.count(), 3267)

    def test_places_added_under_a_parent_made_in_the_same_run_are_linked(self):
        _seed()
        _seed("--file", self._file(self._newer_release()))
        province = Province.objects.get(psgc_code="9901")
        town = Municipality.objects.get(psgc_code="990101")
        barangay = Barangay.objects.get(psgc_code="990101001")
        self.assertEqual(town.province_id, province.pk)
        self.assertEqual(barangay.municipality_id, town.pk)
        # And under a parent that was already there.
        purok = Barangay.objects.get(psgc_code="012801999")
        self.assertEqual(
            purok.municipality.psgc_code, self.payload["municipalities"][0]["code"])

    def test_only_the_changed_rows_are_written(self):
        _seed()
        with CaptureQueriesContext(connection) as ctx:
            out = _seed("--file", self._file(self._newer_release()))
        counts = _statements(ctx.captured_queries)
        # One batch each for the municipality rename, the two barangay changes
        # and nothing for provinces; one batch each of new rows.
        self.assertEqual(counts.get("UPDATE"), 2)
        self.assertEqual(counts.get("INSERT"), 3)
        self.assertLess(len(ctx), 20)
        self.assertRegex(out, r"provinces:\s+5 \(1 new, 0 updated\)")
        self.assertRegex(out, r"municipalities:\s+126 \(1 new, 1 updated\)")
        self.assertRegex(out, r"barangays:\s+3267 \(2 new, 2 updated\)")
        self.assertIn("Release: a newer release", out)

    def test_a_code_listed_twice_in_a_file_takes_the_last_row(self):
        p = json.loads(json.dumps(self.payload))
        p["provinces"].append({"code": "0128", "name": "Ilocos Norte (Again)"})
        _seed("--file", self._file(p))
        self.assertEqual(Province.objects.count(), 4)
        self.assertEqual(
            Province.objects.get(psgc_code="0128").name, "Ilocos Norte (Again)")

    def test_a_failed_run_leaves_the_places_as_they_were(self):
        _seed()
        p = self._newer_release()
        p["barangays"].append(
            {"code": "990101777", "name": "Orphan", "municipality": "no-such-code"})
        with self.assertRaises(KeyError):
            _seed("--file", self._file(p))
        self.assertEqual(Province.objects.count(), 4)
        self.assertEqual(Barangay.objects.count(), 3265)
        self.assertFalse(Province.objects.filter(psgc_code="9901").exists())
        first = self.payload["municipalities"][0]
        self.assertEqual(Municipality.objects.get(psgc_code=first["code"]).name, first["name"])


class LocationApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_psgc", stdout=StringIO())

    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        User.objects.create_user(email="s@racco1.gov.ph", username="s",
                                 password="pass1234", role=role)
        token = self.client.post("/api/auth/login/", {
            "email": "s@racco1.gov.ph", "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_provinces_listed(self):
        resp = self.client.get("/api/locations/provinces/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({p["name"] for p in resp.data},
                         {"Ilocos Norte", "Ilocos Sur", "La Union", "Pangasinan"})

    def test_municipalities_filter_by_province(self):
        la_union = Province.objects.get(name="La Union")
        resp = self.client.get("/api/locations/municipalities/",
                               {"province": la_union.psgc_code})
        self.assertEqual(len(resp.data), 20)
        # PSGC's own name, not the informal one. The hand-kept list this
        # replaces said "San Fernando City"; the standard says this. Keeping
        # the official spelling is the point of using the standard at all.
        self.assertIn("City Of San Fernando (Capital)", [m["name"] for m in resp.data])

    def test_barangays_require_a_municipality(self):
        """3,265 rows is not a list anyone picks from, and not what the form
        asks for — an unfiltered request returns nothing rather than all."""
        resp = self.client.get("/api/locations/barangays/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 0)

    def test_barangays_of_a_municipality(self):
        agoo = Municipality.objects.get(name="Agoo")
        resp = self.client.get("/api/locations/barangays/",
                               {"municipality": agoo.psgc_code})
        self.assertGreater(len(resp.data), 0)
        self.assertTrue(all("psgc_code" in b and "name" in b for b in resp.data))

    def test_anonymous_is_refused(self):
        self.client.credentials()
        self.assertEqual(self.client.get("/api/locations/provinces/").status_code, 401)

    def test_the_api_is_read_only(self):
        resp = self.client.post("/api/locations/provinces/", {"name": "Nowhere"})
        self.assertIn(resp.status_code, (403, 405))


class BackfillTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_psgc", stdout=StringIO())

    def test_matching_text_gets_codes(self):
        child = Child.objects.create(fullname="Ana", province="La Union",
                                     municipality="Agoo", barangay="Ambitacay")
        call_command("backfill_psgc", "--apply", stdout=StringIO())
        child.refresh_from_db()
        self.assertTrue(child.psgc_province)
        self.assertTrue(child.psgc_municipality)
        self.assertTrue(child.psgc_barangay)

    def test_case_and_spacing_do_not_block_a_match(self):
        child = Child.objects.create(fullname="Ben", province="  la union ",
                                     municipality="AGOO")
        call_command("backfill_psgc", "--apply", stdout=StringIO())
        child.refresh_from_db()
        self.assertTrue(child.psgc_municipality)

    def test_unmatched_text_is_kept_not_wiped(self):
        child = Child.objects.create(fullname="Cid", province="Atlantis",
                                     municipality="Nowhere")
        call_command("backfill_psgc", "--apply", stdout=StringIO())
        child.refresh_from_db()
        self.assertEqual(child.province, "Atlantis")
        self.assertEqual(child.municipality, "Nowhere")
        self.assertEqual(child.psgc_province, "")

    def test_dry_run_writes_nothing(self):
        child = Child.objects.create(fullname="Dee", province="La Union", municipality="Agoo")
        call_command("backfill_psgc", stdout=StringIO())
        child.refresh_from_db()
        self.assertEqual(child.psgc_province, "")

    def test_a_code_someone_picked_is_not_recomputed(self):
        """The entrypoint runs this on every deploy, so it must never undo a
        choice made in the form. A person picking an address is better evidence
        than a text match, even when the text was later edited."""
        child = Child.objects.create(
            fullname="Fay", province="La Union", municipality="Agoo",
            psgc_province="0133", psgc_municipality="013301",
            psgc_barangay="013301001")
        call_command("backfill_psgc", "--apply", stdout=StringIO())
        child.refresh_from_db()
        self.assertEqual(child.psgc_barangay, "013301001")

    def test_recheck_opts_back_in(self):
        child = Child.objects.create(
            fullname="Gil", province="Atlantis", psgc_province="9999")
        call_command("backfill_psgc", "--apply", "--recheck", stdout=StringIO())
        child.refresh_from_db()
        self.assertEqual(child.psgc_province, "")

    def test_running_twice_is_a_no_op(self):
        child = Child.objects.create(fullname="Hal", province="La Union", municipality="Agoo")
        call_command("backfill_psgc", "--apply", stdout=StringIO())
        child.refresh_from_db()
        first = child.psgc_municipality
        call_command("backfill_psgc", "--apply", stdout=StringIO())
        child.refresh_from_db()
        self.assertEqual(child.psgc_municipality, first)

    def test_report_names_the_records_needing_a_human(self):
        Child.objects.create(fullname="Eve", province="Atlantis")
        out = StringIO()
        call_command("backfill_psgc", stdout=out)
        self.assertIn("Eve", out.getvalue())
