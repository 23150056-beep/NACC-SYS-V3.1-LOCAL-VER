"""What the screen is shown from the record: Part I, the offered starting text,
and the Domestic Relative custody answer.

Part I is never stored in the case study - it is read from the child's record -
and every age on the report is worked out as of the date prepared. The starting
text is offered and never saved, and a psychologist's summary is offered only
once somebody has confirmed it.
"""
from datetime import date

from django.core.files.base import ContentFile

from case_study.models import CaseStudySection
from case_study.tests.base import TODAY, CaseStudyTestCase
from children.models import Child
from clinical.models import CaseReferral, PsychologicalReport


class FactsBase(CaseStudyTestCase):
    def facts(self, user=None):
        return self.as_user(user or self.sw).get(self.url()).data["record_facts"]

    def update_child(self, **fields):
        Child.objects.filter(pk=self.child.pk).update(**fields)


class PartOneFromTheRecordTest(FactsBase):
    def test_the_identifying_information_as_the_record_holds_it(self):
        self.update_child(
            alias="Bunso", place_of_birth_or_found="Laoag City, Ilocos Norte",
            birth_status="Non-Marital", legal_status="With IVC", health_condition="Healthy",
            current_placement="With the prospective adoptive family",
            date_of_admission=date(2024, 5, 1), education_level="Grade 1")
        facts = self.facts()
        self.assertEqual("Ana Cruz", facts["fullname"])
        self.assertEqual("Bunso", facts["alias"])
        self.assertEqual("Female", facts["gender"])
        self.assertEqual("2019-03-02", facts["birth_date"])
        self.assertEqual("Laoag City, Ilocos Norte", facts["place_of_birth_or_found"])
        self.assertEqual("Non-Marital", facts["birth_status"])
        self.assertEqual("Surrendered", facts["case_category"])
        self.assertEqual("With IVC", facts["legal_status"])
        self.assertEqual("Healthy", facts["health_condition"])
        self.assertEqual("2024-05-01", facts["date_of_admission"])
        self.assertIsNone(facts["date_of_placement_to_custodian"])
        self.assertEqual("Regular", facts["type_of_adoption"])
        self.assertEqual("With the prospective adoptive family", facts["current_placement"])
        self.assertEqual("Grade 1", facts["education_level"])

    def test_special_needs_come_with_the_health_condition(self):
        self.update_child(health_condition="With special needs", special_needs="Mild hearing loss")
        facts = self.facts()
        self.assertEqual("With special needs", facts["health_condition"])
        self.assertEqual("Mild hearing loss", facts["special_needs"])

    def test_the_cdclaa_date_only_with_an_issued_cdclaa(self):
        self.update_child(legal_status="With IVC", legal_status_date=date(2025, 1, 2))
        facts = self.facts()
        self.assertEqual("2025-01-02", facts["legal_status_date"])
        self.assertIsNone(facts["cdclaa_date"])
        self.update_child(legal_status="With Issued CDCLAA")
        self.assertEqual("2025-01-02", self.facts()["cdclaa_date"])

    def test_the_facts_of_a_foundling(self):
        self.update_child(case_category="Abandoned", date_found=date(2019, 3, 12),
                          place_of_birth_or_found="Pasuquin, Ilocos Norte")
        facts = self.facts()
        self.assertEqual("2019-03-12", facts["date_found"])
        self.assertEqual("Pasuquin, Ilocos Norte", facts["place_found"])
        self.assertEqual(0, facts["age_when_found"])

    def test_age_when_found_is_worked_out_from_the_date_found(self):
        self.update_child(case_category="Abandoned", date_found=date(2022, 3, 3))
        self.assertEqual(3, self.facts()["age_when_found"])

    def test_nothing_found_for_a_child_who_was_not(self):
        facts = self.facts()
        self.assertIsNone(facts["date_found"])
        self.assertIsNone(facts["place_found"])
        self.assertIsNone(facts["age_when_found"])


class AgeFollowsTheDatePreparedTest(FactsBase):
    def test_without_a_date_prepared_the_age_is_as_of_today(self):
        self.start()
        facts = self.facts()
        self.assertEqual(7, facts["age"])
        self.assertEqual(TODAY.isoformat(), facts["age_as_of"])

    def test_with_one_it_is_as_of_that_day_not_today(self):
        self.start(date_prepared=date(2024, 3, 1))
        facts = self.facts()
        self.assertEqual(4, facts["age"])
        self.assertEqual("2024-03-01", facts["age_as_of"])

    def test_the_day_of_the_birthday_counts(self):
        self.start(date_prepared=date(2024, 3, 2))
        self.assertEqual(5, self.facts()["age"])

    def test_before_one_is_started_it_is_as_of_today(self):
        self.assertEqual(7, self.facts()["age"])

    def test_no_birth_date_no_age(self):
        self.update_child(birth_date=None)
        self.assertIsNone(self.facts()["age"])

    def test_a_psychologist_sees_the_same_age(self):
        self.start(date_prepared=date(2024, 3, 1))
        self.assertEqual(4, self.facts(self.psy)["age"])


class CustodyAnswerTest(FactsBase):
    def answer(self):
        return self.as_user(self.sw).get(self.url()).data["custody_pre_answer"]

    def test_it_is_asked_of_a_domestic_relative_adoption_only(self):
        self.start()
        self.assertIsNone(self.answer())

    def test_two_years_or_more_before_today_is_a_yes(self):
        self.update_child(type_of_adoption="Domestic Relative",
                          date_of_placement_to_custodian=date(2024, 10, 8))
        self.start()
        self.assertIs(True, self.answer())

    def test_less_than_two_years_is_a_no(self):
        self.update_child(type_of_adoption="Domestic Relative",
                          date_of_placement_to_custodian=date(2024, 10, 9))
        self.start()
        self.assertIs(False, self.answer())

    def test_it_is_worked_out_against_the_date_prepared(self):
        self.update_child(type_of_adoption="Domestic Relative",
                          date_of_placement_to_custodian=date(2024, 6, 1))
        study = self.start(date_prepared=date(2026, 6, 1))
        self.assertIs(True, self.answer())
        study.date_prepared = date(2026, 5, 31)
        study.save()
        self.assertIs(False, self.answer())

    def test_a_placement_on_the_29th_of_february(self):
        self.update_child(type_of_adoption="Domestic Relative",
                          date_of_placement_to_custodian=date(2024, 2, 29))
        study = self.start(date_prepared=date(2026, 2, 28))
        self.assertIs(False, self.answer())
        study.date_prepared = date(2026, 3, 1)
        study.save()
        self.assertIs(True, self.answer())

    def test_a_date_prepared_on_the_29th_of_february(self):
        self.update_child(type_of_adoption="Domestic Relative",
                          date_of_placement_to_custodian=date(2026, 2, 28))
        study = self.start(date_prepared=date(2028, 2, 29))
        self.assertIs(True, self.answer())
        Child.objects.filter(pk=self.child.pk).update(
            date_of_placement_to_custodian=date(2026, 3, 1))
        study.save()
        self.assertIs(False, self.answer())

    def test_without_a_placement_date_it_is_unknown_not_no(self):
        self.update_child(type_of_adoption="Domestic Relative")
        self.start()
        self.assertIsNone(self.answer())
        self.update_child(date_of_placement_to_custodian=date(2024, 10, 9))
        self.assertIs(False, self.answer())

    def test_it_is_unknown_before_a_case_study_is_started_too(self):
        self.update_child(type_of_adoption="Domestic Relative")
        self.assertIsNone(self.answer())

    def test_the_answer_the_social_worker_gave_is_kept_apart_from_it(self):
        self.update_child(type_of_adoption="Domestic Relative",
                          date_of_placement_to_custodian=date(2020, 1, 1))
        self.start(custody_over_two_years=False)
        body = self.as_user(self.sw).get(self.url()).data
        self.assertIs(True, body["custody_pre_answer"])
        self.assertIs(False, body["custody_over_two_years"])


class StartingTextTest(FactsBase):
    def seeds(self):
        return self.as_user(self.sw).get(self.url()).data["seeds"]

    def report(self, summary, confirmed, author=None):
        report = PsychologicalReport(
            child=self.child, author=author or self.psy, original_filename="report.pdf",
            ai_summary=summary, ai_summary_confirmed=confirmed)
        report.file.save("report.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return report

    def test_nothing_on_the_record_offers_nothing(self):
        self.assertEqual({"a2_circumstances": None, "a3_medical": None,
                          "a3_psych_highlights": None}, self.seeds())

    def test_the_circumstances_of_referral(self):
        self.update_child(referral_source="LGU", referral_reason="Mother could not support him.")
        referral = CaseReferral(child=self.child, uploaded_by=self.sw)
        referral.file.save("referral.pdf", ContentFile(b"%PDF-1.4"), save=True)
        self.assertEqual(
            "Referred by LGU.\n\nMother could not support him.\n\n"
            "A case referral was filed on 8 October 2026.",
            self.seeds()["a2_circumstances"])

    def test_the_latest_case_referral_is_the_one_named(self):
        older = CaseReferral(child=self.child, uploaded_by=self.sw)
        older.file.save("one.pdf", ContentFile(b"%PDF-1.4"), save=True)
        CaseReferral.objects.filter(pk=older.pk).update(
            created_at=older.created_at.replace(year=2026, month=3, day=4))
        newer = CaseReferral(child=self.child, uploaded_by=self.sw)
        newer.file.save("two.pdf", ContentFile(b"%PDF-1.4"), save=True)
        self.assertIn("8 October 2026", self.seeds()["a2_circumstances"])
        self.assertNotIn("March", self.seeds()["a2_circumstances"])

    def test_medical_notes_and_special_needs(self):
        self.update_child(medical_notes="Treated for pneumonia at 2.",
                          special_needs="Mild hearing loss")
        self.assertEqual("Treated for pneumonia at 2.\n\nSpecial needs: Mild hearing loss.",
                         self.seeds()["a3_medical"])
        self.update_child(medical_notes="")
        self.assertEqual("Special needs: Mild hearing loss.", self.seeds()["a3_medical"])

    def test_a_confirmed_summary_is_offered(self):
        self.report("Cooperative; mild anxiety.", confirmed=True)
        self.assertEqual("Cooperative; mild anxiety.", self.seeds()["a3_psych_highlights"])

    def test_a_draft_summary_is_never_offered(self):
        self.report("Draft nobody has read.", confirmed=False)
        self.assertIsNone(self.seeds()["a3_psych_highlights"])

    def test_the_latest_report_decides_and_an_older_confirmed_one_is_not_used_instead(self):
        old = self.report("Old confirmed summary.", confirmed=True)
        PsychologicalReport.objects.filter(pk=old.pk).update(
            created_at=old.created_at.replace(year=2026, month=1, day=5))
        self.report("New draft.", confirmed=False)
        self.assertIsNone(self.seeds()["a3_psych_highlights"])

    def test_a_confirmed_flag_with_no_summary_offers_nothing(self):
        self.report("", confirmed=True)
        self.assertIsNone(self.seeds()["a3_psych_highlights"])

    def test_a_report_of_another_childs_is_not_used(self):
        other = Child.objects.create(
            first_name="Ben", last_name="Lim", case_type="Adoption", social_worker=self.sw2)
        report = PsychologicalReport(
            child=other, author=self.psy, ai_summary="Someone else's.", ai_summary_confirmed=True)
        report.file.save("other.pdf", ContentFile(b"%PDF-1.4"), save=True)
        self.assertIsNone(self.seeds()["a3_psych_highlights"])

    def test_offering_text_saves_none_of_it(self):
        self.update_child(referral_reason="Mother could not support him.")
        self.start()
        self.seeds()
        self.assertEqual(0, CaseStudySection.objects.count())
