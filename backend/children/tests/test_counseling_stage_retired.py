"""The Counseling case stage was retired on 2 Nov 2026."""
from importlib import import_module

from django.test import SimpleTestCase, TestCase

from children.models import Child


class OlderFixtureTest(SimpleTestCase):
    def test_a_counseling_stage_in_an_older_fixture_becomes_pre_assessment(self):
        upgrade = import_module("children.management.commands.import_demo_data").upgrade_rows
        rows = [{"model": "children.child", "fields": {"case_status": "counseling"}},
                {"model": "children.child", "fields": {"case_status": "terminated"}}]
        self.assertTrue(upgrade(rows))
        self.assertEqual(["pre_assessment", "terminated"],
                         [r["fields"]["case_status"] for r in rows])


class RenamedClosureReasonsTest(SimpleTestCase):
    def test_old_reason_wording_in_an_older_fixture_is_upgraded(self):
        upgrade = import_module("children.management.commands.import_demo_data").upgrade_rows
        old = ["Counseling completed", "Favorable pre-assessment, no counseling needed",
               "Counseling discontinued", "Other"]
        rows = [{"model": "children.terminationrecord", "fields": {"reason_category": r}}
                for r in old]
        self.assertTrue(upgrade(rows))
        self.assertEqual(["Sessions completed", "Favorable pre-assessment, no sessions needed",
                          "Sessions discontinued", "Other"],
                         [r["fields"]["reason_category"] for r in rows])
        self.assertFalse(upgrade(rows))


class TheStageIsGoneTest(TestCase):
    def test_no_counseling_choice_remains(self):
        self.assertFalse(hasattr(Child, "STAGE_COUNSELING"))
        self.assertEqual({"pre_assessment", "terminated"},
                         {v for v, _ in Child.CASE_STATUS_CHOICES})
