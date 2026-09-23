"""Invented reports, in the three layouts psychologists' own reports come in.

What the demo holds has to be what an upload would have produced: each file is
read by the real extractor and checked by the real check before it is saved.
"""
import shutil
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import Role
from children.models import Child
from clinical import demo_reports
from clinical.models import PsychologicalReport
from locations.models import Barangay, Municipality, Province

MEDIA = Path(settings.BASE_DIR) / "test-media-demo-reports"


@override_settings(MEDIA_ROOT=str(MEDIA))
class InstallReportsTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        make = get_user_model().objects.create_user
        self.pia = make(email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        self.oscar = make(email="o@racco1.gov.ph", username="o", password="pass1234", role=role)
        names = [("Maria", "Santos"), ("Ana", "Reyes"), ("Juan", "Cruz"),
                 ("Liza", "Bautista"), ("Rico", "Lim"), ("Tess", "Garcia")]
        self.children = [
            Child.objects.create(fullname=f"{f} {l}", first_name=f, last_name=l,
                                 assigned_psychologist=self.pia if i % 2 else self.oscar)
            for i, (f, l) in enumerate(names)]

    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def install(self):
        return demo_reports.install_reports(Child.objects.order_by("pk"))

    def test_one_report_each_and_every_psychologist_sees_all_three_layouts(self):
        self.assertEqual(6, self.install())
        for psychologist in (self.pia, self.oscar):
            reports = PsychologicalReport.objects.filter(author=psychologist).order_by("child_id")
            kinds = [(r.original_filename.rsplit(".", 1)[1], r.report_type) for r in reports]
            # Styled Word, plain Word, PDF - one of each.
            self.assertEqual([("docx", "initial"), ("docx", "progress"), ("pdf", "progress")],
                             kinds, psychologist.email)

    def test_every_one_is_read_with_its_sections(self):
        self.install()
        for report in PsychologicalReport.objects.all():
            headings = [l for l in report.extracted_text.splitlines() if l.startswith("## ")]
            self.assertGreaterEqual(len(headings), 8, report.original_filename)
            self.assertIn("DEMONSTRATION DOCUMENT", report.extracted_text)
            self.assertEqual(report.child.assigned_psychologist_id, report.author_id)

    def test_exactly_one_carries_a_leftover_for_the_check_to_find(self):
        self.install()
        flagged = [r for r in PsychologicalReport.objects.all() if r.check_findings]
        self.assertEqual(1, len(flagged))
        [finding] = flagged[0].check_findings
        self.assertEqual("other_child", finding["kind"])
        # Another child of the same psychologist - the report it was started from.
        leftover = next(c for c in self.children if c.fullname in finding["message"])
        self.assertEqual(flagged[0].child.assigned_psychologist_id,
                         leftover.assigned_psychologist_id)

    def test_running_it_again_adds_nothing_and_a_real_report_is_left_alone(self):
        real = PsychologicalReport(child=self.children[0], author=self.oscar,
                                   original_filename="the-real-one.pdf")
        real.file.save("real.pdf", ContentFile(b"%PDF-1.4"), save=True)
        self.assertEqual(5, self.install())
        self.assertEqual(0, self.install())
        self.assertEqual(["the-real-one.pdf"], list(PsychologicalReport.objects.filter(
            child=self.children[0]).values_list("original_filename", flat=True)))


@override_settings(DEBUG=True, MEDIA_ROOT=str(MEDIA))
class SeederTest(TestCase):
    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_the_seeder_writes_a_report_for_every_active_child(self):
        province = Province.objects.create(psgc_code="012800000", name="Ilocos Norte")
        town = Municipality.objects.create(psgc_code="012812000", name="Laoag City",
                                           province=province)
        Barangay.objects.create(psgc_code="012812001", name="Barangay 1", municipality=town)
        out = StringIO()
        call_command("seed_demo_data", children=6, stdout=out)
        active = Child.objects.filter(status=Child.ACTIVE).count()
        self.assertEqual(active, PsychologicalReport.objects.exclude(extracted_text="").count())
        self.assertIn(f"{active} psychological reports written", out.getvalue())
