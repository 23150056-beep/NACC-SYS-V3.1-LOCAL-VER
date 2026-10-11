"""A long report is not a slow page."""
from django.db import connection
from django.test.utils import CaptureQueriesContext

from case_study.models import CaseStudySection
from case_study.sections import SCSR_SECTIONS
from case_study.tests.base import CaseStudyTestCase


class QueryCountTest(CaseStudyTestCase):
    def test_reading_does_not_cost_a_query_per_section(self):
        """Every reader gets the same number of queries whether no box is
        filled or all of them are."""
        study = self.start()

        def queries(user):
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(200, self.as_user(user).get(self.url()).status_code)
            return len(captured)

        before = {u.pk: queries(u) for u in (self.sw, self.psy, self.isa)}
        for entry in SCSR_SECTIONS:
            CaseStudySection.objects.create(
                case_study=study, key=entry["key"], value=None, updated_by=self.sw)
        after = {u.pk: queries(u) for u in (self.sw, self.psy, self.isa)}
        self.assertEqual(before, after)
        self.assertLess(max(after.values()), 20)

    def test_saving_a_box_is_a_handful_of_queries(self):
        self.start()
        self.save_section("a2_sources", ["The child"])
        with CaptureQueriesContext(connection) as captured:
            res = self.save_section("a2_sources", ["The child", "Grandmother"], version=1)
        self.assertEqual(200, res.status_code)
        self.assertLess(len(captured), 25)
