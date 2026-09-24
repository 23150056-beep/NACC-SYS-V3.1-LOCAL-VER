"""Reading reports: Word as well as PDF, headings found whatever the layout.

Word files were accepted and never read - only PDFs were - so a report written
in Word could not be searched, summarised or checked. The heading rules are
measured against the agency's own Word forms in docs/agency-forms/, which use
no heading styles at all.
"""
import io
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role
from children.models import Child
from clinical import services
from clinical.demo_docx import build_docx
from clinical.demo_reports import build_pdf
from clinical.models import CaseReferral, PsychologicalReport
from clinical.services import (extract_docx_text, extract_pdf_text, extract_text,
                               ensure_text, looks_like_heading)

FORMS = Path(settings.BASE_DIR).parent / "docs" / "agency-forms"
BLOCKS = [
    ("title", "Psychological Evaluation Report"),
    ("heading", "Identifying Information"),
    ("fields", [("Name", "Maria Santos"), ("Age", "9")]),
    ("heading", "Reason for Referral"),
    ("para", "Referred for counselling support."),
    ("bullets", ["Withdrawal from peers"]),
]
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def headings(text):
    return [line[3:] for line in text.splitlines() if line.startswith("## ")]


def raw_docx(document_xml, styles_xml=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        if styles_xml:
            zf.writestr("word/styles.xml", styles_xml)
    return ContentFile(out.getvalue(), name="x.docx")


def body(inner):
    return f'<?xml version="1.0"?><w:document {W}><w:body>{inner}</w:body></w:document>'


class RealAgencyFormsTest(SimpleTestCase):
    """The two real Word files in the repository - written in Word, by people."""

    def read(self, name):
        with open(FORMS / name, "rb") as handle:
            return extract_docx_text(handle)

    def test_the_consent_form_reads_with_every_numbered_section(self):
        text = self.read("Adoption-Informed-Consent-Psychological-Evaluation.docx")
        found = headings(text)
        # The agency's own transcription lists the same ten, in order.
        for numbered in ("I. PURPOSE OF THE PSYCHOLOGICAL EVALUATION",
                         "II. NATURE AND SCOPE OF THE EVALUATION",
                         "III. VOLUNTARY PARTICIPATION",
                         "IV. CONFIDENTIALITY AND LIMITATIONS OF CONFIDENTIALITY",
                         "V. RISKS AND DISCOMFORTS", "VI. BENEFITS",
                         "VII. FEES AND PAYMENT", "VIII. ACCURACY AND COOPERATION",
                         "IX. QUESTIONS AND CLARIFICATIONS", "X. CONSENT"):
            self.assertIn(numbered, found)
        self.assertIn("I understand that the purpose of this psychological evaluation", text)

    def test_the_questionnaire_reads_with_its_roman_numbered_sections(self):
        # Plain title-case lines - no style, no bold, no capitals.
        text = self.read("Pre-assessment.docx")
        found = headings(text)
        for numbered in ("I. Background Information", "II. Developmental History",
                         "IX. Adjustment and Readiness", "VII. Self-Concept"):
            self.assertIn(numbered, found)
        # The questions are body text, not headings.
        self.assertIn("How long has the child been under your care?", text)
        self.assertNotIn("How long has the child been under your care?", found)


class DocxTest(SimpleTestCase):
    def test_heading_styles_and_a_details_table(self):
        text = extract_docx_text(ContentFile(build_docx(BLOCKS, styled=True), name="a.docx"))
        self.assertEqual(["Psychological Evaluation Report", "Identifying Information",
                          "Reason for Referral"], headings(text))
        self.assertIn("Name | Maria Santos", text)
        self.assertIn("• Withdrawal from peers", text)

    def test_bold_lines_as_headings(self):
        # How a hand-built template marks sections: a bold line and no style.
        text = extract_docx_text(ContentFile(build_docx(BLOCKS, styled=False), name="a.docx"))
        self.assertEqual(["Psychological Evaluation Report", "Identifying Information",
                          "Reason for Referral"], headings(text))
        self.assertIn("Name: Maria Santos", text)

    def test_a_localised_heading_style_is_still_a_heading(self):
        # German Word names the style id "berschrift1"; its name stays "heading 1".
        styles = (f'<w:styles {W}><w:style w:type="paragraph" w:styleId="berschrift1">'
                  '<w:name w:val="heading 1"/></w:style></w:styles>')
        doc = body('<w:p><w:pPr><w:pStyle w:val="berschrift1"/></w:pPr>'
                   '<w:r><w:t>Hintergrund der Familie</w:t></w:r></w:p>')
        self.assertEqual("## Hintergrund der Familie", extract_docx_text(raw_docx(doc, styles)))

    def test_deleted_text_and_fallback_copies_are_not_read(self):
        mc = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        doc = (f'<?xml version="1.0"?><w:document {W} {mc}><w:body><w:p>'
               '<w:r><w:t>Kept</w:t></w:r>'
               '<w:del><w:r><w:delText>Deleted</w:delText></w:r></w:del>'
               '<mc:AlternateContent><mc:Choice><w:r><w:t> once</w:t></w:r></mc:Choice>'
               '<mc:Fallback><w:r><w:t> once</w:t></w:r></mc:Fallback></mc:AlternateContent>'
               '</w:p></w:body></w:document>')
        self.assertEqual("Kept once", extract_docx_text(raw_docx(doc)))

    def test_a_declaration_is_refused_however_far_down_it_is(self):
        # A genuine document.xml never has one, and padding could hide one from
        # a check of only the first few kilobytes.
        padding = "<!--" + "x" * 10_000 + "-->"
        doc = (f'<?xml version="1.0"?>{padding}<!DOCTYPE d [<!ENTITY e "boom">]>'
               f'<w:document {W}><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual("", extract_docx_text(raw_docx(doc)))

    def test_an_oversized_part_is_refused(self):
        doc = body("<w:p><w:r><w:t>" + "a" * 5000 + "</w:t></w:r></w:p>")
        original = services.MAX_DOCX_XML_BYTES
        services.MAX_DOCX_XML_BYTES = 1000
        try:
            self.assertEqual("", extract_docx_text(raw_docx(doc)))
        finally:
            services.MAX_DOCX_XML_BYTES = original

    def test_anything_that_is_not_a_docx_gives_nothing(self):
        self.assertEqual("", extract_docx_text(ContentFile(b"not a zip", name="x.docx")))
        self.assertEqual("", extract_text(ContentFile(b"\xd0\xcf\x11\xe0", name="old.doc")))


class PdfTest(SimpleTestCase):
    def test_bold_capital_headings_in_a_pdf(self):
        text = extract_pdf_text(ContentFile(build_pdf(BLOCKS), name="a.pdf"))
        self.assertEqual(["PSYCHOLOGICAL EVALUATION REPORT", "IDENTIFYING INFORMATION",
                          "REASON FOR REFERRAL"], headings(text))
        self.assertIn("Name: Maria Santos", text)


class HeadingRuleTest(SimpleTestCase):
    def test_what_is_and_is_not_a_heading(self):
        cases = [
            ("REASON FOR REFERRAL", False, True),
            ("Reason for Referral:", True, True),
            ("I. Background Information", False, True),
            ("IV. Emotional and Behavioral Functioning", False, True),
            # A recommendation in a numbered list, a field, a sentence, a question.
            ("2. Attend school regularly", False, False),
            ("Name: Maria Santos", False, False),
            ("She was cooperative throughout.", True, False),
            ("How long has the child been under your care?", False, False),
            ("II. the child was cooperative", False, False),
            ("C-0012", False, False),
        ]
        for text, bold, expected in cases:
            self.assertEqual(expected, looks_like_heading(text, bold), text)


@override_settings(MEDIA_ROOT=str(Path(settings.BASE_DIR) / "test-media-reports"))
class UploadTest(TestCase):
    def setUp(self):
        self.psy = get_user_model().objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = get_user_model().objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(social_worker=self.staff, fullname="Maria Santos", first_name="Maria",
                                          last_name="Santos", assigned_psychologist=self.psy)

    def tearDown(self):
        import shutil
        shutil.rmtree(Path(settings.BASE_DIR) / "test-media-reports", ignore_errors=True)

    def post(self, user, url, data):
        client = APIClient()
        client.force_authenticate(user)
        return client.post(url, data, format="multipart")

    def docx(self, name="report.docx"):
        return SimpleUploadedFile(name, build_docx(BLOCKS), content_type="application/octet-stream")

    def test_a_word_report_is_read_on_upload(self):
        res = self.post(self.psy, "/api/report-files/",
                        {"child": self.child.pk, "file": self.docx(), "report_type": "initial"})
        self.assertEqual(201, res.status_code, res.data)
        self.assertTrue(res.data["has_text"])
        self.assertIn("## Reason for Referral", PsychologicalReport.objects.get().extracted_text)

    def test_a_word_referral_is_read_on_upload(self):
        res = self.post(self.staff, "/api/case-referrals/",
                        {"child": self.child.pk, "file": self.docx("referral.docx")})
        self.assertEqual(201, res.status_code, res.data)
        self.assertIn("Referred for counselling support.", CaseReferral.objects.get().extracted_text)

    def test_one_uploaded_before_word_could_be_read_is_read_when_needed(self):
        report = PsychologicalReport(child=self.child, author=self.psy,
                                     original_filename="old.docx")
        report.file.save("old.docx", ContentFile(build_docx(BLOCKS)), save=True)
        self.assertEqual("", report.extracted_text)
        self.assertIn("Referred for counselling support.", ensure_text(report))
        report.refresh_from_db()
        self.assertIn("## Identifying Information", report.extracted_text)

    def test_an_old_word_97_file_stays_unread(self):
        report = PsychologicalReport(child=self.child, author=self.psy,
                                     original_filename="old.doc")
        report.file.save("old.doc", ContentFile(b"\xd0\xcf\x11\xe0binary"), save=True)
        self.assertEqual("", ensure_text(report))
