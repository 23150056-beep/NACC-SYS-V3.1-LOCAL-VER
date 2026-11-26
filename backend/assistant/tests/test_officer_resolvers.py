"""What each chatbot tool answers the RACC Officer (WP3 of the Officer plan).

The Officer is answered over every record, as a social worker is over their
own. Three tools carry a sentence written for a social worker behind a single
`== Role.STAFF` - "these are your own records", "in your records", "nothing
outstanding in your records" - which must not be said to an Officer; the
Officer's own empty sentence for care gaps is "Nothing outstanding in any
record." No prompt, tool description or `count_people` option changed.
"""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from accounts.models import Role
from assistant import tools
from children.models import Child
from clinical.models import CaseReferral, ConsentRecord, SelfReportFlag, OpinionnaireInvite, \
    AgencyFormTemplate
from scheduling.models import Appointment, AvailabilityBlock

User = get_user_model()


class OfficerResolverBase(TestCase):
    def setUp(self):
        def make(email, role, first):
            return User.objects.create_user(
                email=email, username=email, password="pass1234", first_name=first,
                last_name="Tester", role=Role.objects.get_or_create(role_name=role)[0])
        self.officer = make("o@racco1.gov.ph", Role.OFFICER, "Corazon")
        self.sw = make("s@racco1.gov.ph", Role.STAFF, "Sol")
        self.sw2 = make("s2@racco1.gov.ph", Role.STAFF, "Rosa")
        self.psy = make("p@racco1.gov.ph", Role.PSYCHOLOGIST, "Pia")
        self.psy2 = make("p2@racco1.gov.ph", Role.PSYCHOLOGIST, "Oscar")
        self.admin = make("a@racco1.gov.ph", Role.ADMINISTRATOR, "Ada")
        self.held = Child.objects.create(
            fullname="Maria Santos", social_worker=self.sw, assigned_psychologist=self.psy,
            case_type="Foster Care", case_status=Child.STAGE_PRE_ASSESSMENT)
        self.theirs = Child.objects.create(
            fullname="Juan Dela Cruz", social_worker=self.sw2, assigned_psychologist=self.psy2,
            case_type="Adoption", case_status=Child.STAGE_PRE_ASSESSMENT)
        self.unheld = Child.objects.create(
            fullname="Una Diaz", case_type="Foster Care",
            case_status=Child.STAGE_PRE_ASSESSMENT)
        self.factory = APIRequestFactory()

    def resolve(self, user, tool, raw=None):
        call = tools.validate(tool, raw or {}, allow_internal=True)
        self.assertTrue(call.ok, call.error)
        request = self.factory.get("/")
        request.user = user
        return tools.REGISTRY[tool]["resolve"](request, call.args)

    def appointment(self, child, psychologist, hour=12):
        day = timezone.localdate()
        return Appointment.objects.create(
            child=child, psychologist=psychologist, status=Appointment.SCHEDULED,
            start=timezone.make_aware(datetime.combine(day, time(hour, 0))))


class TheAnswersAreOverEveryRecordTest(OfficerResolverBase):
    def test_the_schedule_is_the_agencys_with_names(self):
        self.appointment(self.held, self.psy, 12)
        self.appointment(self.theirs, self.psy2, 13)
        out = self.resolve(self.officer, "list_my_appointments", {"when": "today"})
        self.assertEqual("agency", out["scope"])
        self.assertEqual({"Maria Santos", "Juan Dela Cruz"}, {a["child"] for a in out["items"]})
        # A social worker is answered for their own children, as a case reference
        # beside the others'.
        sw = self.resolve(self.sw, "list_my_appointments", {"when": "today"})
        self.assertEqual("own", sw["scope"])

    def test_statistics_count_every_record_and_do_not_claim_they_are_yours(self):
        out = self.resolve(self.officer, "get_statistics", {"by": "psychologist"})
        self.assertEqual(3, out["total"])
        self.assertNotIn("your own records", out["note"])
        self.assertEqual("/reports/summary", out["screen"]["path"])
        # The social worker's note is unchanged.
        sw = self.resolve(self.sw, "get_statistics", {"by": "psychologist"})
        self.assertIn("your own records", sw["note"])
        self.assertEqual(1, sw["total"])

    def test_the_unassigned_list_says_the_agencys_sentence(self):
        out = self.resolve(self.officer, "list_unassigned_children")
        self.assertEqual(["Una Diaz"], [c["name"] for c in out["items"]])
        Child.objects.filter(pk=self.unheld.pk).update(assigned_psychologist=self.psy)
        out = self.resolve(self.officer, "list_unassigned_children")
        self.assertEqual("Every active child has a psychologist assigned.", out["empty"])
        sw = self.resolve(self.sw, "list_unassigned_children")
        self.assertIn("your records", sw["empty"])

    def test_availability_names_every_psychologist(self):
        for person in (self.psy, self.psy2):
            AvailabilityBlock.objects.create(
                psychologist=person, weekday=(timezone.localdate() + timedelta(days=1)).weekday(),
                start_time="09:00", end_time="12:00", capacity=3)
        out = self.resolve(self.officer, "find_availability", {"when": "next_week"})
        self.assertEqual("availability", out["kind"])
        self.assertEqual({"Pia Tester", "Oscar Tester"},
                         {item["psychologist"] for item in out["items"]})

    def test_the_headcount_counts_officers_only_within_everyone(self):
        anyone = self.resolve(self.officer, "count_people", {"role": "anyone"})
        self.assertEqual(User.objects.filter(status="active").count(), anyone["count"])
        self.assertFalse(tools.validate("count_people", {"role": "officer"}).ok)

    def test_a_childs_summary_the_concern_search_and_the_flags_reach_every_record(self):
        summary = self.resolve(self.officer, "get_child_summary", {"child_id": self.theirs.id})
        self.assertEqual("one", summary["match"])
        self.assertEqual("case", summary["facts"]["kind"])
        self.assertIn("next_steps", summary["facts"])
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="S", fields=[{"label": "Q?"}])
        for child in (self.held, self.theirs, self.unheld):
            invite = OpinionnaireInvite.objects.create(
                child=child, template=template, status=OpinionnaireInvite.SUBMITTED,
                submitted_at=timezone.now(), expires_at=timezone.now() + timedelta(days=7))
            SelfReportFlag.objects.create(
                invite=invite, child=child, question="Q?", answer="Lagi akong umiiyak.",
                source=SelfReportFlag.LEXICON, matched="umiiyak")
        flags = self.resolve(self.officer, "list_self_report_flags")
        self.assertEqual(3, flags["total"])
        self.assertEqual(1, self.resolve(self.sw, "list_self_report_flags")["total"])


class CareGapsTest(OfficerResolverBase):
    def test_the_social_workers_rules_run_over_every_record(self):
        out = self.resolve(self.officer, "list_care_gaps")
        self.assertEqual({self.held.id, self.theirs.id, self.unheld.id},
                         {g["child_id"] for g in out["groups"]})
        types = {gap["type"] for group in out["groups"] for gap in group["gaps"]}
        self.assertIn("no_case_referral", types)
        # The social worker's rules, never the psychologist's: no stalled
        # pre-assessment or report-due, which nobody at that desk can close.
        self.assertFalse({t for t in types if "pre_assessment" in t or "report" in t}, types)

    def test_the_empty_sentence_is_the_officers_own(self):
        Child.objects.filter(pk=self.unheld.pk).update(assigned_psychologist=self.psy)
        for child in (self.held, self.theirs, self.unheld):
            CaseReferral.objects.create(child=child, uploaded_by=self.sw, file="case-referrals/x.pdf")
            ConsentRecord.objects.create(child=child, status=ConsentRecord.SIGNED)
            Appointment.objects.create(child=child, psychologist=self.psy,
                                       start=timezone.now() + timedelta(days=3))
        out = self.resolve(self.officer, "list_care_gaps")
        self.assertEqual(([], 0), (out["groups"], out["total"]))
        self.assertEqual("Nothing outstanding in any record.", out["empty"])
        self.assertEqual("Nothing outstanding in your records.",
                         self.resolve(self.sw, "list_care_gaps")["empty"])
        self.assertNotIn("empty", self.resolve(self.admin, "list_care_gaps"))

    def test_the_dashboard_card_is_the_same_answer(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.officer)
        gaps = client.get("/api/reports/dashboard/").data["care_gaps"]
        self.assertEqual({self.held.id, self.theirs.id, self.unheld.id},
                         {g["child_id"] for g in gaps})
        self.assertIn("no_case_referral", {g["type"] for g in gaps})


class TheStaffSentencesAreStaffOnlyTest(OfficerResolverBase):
    def test_each_one_is_read_off_the_role_not_the_list(self):
        import inspect
        source = inspect.getsource(tools)
        # The three sentences written for a social worker stay behind a single
        # comparison; a SOCIAL_WORK list here would say them to the Officer.
        self.assertIn('role_of(request) == Role.STAFF:\n        out["empty"] = "Nothing outstanding '
                      'in your records."', source)
        self.assertIn('screen is _SUMMARY and role_of(request) == Role.STAFF', source)
        self.assertIn('elif role == Role.STAFF:\n        empty = "Every active child in your '
                      'records has a psychologist assigned."', source)
