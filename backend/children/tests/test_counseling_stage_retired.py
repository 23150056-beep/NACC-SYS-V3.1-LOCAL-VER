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


class TheStageIsGoneTest(TestCase):
    def test_no_counseling_choice_remains(self):
        self.assertFalse(hasattr(Child, "STAGE_COUNSELING"))
        self.assertEqual({"pre_assessment", "terminated"},
                         {v for v, _ in Child.CASE_STATUS_CHOICES})
