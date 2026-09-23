"""Moving the fictional caseload to a hosted branch.

`seed_demo_data` refuses to run against a hosted database and that guard is not
weakened — its own comment gives the reason: mixing fictional records into real
case files is "not a data loss, something worse: a file that cannot be
trusted." So the demo children travel as a fixture instead.
"""
import json
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import Role
from children.models import Child
from clinical.models import CaseReferral
from scheduling.models import AvailabilityBlock

User = get_user_model()


class ExportDemoDataTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=self.psy)

    def _export(self):
        path = Path(tempfile.mkdtemp()) / "demo.json"
        call_command("export_demo_data", output=str(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_writes_the_children(self):
        self.assertIn("children.child", {row["model"] for row in self._export()})

    def test_excludes_users(self):
        # Importing users would collide with the real accounts on the branch,
        # which are the entire reason for using that database.
        models = {row["model"] for row in self._export()}
        self.assertNotIn("accounts.user", models)
        self.assertNotIn("accounts.role", models)

    def test_excludes_assistant_jobs(self):
        # Audit rows carry the questions people typed, which name children.
        self.assertNotIn("assistant.assistantjob",
                         {row["model"] for row in self._export()})

    def test_reports_what_it_wrote(self):
        out = StringIO()
        path = Path(tempfile.mkdtemp()) / "demo.json"
        call_command("export_demo_data", output=str(path), stdout=out)
        self.assertIn("1 children", out.getvalue())


class ImportDemoDataTest(TestCase):
    """The import runs against a branch that already holds the real accounts."""

    def setUp(self):
        self.role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        # Two "real" accounts, as a Neon branch would carry.
        self.real_a = User.objects.create_user(
            email="real.a@racco1.gov.ph", username="ra", password="pass1234",
            role=self.role)
        self.real_b = User.objects.create_user(
            email="real.b@racco1.gov.ph", username="rb", password="pass1234",
            role=self.role)
        # A seeder account that must NOT survive as an assignee.
        self.seeded = User.objects.create_user(
            email="m.bulan@racco1.gov.ph", username="mb", password="pass1234",
            role=self.role)
        self.fixture = Path(tempfile.mkdtemp()) / "demo.json"

    def _write_fixture(self, count=4):
        # created_at is NOT NULL and auto_now_add does not fire during
        # loaddata, so a fixture has to carry it. A real export from
        # export_demo_data always does; this one is hand-built.
        rows = [{
            "model": "children.child",
            "pk": 900 + i,
            "fields": {"fullname": f"Demo Child {i}",
                       "assigned_psychologist": self.seeded.pk,
                       "created_at": "2026-08-01T00:00:00Z",
                       "updated_at": "2026-08-01T00:00:00Z"},
        } for i in range(count)]
        self.fixture.write_text(json.dumps(rows), encoding="utf-8")

    def test_loads_the_children(self):
        self._write_fixture()
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertEqual(4, Child.objects.count())

    def test_spreads_them_across_the_real_psychologists(self):
        # The whole point: the demo caseload lands on the accounts that already
        # exist, not on the seeder's invented ones.
        self._write_fixture()
        call_command("import_demo_data", fixture=str(self.fixture))
        assignees = set(Child.objects.values_list(
            "assigned_psychologist__email", flat=True))
        self.assertEqual({"real.a@racco1.gov.ph", "real.b@racco1.gov.ph",
                          "m.bulan@racco1.gov.ph"} & assignees, assignees)
        self.assertIn("real.a@racco1.gov.ph", assignees)
        self.assertIn("real.b@racco1.gov.ph", assignees)

    def test_every_child_has_an_assignee_that_exists_here(self):
        self._write_fixture()
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertEqual(0, Child.objects.filter(
            assigned_psychologist__isnull=True).count())

    def test_clear_removes_existing_children_first(self):
        Child.objects.create(fullname="Pre-existing", assigned_psychologist=self.real_a)
        self._write_fixture()
        call_command("import_demo_data", fixture=str(self.fixture), clear=True)
        self.assertEqual(4, Child.objects.count())
        self.assertFalse(Child.objects.filter(fullname="Pre-existing").exists())

    def test_without_clear_existing_children_survive(self):
        Child.objects.create(fullname="Pre-existing", assigned_psychologist=self.real_a)
        self._write_fixture()
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertTrue(Child.objects.filter(fullname="Pre-existing").exists())

    def test_can_set_a_known_password_for_one_account(self):
        self._write_fixture()
        call_command("import_demo_data", fixture=str(self.fixture),
                     set_password="real.a@racco1.gov.ph:demo12345")
        self.real_a.refresh_from_db()
        self.assertTrue(self.real_a.check_password("demo12345"))

    def test_refuses_an_unknown_account_for_the_password(self):
        self._write_fixture()
        with self.assertRaises(CommandError):
            call_command("import_demo_data", fixture=str(self.fixture),
                         set_password="nobody@racco1.gov.ph:demo12345")

    def test_refuses_when_no_psychologist_exists(self):
        # Better to stop than to import a caseload nobody can see.
        Child.objects.all().delete()
        User.objects.filter(role=self.role).delete()
        self._write_fixture()
        with self.assertRaises(CommandError):
            call_command("import_demo_data", fixture=str(self.fixture))


class TheImportedCaseloadIsBookableTest(TestCase):
    """The third time this exact fault has shipped, so it gets a test.

    `seed_demo_data` learned to give its psychologists a working week, and
    then to write a case referral for every child, because the booking
    endpoint refuses without either. `import_demo_data` is the path a HOSTED
    demo gets its caseload by — and it did neither, so the branch it loads
    carries forty children the calendar turns away.

    `fix_demo_schedule` repairs exactly this, and refuses to run against a
    hosted database. That left no supported way to make a deployed demo
    bookable at all.

    Checked through `booking.bookable_slots`, the same function the slot grid
    and the endpoint both run. Asserting that rows exist would pass while the
    calendar stayed empty.
    """

    def setUp(self):
        self.role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.real = User.objects.create_user(
            email="real.a@racco1.gov.ph", username="ra", password="pass1234",
            role=self.role)
        self.fixture = Path(tempfile.mkdtemp()) / "demo.json"
        rows = [{
            "model": "children.child",
            "pk": 900 + i,
            "fields": {"fullname": f"Demo Child {i}",
                       "assigned_psychologist": self.real.pk,
                       "created_at": "2026-08-01T00:00:00Z",
                       "updated_at": "2026-08-01T00:00:00Z"},
        } for i in range(3)]
        self.fixture.write_text(json.dumps(rows), encoding="utf-8")

    def _next_clinic_day(self):
        from django.utils import timezone
        from scheduling.demo_schedule import CLINIC_WEEKDAYS
        day = timezone.localdate() + timedelta(days=1)
        while day.weekday() not in CLINIC_WEEKDAYS:
            day += timedelta(days=1)
        return day

    def test_every_imported_child_can_be_offered_a_slot(self):
        from scheduling import booking
        call_command("import_demo_data", fixture=str(self.fixture))
        day = self._next_clinic_day()
        for child in Child.objects.all():
            slots = booking.bookable_slots(
                child.assigned_psychologist, child, day)
            self.assertTrue(
                slots,
                f"{child.fullname} has no bookable hour: "
                f"{booking.why_empty(child.assigned_psychologist, day, child=child)}")

    def test_it_gives_every_psychologist_a_working_week(self):
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertTrue(
            AvailabilityBlock.objects.filter(psychologist=self.real).exists())

    def test_every_imported_child_has_a_case_referral(self):
        from scheduling import booking
        call_command("import_demo_data", fixture=str(self.fixture))
        for child in Child.objects.all():
            self.assertTrue(booking.referral_on_file(child), child.fullname)

    def test_every_imported_child_has_a_readable_report_by_its_psychologist(self):
        # Reports are files, like referrals, so the fixture cannot carry them.
        # Written after the reassignment: the author is who the child is with now.
        from clinical.models import PsychologicalReport
        call_command("import_demo_data", fixture=str(self.fixture))
        for child in Child.objects.all():
            report = PsychologicalReport.objects.get(child=child)
            self.assertEqual(child.assigned_psychologist_id, report.author_id)
            self.assertIn("## ", report.extracted_text, child.fullname)

    def test_it_reports_what_it_had_to_add(self):
        out = StringIO()
        call_command("import_demo_data", fixture=str(self.fixture), stdout=out)
        said = out.getvalue()
        self.assertIn("referral", said.lower())
        self.assertIn("availability", said.lower())

    def test_it_leaves_a_referral_that_is_already_there_alone(self):
        # Running the import twice must not pile up documents, and must never
        # replace something somebody uploaded.
        call_command("import_demo_data", fixture=str(self.fixture))
        before = list(CaseReferral.objects.values_list("pk", flat=True))
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertEqual(before,
                         list(CaseReferral.objects.values_list("pk", flat=True)))
