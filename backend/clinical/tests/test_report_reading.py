"""Reading a report on screen (24 Sep 2026): /report-files/<id>/text/.

Staff asked to see the psychologists' reports without downloading them. The
text endpoint answers exactly who the download answers - it is the same object
through the same queryset - so reading on screen widens nobody's access.
"""
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role
from children.models import Child
from clinical.demo_docx import build_docx
from clinical.models import PsychologicalReport

User = get_user_model()
MEDIA = Path(tempfile.gettempdir()) / "report-reading-media"


@override_settings(MEDIA_ROOT=str(MEDIA))
class ReadingAReportTest(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345", role=roles[role])
        self.staff = make("sw@t.ph", Role.STAFF)
        self.psy = make("psy@t.ph", Role.PSYCHOLOGIST)
        self.other_psy = make("psy2@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(fullname="Ana Cruz", assigned_psychologist=self.psy)
        self.report = PsychologicalReport(child=self.child, author=self.psy,
                                          original_filename="progress.docx")
        self.report.file.save("progress.docx", ContentFile(build_docx([
            ("title", "Psychological Report"), ("heading", "Findings"),
            ("para", "Ana settled well into the routine of the house.")])), save=True)

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def _read(self, user, report=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user)
        return client.get(f"/api/report-files/{(report or self.report).id}/text/")

    def test_staff_can_read_a_psychologists_report(self):
        r = self._read(self.staff)
        self.assertEqual(200, r.status_code, r.data)
        self.assertTrue(r.data["readable"])
        self.assertIn("## Findings", r.data["text"])
        self.assertIn("settled well", r.data["text"])

    def test_the_childs_psychologist_can(self):
        self.assertEqual(200, self._read(self.psy).status_code)

    def test_nobody_the_download_would_refuse(self):
        self.assertEqual(404, self._read(self.other_psy).status_code)
        self.assertEqual(401, self._read(None).status_code)
        # And the download agrees, for the same people.
        client = APIClient()
        client.force_authenticate(self.other_psy)
        self.assertEqual(404, client.get(f"/api/report-files/{self.report.id}/download/").status_code)

    def test_a_file_that_cannot_be_read_says_so(self):
        old = PsychologicalReport(child=self.child, author=self.psy, original_filename="old.doc")
        old.file.save("old.doc", ContentFile(b"\xd0\xcf\x11\xe0 not readable"), save=True)
        r = self._read(self.staff, old)
        self.assertEqual(200, r.status_code)
        self.assertFalse(r.data["readable"])
        self.assertEqual("", r.data["text"])

    def test_reading_changes_nothing(self):
        # Staff cannot write clinical records at all (403, before the method is
        # looked at); the psychologist can, and this endpoint still only reads.
        url = f"/api/report-files/{self.report.id}/text/"
        client = APIClient()
        client.force_authenticate(self.staff)
        self.assertEqual(403, client.post(url).status_code)
        client.force_authenticate(self.psy)
        self.assertEqual(405, client.post(url).status_code)
