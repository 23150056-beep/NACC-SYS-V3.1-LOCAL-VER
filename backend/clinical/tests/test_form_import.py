"""Upload a template on the Clinical interview step (owner's request, 28 Sep 2026).

A Word or PDF interview form becomes a DRAFT template: sections and questions
proposed from the file, nothing saved until the psychologist reviews it and
saves through the ordinary create, attestation included. Measured against the
agency's real interview form in docs/agency-forms/ - written in Word, by
people, not by the fixture.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from clinical.form_import import draft_from_text
from clinical.models import AgencyFormTemplate

User = get_user_model()
FORM = Path(settings.BASE_DIR).parent / "docs" / "agency-forms" / "Pre-assessment.docx"


class DraftFromTextTest(SimpleTestCase):
    def test_headings_become_sections_and_lines_under_them_questions(self):
        d = draft_from_text("## I. Background\nWho do you live with?\n## II. School\nWhat grade are you in?")
        self.assertEqual(
            [("I. Background", "section"), ("Who do you live with?", "long_text"),
             ("II. School", "section"), ("What grade are you in?", "long_text")],
            [(f["label"], f["field_type"]) for f in d["fields"]])
        self.assertEqual("I. Background", d["title"])

    def test_lines_before_the_first_section_are_the_introduction(self):
        d = draft_from_text("Read each question aloud.\n## I. Home\nWho takes care of you?")
        self.assertEqual("Read each question aloud.", d["body"])
        self.assertNotIn("Read each question aloud.", [f["label"] for f in d["fields"]])

    def test_a_form_with_no_headings_is_all_questions(self):
        d = draft_from_text("Who do you live with?\nWhat makes you happy?", fallback_title="Child interview")
        self.assertEqual(["long_text", "long_text"], [f["field_type"] for f in d["fields"]])
        self.assertEqual("Child interview", d["title"])

    def test_a_blank_to_fill_is_short_text_and_a_date_is_a_date(self):
        d = draft_from_text("## Details\nName of informant: ________\nDate of interview:\nRelationship ______")
        self.assertEqual([("Name of informant", "text"), ("Date of interview", "date"),
                          ("Relationship", "text")],
                         [(f["label"], f["field_type"]) for f in d["fields"][1:]])

    def test_table_cells_are_fields_of_their_own(self):
        d = draft_from_text("## Details\nName: | Age: | Date:")
        self.assertEqual(["Name", "Age", "Date"], [f["label"] for f in d["fields"][1:]])

    def test_a_repeated_question_gets_a_number_since_answers_are_kept_by_wording(self):
        d = draft_from_text("## Mother\nHow do you feel about her?\n## Father\nHow do you feel about her?")
        labels = [f["label"] for f in d["fields"] if f["field_type"] != "section"]
        self.assertEqual(["How do you feel about her?", "How do you feel about her? (2)"], labels)

    def test_an_instruction_paragraph_is_not_a_question(self):
        long = "Explain to the respondent " + "that everything said is confidential " * 10
        d = draft_from_text(f"## I. Home\n{long}\nWho takes care of you?")
        self.assertEqual(["Who takes care of you?"],
                         [f["label"] for f in d["fields"] if f["field_type"] != "section"])
        self.assertIn("confidential", d["body"])

    def test_headings_alone_are_not_a_form(self):
        self.assertEqual([], draft_from_text("## I. Home\n## II. School")["fields"])


class TheAgencysOwnFormTest(SimpleTestCase):
    def test_both_questionnaires_and_every_section_come_through(self):
        from clinical.services import extract_docx_text
        with FORM.open("rb") as handle:
            d = draft_from_text(extract_docx_text(handle))
        sections = [f["label"] for f in d["fields"] if f["field_type"] == "section"]
        questions = [f["label"] for f in d["fields"] if f["field_type"] != "section"]
        self.assertEqual("ADOPTION PRE-ASSESSMENT QUESTIONNAIRE FOR THE CUSTODIAN/ PAP", d["title"])
        # The second questionnaire's title carries a bracketed note that makes
        # it too long for the report reader's heading rule; it is a section.
        self.assertTrue(any(s.startswith("ADOPTION PRE-ASSESSMENT QUESTIONNAIRE FOR THE CHILD")
                            for s in sections))
        self.assertIn("I. Background Information", sections)
        self.assertIn("IX. Adjustment and Readiness", sections)
        self.assertIn("Who do you live with?", questions)
        self.assertIn("If you could wish for three things, what would they be?", questions)
        self.assertEqual(80, len(questions))
        self.assertEqual(len(questions), len(set(questions)))


class ImportEndpointTest(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        self.psy = User.objects.create_user(email="p@t.ph", username="p", password="x",
                                            role=roles[Role.PSYCHOLOGIST])
        self.staff = User.objects.create_user(email="s@t.ph", username="s", password="x",
                                              role=roles[Role.STAFF])

    def _upload(self, user, name, content, **extra):
        client = APIClient()
        client.force_authenticate(user)
        return client.post("/api/form-templates/import/",
                           {"file": SimpleUploadedFile(name, content), **extra}, format="multipart")

    def test_the_agency_form_comes_back_as_a_draft_and_nothing_is_saved(self):
        res = self._upload(self.psy, "Pre-assessment.docx", FORM.read_bytes())
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("clinical_interview", res.data["form_type"])
        self.assertEqual("Pre-assessment.docx", res.data["source"])
        self.assertEqual(99, len(res.data["fields"]))
        self.assertFalse(AgencyFormTemplate.objects.exists())

    def test_the_draft_saves_through_the_ordinary_create(self):
        draft = self._upload(self.psy, "Pre-assessment.docx", FORM.read_bytes()).data
        client = APIClient()
        client.force_authenticate(self.psy)
        res = client.post("/api/form-templates/", {
            "form_type": draft["form_type"], "title": draft["title"], "body": draft["body"],
            "fields": draft["fields"], "attestation": True}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        saved = AgencyFormTemplate.objects.get()
        self.assertEqual((self.psy, 99), (saved.owner, len(saved.fields)))

    def test_an_old_word_file_is_refused_with_what_to_do(self):
        res = self._upload(self.psy, "form.doc", b"\xd0\xcf\x11\xe0 old word")
        self.assertEqual(400, res.status_code)
        self.assertIn("save it as .docx", res.data["file"])

    def test_other_files_are_refused(self):
        self.assertEqual(400, self._upload(self.psy, "form.png", b"\x89PNG").status_code)

    def test_an_unreadable_file_says_so(self):
        res = self._upload(self.psy, "form.docx", b"not a zip")
        self.assertEqual(400, res.status_code)
        self.assertIn("No text could be read", res.data["file"])

    def test_staff_cannot_import_since_they_cannot_create_templates(self):
        self.assertEqual(403, self._upload(self.staff, "Pre-assessment.docx",
                                           FORM.read_bytes()).status_code)

    def test_an_unknown_form_type_is_refused(self):
        res = self._upload(self.psy, "Pre-assessment.docx", FORM.read_bytes(), form_type="novel")
        self.assertEqual(400, res.status_code)
