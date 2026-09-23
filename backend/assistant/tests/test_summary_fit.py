"""Long reports are fitted to what the model reads, and the draft says so.

The whole extracted text - up to 200,000 characters - used to follow the
summary instructions, into a model whose window is a few thousand tokens.
What the runtime drops to make room was never this code's choice.
"""
import shutil
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from assistant import prompts, services
from assistant.tests.test_summaries import SummaryTestBase
from clinical.demo_docx import build_docx
from clinical.models import PsychologicalReport

BUDGET = prompts.SUMMARY_BUDGET_CHARS


def report(*sections):
    return "\n".join(f"## {heading}\n{body}" for heading, body in sections)


LONG_NOTES = "Session. The child talked about school and home at length.\n" * 250


class FitTest(SimpleTestCase):
    def test_a_report_that_fits_is_sent_whole(self):
        text = report(("Background", "In care since April."), ("Recommendations", "Continue."))
        self.assertEqual((text, None), prompts.fit_document(text))

    def test_a_long_report_keeps_what_a_summary_needs(self):
        text = report(("Identifying Information", "Name: Maria Santos"),
                      ("Reason for Referral", "Referred for counselling."),
                      ("Background Information", "In care since April."),
                      ("Session Notes", LONG_NOTES),
                      ("Recommendations", "Continue weekly sessions."))
        fitted, note = prompts.fit_document(text)
        self.assertLessEqual(len(fitted), BUDGET)
        # The recommendations come last in the file - the part a cut from the
        # end would have lost first.
        self.assertIn("Continue weekly sessions.", fitted)
        self.assertIn("Referred for counselling.", fitted)
        # Still in the document's own order.
        self.assertLess(fitted.index("## Reason for Referral"), fitted.index("## Recommendations"))
        self.assertEqual("This report is too long to read in one go. Only the first part "
                         "of Session Notes was read.", note)

    def test_what_is_left_out_is_named(self):
        text = report(("Recommendations", "Continue."), ("Session Notes", LONG_NOTES),
                      ("Annex", LONG_NOTES))
        fitted, note = prompts.fit_document(text)
        self.assertIn("Not read: Annex.", note)
        self.assertNotIn("## Annex", fitted)

    def test_without_headings_the_beginning_and_the_end_are_read(self):
        text = "Opening line about the child.\n" + LONG_NOTES + "Closing recommendation line."
        fitted, note = prompts.fit_document(text)
        self.assertLessEqual(len(fitted), BUDGET + 10)
        self.assertTrue(fitted.startswith("Opening line about the child."))
        self.assertTrue(fitted.endswith("Closing recommendation line."))
        self.assertIn("[...]", fitted)
        self.assertIn("Only its beginning and its end were read.", note)

    def test_the_instructions_still_lead_the_prompt(self):
        # The static prefix is what keeps the model's cache warm - unchanged.
        prompt = prompts.build_summary_prompt(report(("Notes", LONG_NOTES)) * 3, "report")
        self.assertTrue(prompt.startswith(prompts.SUMMARY_INSTRUCTIONS))
        self.assertLess(len(prompt), BUDGET + len(prompts.SUMMARY_INSTRUCTIONS) + 40)


MEDIA = Path(settings.BASE_DIR) / "test-media-fit"


@override_settings(MEDIA_ROOT=str(MEDIA))
class SummaryViewTest(SummaryTestBase):
    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def summarise(self, doc):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Draft.") as gen:
            res = self.client.post(f"/api/assistant/summarize-report/{doc.id}/")
        return res, gen

    def test_a_long_report_is_sent_fitted_and_the_answer_says_what_was_read(self):
        self.report.extracted_text = report(("Background", "In care."),
                                            ("Session Notes", LONG_NOTES),
                                            ("Recommendations", "Continue."))
        self.report.save()
        res, gen = self.summarise(self.report)
        self.assertEqual(200, res.status_code, res.data)
        self.assertIn("Only the first part of Session Notes was read.", res.data["coverage"])
        sent = gen.call_args.args[0] if gen.call_args.args else gen.call_args.kwargs["prompt"]
        self.assertLess(len(sent), BUDGET + len(prompts.SUMMARY_INSTRUCTIONS) + 40)

    def test_a_short_one_says_nothing_about_coverage(self):
        res, _ = self.summarise(self.report)
        self.assertIsNone(res.data["coverage"])

    def test_a_word_report_uploaded_before_it_could_be_read_can_be_summarised(self):
        old = PsychologicalReport(child=self.child, author=self.psy,
                                  original_filename="old.docx")
        old.file.save("old.docx", ContentFile(build_docx(
            [("heading", "Background"), ("para", "In care since April.")])), save=True)
        res, gen = self.summarise(old)
        self.assertEqual(200, res.status_code, res.data)
        sent = gen.call_args.args[0] if gen.call_args.args else gen.call_args.kwargs["prompt"]
        self.assertIn("In care since April.", sent)


class EvalCommandTest(SummaryTestBase):
    """ai_eval --feature summary needs a live model on the agency machine;
    here a stand-in answers, to prove the measuring itself works."""

    class Model:
        def generate(self, prompt, system=None):
            # Answers properly to a fitted prompt; loses the plot on a whole one.
            if len(prompt) < BUDGET + 500:
                return ("Background: in care.\nPresenting concerns: withdrawal.\n"
                        "Recommendations: continue.")
            return "Settling in well."

    def test_long_reports_are_measured_fitted_and_whole(self):
        self.report.extracted_text = report(("Background", "In care."),
                                            ("Session Notes", LONG_NOTES),
                                            ("Recommendations", "Continue."))
        self.report.save()
        out = StringIO()
        with patch("assistant.management.commands.ai_eval.get_ai_client",
                   return_value=self.Model()):
            call_command("ai_eval", feature="summary", reps=1, limit=1, stdout=out)
        text = out.getvalue()
        self.assertIn("SUMMARIES (fitted)", text)
        self.assertIn("SUMMARIES (whole)", text)
        fitted = text[text.index("SUMMARIES (fitted)  ("):]
        self.assertIn("instructions lost      0/1", fitted.split("SUMMARIES (whole)")[0])
        self.assertIn("instructions lost      1/1", text[text.index("SUMMARIES (whole)  ("):])
