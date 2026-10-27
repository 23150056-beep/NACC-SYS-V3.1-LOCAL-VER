"""A test run must not write into the developer's media directory.

Django isolates the database for a test run and does NOT isolate file
storage. `MEDIA_ROOT` stays pointed at `backend/media`, so every test that
saves a FileField writes a real file there and nothing ever removes it.

Found by counting: 1,527 files under `media/case-referrals` for 42 rows —
1,485 orphans, every one of them left by a test. It ran for months because
the directory is gitignored, so the growth was invisible to `git status`, and
because a leak is not a failure: nothing goes red, the disk just fills.

Six test modules had already noticed and each made its own temporary
directory with `override_settings`. None of them cleaned it up, and the
modules that write the most files — the referral seeders — were never among
them. A guard applied per file is a guard somebody forgets, so this is done
once for the whole run in `config.test_runner`.
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase

REPO_MEDIA = Path(settings.BASE_DIR) / "media"


class MediaRootIsIsolatedTest(SimpleTestCase):
    def test_it_is_not_the_working_copys_media_directory(self):
        self.assertNotEqual(REPO_MEDIA.resolve(),
                            Path(settings.MEDIA_ROOT).resolve())

    def test_a_saved_file_lands_outside_the_working_copy(self):
        name = default_storage.save("isolation-probe.txt", ContentFile(b"probe"))
        try:
            written = Path(default_storage.path(name)).resolve()
            self.assertFalse(
                written.is_relative_to(REPO_MEDIA.resolve()),
                f"a test wrote into the repository: {written}")
        finally:
            default_storage.delete(name)


class TheReferralSeederWritesOutsideTheWorkingCopyTest(TestCase):
    """The specific path that leaked, kept honest by going through it."""

    def test_seeded_referrals_do_not_land_in_the_repository(self):
        from accounts.models import Role
        from children.models import Child
        from clinical import demo_referrals
        from clinical.models import CaseReferral
        from django.contrib.auth import get_user_model

        psy = get_user_model().objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        child = Child.objects.create(fullname="Ana Lopez", assigned_psychologist=psy)
        demo_referrals.install_referrals([child])

        written = Path(CaseReferral.objects.get(child=child).file.path).resolve()
        self.assertFalse(
            written.is_relative_to(REPO_MEDIA.resolve()),
            f"the seeder wrote into the repository: {written}")


class EachTestGetsAMediaFolderOfItsOwnTest(SimpleTestCase):
    """`IsolatedMediaMixin`: a fresh folder per test, outside the working copy,
    gone afterwards. Shared fixed folders removed in tearDown raced across the
    parallel runner's workers."""

    def run_inner(self):
        import unittest

        from config.test_media import IsolatedMediaMixin

        seen = []

        class Inner(IsolatedMediaMixin, SimpleTestCase):
            def test_a(self):
                seen.append(self.media_root)
                assert Path(settings.MEDIA_ROOT) == Path(self.media_root)
                default_storage.save("x.txt", ContentFile(b"x"))

            test_b = test_a

        with open(os.devnull, "w") as quiet:
            result = unittest.TextTestRunner(stream=quiet).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(Inner))
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        return seen

    def test_folders_are_distinct_outside_the_working_copy_and_removed(self):
        seen = self.run_inner()
        self.assertEqual(2, len(set(seen)))
        for folder in seen:
            self.assertFalse(Path(folder).resolve().is_relative_to(
                Path(settings.BASE_DIR).resolve()))
            self.assertFalse(Path(folder).exists(), "left behind")

    def test_the_run_wide_setting_is_back_afterwards(self):
        before = settings.MEDIA_ROOT
        self.run_inner()
        self.assertEqual(before, settings.MEDIA_ROOT)


class NoTestFixesItsOwnMediaFolderTest(SimpleTestCase):
    """A fixed MEDIA_ROOT in a test is the shared folder again."""

    def test_no_tests_module_sets_one(self):
        this = Path(__file__).resolve()
        skip = {this, Path(settings.BASE_DIR, "config", "test_media.py").resolve(),
                Path(settings.BASE_DIR, "config", "test_runner.py").resolve()}
        offenders = []
        for path in Path(settings.BASE_DIR).rglob("*.py"):
            if ".venv" in path.parts or "tests" not in path.parts or path.resolve() in skip:
                continue
            text = path.read_text(encoding="utf-8")
            if "MEDIA_ROOT=" in text or "test-media-" in text:
                offenders.append(str(path.relative_to(settings.BASE_DIR)))
        self.assertEqual([], offenders, "use IsolatedMediaMixin instead")
