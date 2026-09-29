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


class AFreshExportFromAnotherMachineTest(TestCase):
    """An export made since 24 Sep carries the exporting machine's social
    worker ids, and since 29 Sep its custodian numbers and consent. On a
    branch those ids name somebody else or nobody: a missing one failed the
    whole load, and one naming a psychologist left the child with no SW who
    could see it. Rehearsed against a stand-in branch on 29 Sep 2026."""

    def setUp(self):
        psy = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff = Role.objects.create(role_name=Role.STAFF)
        self.psy = User.objects.create_user(
            email="real.psy@racco1.gov.ph", username="rp", password="pass1234",
            role=psy, status=User.ACTIVE)
        self.sw = User.objects.create_user(
            email="real.sw@racco1.gov.ph", username="rs", password="pass1234",
            role=staff, status=User.ACTIVE)
        self.fixture = Path(tempfile.mkdtemp()) / "demo.json"

    def _write(self, social_worker, **extra):
        rows = [{
            "model": "children.child",
            "pk": 900,
            "fields": {"fullname": "Demo Child", "case_type": "Foster Care",
                       "assigned_psychologist": self.psy.pk,
                       "social_worker": social_worker,
                       "created_at": "2026-08-01T00:00:00Z",
                       "updated_at": "2026-08-01T00:00:00Z", **extra},
        }]
        self.fixture.write_text(json.dumps(rows), encoding="utf-8")

    def test_a_social_worker_id_that_is_nobody_here_still_loads(self):
        self._write(social_worker=self.sw.pk + 50)
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertEqual(self.sw, Child.objects.get().social_worker)

    def test_a_social_worker_id_that_is_a_psychologist_here_is_dealt_again(self):
        self._write(social_worker=self.psy.pk)
        call_command("import_demo_data", fixture=str(self.fixture))
        self.assertEqual(self.sw, Child.objects.get().social_worker)

    def test_custodian_numbers_and_consent_stay_behind(self):
        # A demo custodian has a name and never a number, so the demo cannot
        # text a handset somebody typed into a local copy.
        self._write(social_worker=self.sw.pk,
                    custodian_name="Rosa Dela Cruz (foster parent)",
                    custodian_contact="+639171234567",
                    custodian_sms_consent=True,
                    custodian_sms_consent_at="2026-09-29T01:00:00Z",
                    custodian_sms_consent_by=self.sw.pk + 50,
                    custodian_contact_verified_at="2026-09-29T01:00:00Z")
        call_command("import_demo_data", fixture=str(self.fixture))
        child = Child.objects.get()
        self.assertEqual("Rosa Dela Cruz (foster parent)", child.custodian_name)
        self.assertEqual("", child.custodian_contact)
        self.assertFalse(child.custodian_sms_consent)
        self.assertIsNone(child.custodian_sms_consent_at)
        self.assertIsNone(child.custodian_sms_consent_by)
        self.assertIsNone(child.custodian_contact_verified_at)

    def test_it_says_so(self):
        self._write(social_worker=self.sw.pk + 50)
        out = StringIO()
        call_command("import_demo_data", fixture=str(self.fixture), stdout=out)
        self.assertIn("1 social worker(s) here", out.getvalue())


class RecordsFollowTheirChildTest(TestCase):
    """Sessions and notes named the exporting machine's psychologists, and
    only the child itself was dealt again: on a stand-in branch numbered
    differently, all 198 demo sessions sat with somebody other than the
    child's psychologist, 68 of them with a Staff account (29 Sep 2026).

    Built from a real export rather than hand-written rows, then the
    exporting machine's accounts are taken away: one id is re-used by a
    social worker, the rest name nobody."""

    def setUp(self):
        from clinical.models import InstrumentCatalog, RemarkNote
        from django.utils import timezone
        from scheduling.models import Appointment
        psy = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff = Role.objects.create(role_name=Role.STAFF)

        def account(email, role):
            return User.objects.create_user(
                email=email, username=email.split("@")[0], password="pass1234",
                role=role, status=User.ACTIVE)

        local_a, local_b = account("a@local.ph", psy), account("b@local.ph", psy)
        local_sw = account("sw@local.ph", staff)
        start = timezone.now() + timedelta(days=3)
        kid_a = Child.objects.create(fullname="Child A", assigned_psychologist=local_a,
                                     social_worker=local_sw)
        kid_b = Child.objects.create(fullname="Child B", assigned_psychologist=local_b,
                                     social_worker=local_sw)
        Appointment.objects.create(child=kid_a, psychologist=local_a, booked_by=local_sw,
                                   start=start)
        Appointment.objects.create(child=kid_b, psychologist=local_b, booked_by=local_b,
                                   start=start + timedelta(hours=1))
        RemarkNote.objects.create(child=kid_a, author=local_a, text="Settling in.")
        # Child B was with A before a transfer: the earlier psychologist's note.
        RemarkNote.objects.create(child=kid_b, author=local_a, text="Before the move.")
        InstrumentCatalog.objects.create(title="B's own scale", owner=local_b)
        self.fixture = Path(tempfile.mkdtemp()) / "demo.json"
        call_command("export_demo_data", output=str(self.fixture), stdout=StringIO())

        # The branch.
        Child.objects.all().delete()
        InstrumentCatalog.objects.all().delete()
        User.objects.filter(pk__in=[local_a.pk, local_sw.pk]).delete()
        local_b.role = staff
        local_b.save()
        self.branch_sw = local_b
        self.p1, self.p2 = account("p1@branch.ph", psy), account("p2@branch.ph", psy)
        self.pks = (kid_a.pk, kid_b.pk)
        call_command("import_demo_data", fixture=str(self.fixture), clear=True,
                     stdout=StringIO())

    def test_every_session_is_with_the_childs_psychologist(self):
        from scheduling.models import Appointment
        sessions = list(Appointment.objects.select_related("child"))
        self.assertEqual(2, len(sessions))
        for s in sessions:
            self.assertEqual(s.child.assigned_psychologist_id, s.psychologist_id)

    def test_nothing_clinical_lands_on_a_staff_account(self):
        from clinical.models import RemarkNote
        from scheduling.models import Appointment
        self.assertFalse(RemarkNote.objects.filter(author=self.branch_sw).exists())
        self.assertFalse(Appointment.objects.filter(psychologist=self.branch_sw).exists())

    def test_what_the_social_worker_did_is_the_new_social_workers(self):
        from scheduling.models import Appointment
        booked = Appointment.objects.get(child_id=self.pks[0])
        self.assertEqual(self.branch_sw, booked.child.social_worker)
        self.assertEqual(self.branch_sw, booked.booked_by)

    def test_an_earlier_psychologists_note_stays_somebody_elses(self):
        # A's caseload went to p1 and B's child to p2; the note A wrote on B's
        # child before the transfer is p1's, so the carry-history rule still
        # has a colleague's note to hide.
        from clinical.models import RemarkNote
        kid_a, kid_b = (Child.objects.get(pk=pk) for pk in self.pks)
        self.assertEqual((self.p1, self.p2),
                         (kid_a.assigned_psychologist, kid_b.assigned_psychologist))
        self.assertEqual(self.p1, RemarkNote.objects.get(child=kid_b).author)

    def test_an_owned_instrument_follows_its_psychologists_caseload(self):
        from clinical.models import InstrumentCatalog
        self.assertEqual(self.p2, InstrumentCatalog.objects.get().owner)

    def test_a_child_already_here_keeps_its_psychologist(self):
        # Without --clear the import adds; only the fixture's children are dealt.
        Child.objects.all().delete()
        here = Child.objects.create(fullname="Already here", assigned_psychologist=self.p2)
        call_command("import_demo_data", fixture=str(self.fixture), stdout=StringIO())
        here.refresh_from_db()
        self.assertEqual(self.p2, here.assigned_psychologist)
