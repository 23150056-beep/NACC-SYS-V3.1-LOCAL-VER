"""The wizard's status labels live twice, in Child and in caseData.js."""
import re

from django.test import SimpleTestCase

from children.models import Child
from children.tests.test_intake import CASE_DATA_JS, _js_array


class PreAssessmentLabelsTest(SimpleTestCase):
    def setUp(self):
        self.js = CASE_DATA_JS.read_text(encoding="utf-8")

    def test_the_status_list_is_the_models_in_order(self):
        self.assertEqual(_js_array(self.js, "PA_STATUSES"), [
            Child.PA_NO_CONSENT, Child.PA_NOT_YET, Child.PA_IN_PROGRESS, Child.PA_COMPLETED])

    def test_the_stored_status_is_the_models(self):
        from clinical.models import PreAssessment
        match = re.search(r"export const PA_ROW_COMPLETED\s*=\s*'([^']+)'", self.js)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), PreAssessment.COMPLETED)

    def test_the_named_constants_are_the_models(self):
        for name, value in (("PA_STATUS_IN_PROGRESS", Child.PA_IN_PROGRESS),
                            ("PA_STATUS_COMPLETED", Child.PA_COMPLETED)):
            match = re.search(r"export const " + name + r"\s*=\s*'([^']+)'", self.js)
            self.assertIsNotNone(match, name)
            self.assertEqual(match.group(1), value)
