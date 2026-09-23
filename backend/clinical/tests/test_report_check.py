"""The check made before a report is filed against a child.

What it looks for is what survives writing the next report over the last one:
another child's name, their case number, age, birthday or sex. Every finding
points at text that is really in the document; nothing here uses a model.
"""
import shutil
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role
from children.models import Child
from clinical import report_check
from clinical.demo_docx import build_docx
from clinical.models import PsychologicalReport
from clinical.report_check import check_for, check_report

TODAY = date(2026, 9, 23)
FILLER = " The child was cooperative and engaged throughout the session." * 8


def kid(pk, first, last, born=None, sex=""):
    return SimpleNamespace(pk=pk, fullname=f"{first} {last}", first_name=first,
                           last_name=last, birth_date=born, gender=sex)


MARIA = kid(12, "Maria", "Santos", date(2017, 5, 3), "Female")
JUAN = kid(7, "Juan", "Cruz")
ANA = kid(9, "Ana", "Reyes")


def kinds(text, others=(JUAN, ANA), child=MARIA):
    return [f["kind"] for f in check_report(text, child, list(others), TODAY)]


class RulesTest(SimpleTestCase):
    def test_a_report_about_the_right_child_is_clean(self):
        text = ("## IDENTIFYING INFORMATION\nName: Maria L. Santos\nAge: 9\nSex: Female\n"
                "Date of Birth: May 3, 2017\nCase reference: C-0012\n" + FILLER)
        self.assertEqual([], kinds(text))

    def test_another_childs_name_in_the_forms_people_write_it(self):
        for text in ("Juan Cruz attended.", "JUAN CRUZ attended.", "Juan P. Cruz attended.",
                     "Juan Paolo Cruz attended.", "Cruz, Juan attended."):
            self.assertEqual(["other_child"], kinds("Maria Santos. " + text + FILLER), text)

    def test_words_between_two_names_are_not_a_name(self):
        # Lower-case words between them: prose, not a middle name - within the
        # two words a middle name may take, and beyond them.
        self.assertEqual([], kinds("Maria Santos. Juan and Cruz were paired." + FILLER))
        self.assertEqual([], kinds("Maria Santos. Juan asked for Cruz Street." + FILLER))
        self.assertEqual([], kinds("Maria Santos. Juan went to the Cruz school." + FILLER))
        # A first name alone is too common to mean anything.
        self.assertEqual([], kinds("Maria Santos. Juan was mentioned." + FILLER))

    def test_the_finding_says_whose_name_and_what_to_do(self):
        [finding] = check_report("Maria Santos. Juan Cruz attended." + FILLER, MARIA, [JUAN], TODAY)
        self.assertIn("Juan Cruz", finding["message"])
        self.assertIn("sibling named on purpose is fine", finding["message"])

    def test_a_namesake_is_not_flagged(self):
        # Two children with one name cannot be told apart by the text.
        self.assertEqual([], kinds("Maria Santos attended." + FILLER,
                                   others=[kid(99, "Maria", "Santos")]))

    def test_the_childs_own_name_missing(self):
        self.assertEqual(["name_missing"], kinds("The child attended." + FILLER))
        # Initials, as a confidential report may use - and a short text says nothing.
        self.assertEqual([], kinds("M.S. attended." + FILLER))
        self.assertEqual([], kinds("The child attended."))

    def test_another_case_reference(self):
        [finding] = check_report("Maria Santos, C-0007." + FILLER, MARIA, [], TODAY)
        self.assertEqual("Mentions case C-0007, but it is being filed for C-0012.",
                         finding["message"])
        self.assertEqual([], kinds("Maria Santos, C-0012." + FILLER))

    def test_an_age_that_is_not_this_childs(self):
        # Maria is 9 on TODAY. One year either way is allowed.
        self.assertEqual(["age"], kinds("Maria Santos\nEdad: 6\n" + FILLER))
        self.assertEqual(["age"], kinds("Maria is a 12-year-old girl." + FILLER))
        self.assertEqual([], kinds("Maria Santos\nAge: 8\n" + FILLER))
        # Somebody else's age is not hers.
        self.assertEqual([], kinds("Maria Santos. Her mother, 34 years old, attended." + FILLER))

    def test_a_birth_date_that_is_not_this_childs(self):
        self.assertEqual(["birth_date"], kinds("Maria Santos\nDOB: 07/14/2016\n" + FILLER))
        # Day first or month first: wrong only if wrong both ways.
        self.assertEqual([], kinds("Maria Santos\nDOB: 03/05/2017\n" + FILLER))
        self.assertEqual([], kinds("Maria Santos\nDate of birth | 3 May 2017\n" + FILLER))

    def test_a_sex_that_is_not_the_one_recorded(self):
        self.assertEqual(["sex"], kinds("Maria Santos\nKasarian: Lalaki\n" + FILLER))
        self.assertEqual([], kinds("Maria Santos\nSex | Female\n" + FILLER))

    def test_nothing_to_check_without_text(self):
        self.assertEqual([], check_report("", MARIA, [JUAN], TODAY))


def user(email, role):
    return get_user_model().objects.create_user(
        email=email, username=email.split("@")[0], password="pass1234",
        role=Role.objects.get_or_create(role_name=role)[0])


MEDIA = Path(settings.BASE_DIR) / "test-media-check"


@override_settings(MEDIA_ROOT=str(MEDIA))
class ScopeAndEndpointTest(TestCase):
    def setUp(self):
        self.pia = user("p@racco1.gov.ph", Role.PSYCHOLOGIST)
        self.oscar = user("o@racco1.gov.ph", Role.PSYCHOLOGIST)
        self.admin = user("a@racco1.gov.ph", Role.ADMINISTRATOR)
        self.staff = user("s@racco1.gov.ph", Role.STAFF)
        self.maria = Child.objects.create(fullname="Maria Santos", first_name="Maria",
                                          last_name="Santos", assigned_psychologist=self.pia)
        self.ana = Child.objects.create(fullname="Ana Reyes", first_name="Ana",
                                        last_name="Reyes", assigned_psychologist=self.pia)
        self.juan = Child.objects.create(fullname="Juan Cruz", first_name="Juan",
                                         last_name="Cruz", assigned_psychologist=self.oscar)
        self.text = "Maria Santos attended. Ana Reyes and Juan Cruz were mentioned." + FILLER

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def named(self, who):
        return sorted(f["message"].split(",")[0] for f in
                      check_for(SimpleNamespace(user=who), self.text, self.maria))

    def test_a_psychologist_is_told_only_about_their_own_children(self):
        # Telling Pia that "Juan Cruz" matches a record would tell her the
        # agency has one - about a child she has no access to.
        self.assertEqual(["Mentions Ana Reyes"], self.named(self.pia))

    def test_an_administrator_is_told_about_any_child(self):
        self.assertEqual(["Mentions Ana Reyes", "Mentions Juan Cruz"], self.named(self.admin))

    def client_for(self, who):
        client = APIClient()
        client.force_authenticate(who)
        return client

    def upload(self, name="report.docx"):
        data = build_docx([("para", self.text)])
        return SimpleUploadedFile(name, data, content_type="application/octet-stream")

    def test_the_check_endpoint_answers_and_saves_nothing(self):
        res = self.client_for(self.pia).post("/api/report-files/check/",
                                             {"child": self.maria.pk, "file": self.upload()},
                                             format="multipart")
        self.assertEqual(200, res.status_code, res.data)
        self.assertTrue(res.data["readable"])
        self.assertEqual(["other_child"], [f["kind"] for f in res.data["findings"]])
        self.assertEqual(0, PsychologicalReport.objects.count())

    def test_an_unreadable_file_is_said_to_be(self):
        old = SimpleUploadedFile("old.doc", b"\xd0\xcf\x11\xe0binary")
        res = self.client_for(self.pia).post("/api/report-files/check/",
                                             {"child": self.maria.pk, "file": old},
                                             format="multipart")
        self.assertEqual((200, False, []), (res.status_code, res.data["readable"],
                                            res.data["findings"]))

    def test_only_those_who_may_file_it_may_check_it(self):
        # Staff never file reports; Oscar cannot see Maria at all.
        res = self.client_for(self.staff).post("/api/report-files/check/",
                                               {"child": self.maria.pk, "file": self.upload()},
                                               format="multipart")
        self.assertEqual(403, res.status_code)
        res = self.client_for(self.oscar).post("/api/report-files/check/",
                                               {"child": self.maria.pk, "file": self.upload()},
                                               format="multipart")
        self.assertEqual(400, res.status_code)

    def test_the_file_is_held_to_the_upload_rules(self):
        res = self.client_for(self.pia).post(
            "/api/report-files/check/",
            {"child": self.maria.pk, "file": SimpleUploadedFile("x.html", b"<p>hi</p>")},
            format="multipart")
        self.assertEqual(400, res.status_code)

    def test_what_was_found_is_kept_on_the_report_until_looked_at(self):
        client = self.client_for(self.pia)
        res = client.post("/api/report-files/",
                          {"child": self.maria.pk, "file": self.upload(), "report_type": "initial"},
                          format="multipart")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["other_child"], [f["kind"] for f in res.data["check_findings"]])
        self.assertFalse(res.data["check_reviewed"])

        # Oscar may not mark Pia's report looked at; Pia may.
        other = self.client_for(self.oscar).post(f"/api/report-files/{res.data['id']}/review-check/")
        self.assertIn(other.status_code, (403, 404))
        done = client.post(f"/api/report-files/{res.data['id']}/review-check/")
        self.assertEqual(200, done.status_code)
        self.assertTrue(done.data["check_reviewed"])
        # The findings stay on the record.
        self.assertEqual(1, len(done.data["check_findings"]))


@override_settings(MEDIA_ROOT=str(MEDIA))
class LaterReaderTest(TestCase):
    """The check named children the UPLOADER could see. Whoever reads the
    report later sees only the names of children THEY can see."""

    def setUp(self):
        self.pia = user("p@racco1.gov.ph", Role.PSYCHOLOGIST)
        self.oscar = user("o@racco1.gov.ph", Role.PSYCHOLOGIST)
        self.admin = user("a@racco1.gov.ph", Role.ADMINISTRATOR)
        self.maria = Child.objects.create(fullname="Maria Santos", first_name="Maria",
                                          last_name="Santos", assigned_psychologist=self.pia)
        Child.objects.create(fullname="Ana Reyes", first_name="Ana", last_name="Reyes",
                             assigned_psychologist=self.pia)
        client = APIClient()
        client.force_authenticate(self.pia)
        data = build_docx([("para", "Maria Santos attended with Ana Reyes. C-0999." + FILLER)])
        res = client.post("/api/report-files/", {"child": self.maria.pk, "report_type": "progress",
                                                 "file": SimpleUploadedFile("r.docx", data)},
                          format="multipart")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["other_child", "case_reference"],
                         [f["kind"] for f in res.data["check_findings"]])
        # Maria moves to Oscar. Ana stays with Pia, out of Oscar's sight.
        Child.objects.filter(pk=self.maria.pk).update(assigned_psychologist=self.oscar)

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def kinds_for(self, who, url):
        client = APIClient()
        client.force_authenticate(who)
        res = client.get(url)
        self.assertEqual(200, res.status_code)
        rows = res.data["reports"] if "reports" in res.data else res.data
        return [f["kind"] for f in rows[0]["check_findings"]]

    def test_the_new_psychologist_is_not_told_about_a_child_who_is_not_theirs(self):
        for url in (f"/api/report-files/?child={self.maria.pk}",
                    f"/api/reports/child/{self.maria.pk}/"):
            # The case number is about the text, not a person: it stays.
            self.assertEqual(["case_reference"], self.kinds_for(self.oscar, url), url)

    def test_an_administrator_still_sees_it(self):
        self.assertEqual(["other_child", "case_reference"],
                         self.kinds_for(self.admin, f"/api/reports/child/{self.maria.pk}/"))

    def test_with_no_reader_known_it_is_left_out(self):
        from clinical.serializers import PsychologicalReportSerializer
        data = PsychologicalReportSerializer(PsychologicalReport.objects.get()).data
        self.assertEqual(["case_reference"], [f["kind"] for f in data["check_findings"]])


@override_settings(MEDIA_ROOT=str(MEDIA))
class BackfillCommandTest(TestCase):
    def setUp(self):
        self.pia = user("p@racco1.gov.ph", Role.PSYCHOLOGIST)
        self.maria = Child.objects.create(fullname="Maria Santos", first_name="Maria",
                                          last_name="Santos", assigned_psychologist=self.pia)
        Child.objects.create(fullname="Ana Reyes", first_name="Ana", last_name="Reyes",
                             assigned_psychologist=self.pia)
        self.report = PsychologicalReport(child=self.maria, author=self.pia,
                                          original_filename="old.docx")
        self.report.file.save("old.docx", ContentFile(build_docx(
            [("para", "Maria Santos attended with Ana Reyes." + FILLER)])), save=True)

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def run_command(self):
        out = StringIO()
        call_command("check_reports", stdout=out)
        self.report.refresh_from_db()
        return out.getvalue()

    def test_a_word_report_on_file_is_read_and_checked(self):
        out = self.run_command()
        self.assertIn("Text read from 1 file(s)", out)
        self.assertIn("Maria Santos attended", self.report.extracted_text)
        self.assertEqual(["other_child"], [f["kind"] for f in self.report.check_findings])

    def test_running_it_again_keeps_a_look_that_still_holds(self):
        self.run_command()
        PsychologicalReport.objects.filter(pk=self.report.pk).update(check_reviewed=True)
        self.assertIn("0 changed", self.run_command())
        self.assertTrue(self.report.check_reviewed)

    def test_a_changed_finding_asks_for_a_look_again(self):
        self.run_command()
        PsychologicalReport.objects.filter(pk=self.report.pk).update(check_reviewed=True)
        Child.objects.create(fullname="Juan Cruz", first_name="Juan", last_name="Cruz",
                             assigned_psychologist=self.pia)
        PsychologicalReport.objects.filter(pk=self.report.pk).update(
            extracted_text=self.report.extracted_text + " Juan Cruz too.")
        self.assertIn("1 changed", self.run_command())
        self.assertFalse(self.report.check_reviewed)


class StableKindsTest(SimpleTestCase):
    def test_the_kinds_the_screen_may_rely_on(self):
        self.assertEqual({"other_child", "name_missing", "case_reference", "age",
                          "birth_date", "sex"},
                         {report_check.OTHER_CHILD, report_check.NAME_MISSING,
                          report_check.CASE_REFERENCE, report_check.AGE,
                          report_check.BIRTH_DATE, report_check.SEX})
