"""The three tables: their names, the one-per-child rule and the one-per-key rule."""
from django.db import IntegrityError, transaction

from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.tests.base import CaseStudyTestCase
from children.models import Child


class TablesTest(CaseStudyTestCase):
    def test_the_tables_are_named_explicitly(self):
        # Like the removed adoption and samd apps, so a drop written from an
        # app label cannot silently drop nothing (CLAUDE.md, "Removed").
        self.assertEqual("tbl_case_study", CaseStudy._meta.db_table)
        self.assertEqual("tbl_case_study_section", CaseStudySection._meta.db_table)
        self.assertEqual("tbl_case_study_final", CaseStudyFinal._meta.db_table)

    def test_a_new_one_is_a_draft_with_nothing_answered(self):
        study = self.start()
        self.assertEqual("draft", study.status)
        self.assertIsNone(study.date_prepared)
        self.assertIsNone(study.custody_over_two_years)

    def test_one_per_child(self):
        self.start()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CaseStudy.objects.create(child=self.child)

    def test_one_section_per_key(self):
        study = self.start()
        CaseStudySection.objects.create(case_study=study, key="a2_sources", value=[])
        with self.assertRaises(IntegrityError), transaction.atomic():
            CaseStudySection.objects.create(case_study=study, key="a2_sources", value=[])

    def test_a_section_starts_at_version_one(self):
        study = self.start()
        section = CaseStudySection.objects.create(case_study=study, key="a2_sources")
        self.assertEqual(1, section.version)
        self.assertFalse(section.not_applicable)
        self.assertIsNone(section.value)

    def test_the_rows_go_with_the_child_and_the_people_may_go_without_them(self):
        study = self.start()
        CaseStudySection.objects.create(
            case_study=study, key="a2_sources", value=["x"], updated_by=self.sw)
        CaseStudyFinal.objects.create(case_study=study, snapshot={"a": 1}, finalized_by=self.sw)
        self.sw.delete()
        study.refresh_from_db()
        self.assertIsNone(study.created_by)
        self.assertIsNone(study.sections.get().updated_by)
        self.assertIsNone(study.finals.get().finalized_by)
        Child.objects.filter(pk=self.child.pk).delete()
        self.assertFalse(CaseStudy.objects.exists())
        self.assertFalse(CaseStudySection.objects.exists())
        self.assertFalse(CaseStudyFinal.objects.exists())

    def test_a_final_copy_is_never_changed(self):
        study = self.start()
        final = CaseStudyFinal.objects.create(case_study=study, snapshot={"a": 1})
        final.snapshot = {"a": 2}
        with self.assertRaises(ValueError):
            final.save()
        self.assertEqual({"a": 1}, CaseStudyFinal.objects.get().snapshot)

    def test_finalizing_again_writes_a_new_one(self):
        study = self.start()
        CaseStudyFinal.objects.create(case_study=study, snapshot={"n": 1})
        CaseStudyFinal.objects.create(case_study=study, snapshot={"n": 2})
        self.assertEqual([2, 1], [f.snapshot["n"] for f in study.finals.all()])
