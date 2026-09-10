"""The HTTP surface, and who is allowed at it - spec section 6.

The permission rows in the spec are the security boundary of a module that
holds statutory documents about children, so they are tested at the endpoint
rather than only at the service. A rule that holds in `docket.py` but is not
enforced by the view it is reachable through is not enforced.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from adoption import pipeline
from adoption.models import AdoptionCase, AdoptionStage, Requirement
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


class ApiTestBase(APITestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.other_psy = User.objects.create_user(
            email="p2@racco1.gov.ph", username="p2", password="pass1234", role=psy_role)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.other_staff = User.objects.create_user(
            email="s2@racco1.gov.ph", username="s2", password="pass1234", role=staff_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))

    def make_case(self, name="Ana Lopez", psychologist=None):
        child = Child.objects.create(
            fullname=name, assigned_psychologist=psychologist or self.psy)
        pa = PreAssessment.objects.create(
            child=child, psychologist=psychologist or self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        return pipeline.admit(child, owner=self.staff, actor=self.staff)


class BoardAccessTests(ApiTestBase):
    def test_staff_can_read_the_board(self):
        self.make_case()
        self.client.force_authenticate(self.staff)
        r = self.client.get("/api/adoption/board/")
        self.assertEqual(200, r.status_code)
        self.assertEqual(1, len(r.data["cases"]))

    def test_the_board_carries_the_stages_kpis_and_worklist_in_one_request(self):
        self.make_case()
        self.client.force_authenticate(self.admin)
        r = self.client.get("/api/adoption/board/")
        for key in ("stages", "cases", "kpis", "handoffs", "needs_attention"):
            self.assertIn(key, r.data)
        self.assertEqual(8, len(r.data["stages"]))

    def test_a_psychologist_cannot_read_the_board(self):
        # Spec section 6: psychologists get the timeline of children they
        # assessed. The caseload of the whole office is not theirs to browse.
        self.make_case()
        self.client.force_authenticate(self.psy)
        self.assertEqual(403, self.client.get("/api/adoption/board/").status_code)

    def test_signed_out_gets_nothing(self):
        self.assertEqual(401, self.client.get("/api/adoption/board/").status_code)

    def test_each_case_carries_its_derived_status_and_next_action(self):
        self.make_case()
        self.client.force_authenticate(self.staff)
        case = self.client.get("/api/adoption/board/").data["cases"][0]
        self.assertEqual("on_track", case["status"])
        self.assertEqual("Psychologist's report attached", case["next_action"])
        self.assertEqual(0, case["progress_percent"])


class CaseDetailAccessTests(ApiTestBase):
    def test_staff_can_open_any_case(self):
        case = self.make_case()
        self.client.force_authenticate(self.staff)
        r = self.client.get(f"/api/adoption/cases/{case.id}/")
        self.assertEqual(200, r.status_code)
        self.assertEqual(8, len(r.data["timeline"]))

    def test_a_psychologist_can_read_the_timeline_of_a_child_they_assessed(self):
        case = self.make_case()
        self.client.force_authenticate(self.psy)
        self.assertEqual(200, self.client.get(f"/api/adoption/cases/{case.id}/").status_code)

    def test_a_psychologist_cannot_read_another_psychologists_child(self):
        case = self.make_case(psychologist=self.other_psy)
        self.client.force_authenticate(self.psy)
        self.assertEqual(404, self.client.get(f"/api/adoption/cases/{case.id}/").status_code)

    def test_the_detail_lists_the_blockers_on_the_current_stage(self):
        case = self.make_case()
        self.client.force_authenticate(self.staff)
        r = self.client.get(f"/api/adoption/cases/{case.id}/")
        codes = {b["code"] for b in r.data["blockers"]}
        self.assertIn("owner_assigned", codes)


class AdvanceApiTests(ApiTestBase):
    def test_advancing_a_blocked_case_returns_what_is_missing(self):
        case = self.make_case()
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/adoption/cases/{case.id}/advance/")
        self.assertEqual(400, r.status_code)
        self.assertTrue(r.data["blockers"])

    def test_advancing_a_clear_case_moves_it(self):
        case = self.make_case()
        case.requirements.filter(stage=case.current_stage).update(state=Requirement.VERIFIED)
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/adoption/cases/{case.id}/advance/")
        self.assertEqual(200, r.status_code)
        case.refresh_from_db()
        self.assertEqual(2, case.current_stage.number)

    def test_a_psychologist_cannot_advance(self):
        case = self.make_case()
        case.requirements.filter(stage=case.current_stage).update(state=Requirement.VERIFIED)
        self.client.force_authenticate(self.psy)
        self.assertEqual(403,
                         self.client.post(f"/api/adoption/cases/{case.id}/advance/").status_code)

    def test_reverting_without_a_note_is_refused(self):
        case = self.make_case()
        case.requirements.filter(stage=case.current_stage).update(state=Requirement.VERIFIED)
        self.client.force_authenticate(self.staff)
        self.client.post(f"/api/adoption/cases/{case.id}/advance/")

        r = self.client.post(f"/api/adoption/cases/{case.id}/revert/", {"note": ""})
        self.assertEqual(400, r.status_code)

    def test_staff_can_close_a_case(self):
        case = self.make_case()
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/adoption/cases/{case.id}/close/",
                             {"reason": AdoptionCase.AGED_OUT})
        self.assertEqual(200, r.status_code)

    def test_a_psychologist_still_cannot_close_a_case(self):
        # Opening the module to staff does not open it to every role. The
        # caseload of the office is still not a psychologist's to run.
        case = self.make_case()
        self.client.force_authenticate(self.psy)
        r = self.client.post(f"/api/adoption/cases/{case.id}/close/",
                             {"reason": AdoptionCase.AGED_OUT})
        self.assertEqual(403, r.status_code)


class RequirementApiTests(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.case = self.make_case()
        self.req = self.case.requirements.get(stage__number=1, code="consent_on_file")

    def test_staff_can_submit(self):
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/adoption/requirements/{self.req.id}/submit/")
        self.assertEqual(200, r.status_code)
        self.req.refresh_from_db()
        self.assertEqual(Requirement.SUBMITTED, self.req.state)

    def test_staff_verify_somebody_elses_submission(self):
        self.client.force_authenticate(self.other_staff)
        self.client.post(f"/api/adoption/requirements/{self.req.id}/submit/")
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/adoption/requirements/{self.req.id}/verify/")
        self.assertEqual(200, r.status_code)

    def test_the_payload_says_WHO_submitted_it_not_just_their_name(self):
        # The screen has to hide Verify on your own upload rather than offer a
        # button the server will refuse. Matching on a display name would work
        # until two people share one.
        self.client.force_authenticate(self.staff)
        self.client.post(f"/api/adoption/requirements/{self.req.id}/submit/")
        r = self.client.get(f"/api/adoption/cases/{self.case.id}/")
        row = next(x for x in r.data["requirements"] if x["id"] == self.req.id)
        self.assertEqual(self.staff.id, row["submitted_by"])

    def test_staff_cannot_verify_their_own_submission(self):
        self.client.force_authenticate(self.staff)
        self.client.post(f"/api/adoption/requirements/{self.req.id}/submit/")
        r = self.client.post(f"/api/adoption/requirements/{self.req.id}/verify/")
        self.assertEqual(400, r.status_code)

    def test_an_administrator_cannot_verify_their_own_submission(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"/api/adoption/requirements/{self.req.id}/submit/")
        r = self.client.post(f"/api/adoption/requirements/{self.req.id}/verify/")
        self.assertEqual(400, r.status_code)

    def test_an_administrator_verifies_somebody_elses_submission(self):
        self.client.force_authenticate(self.staff)
        self.client.post(f"/api/adoption/requirements/{self.req.id}/submit/")
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/adoption/requirements/{self.req.id}/verify/")
        self.assertEqual(200, r.status_code)

    def test_waiving_needs_a_reason_whoever_grants_it(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(400, self.client.post(
            f"/api/adoption/requirements/{self.req.id}/waive/",
            {"reason": ""}).status_code)
        self.assertEqual(200, self.client.post(
            f"/api/adoption/requirements/{self.req.id}/waive/",
            {"reason": "lost"}).status_code)

        self.client.force_authenticate(self.admin)
        self.assertEqual(400, self.client.post(
            f"/api/adoption/requirements/{self.req.id}/waive/", {"reason": ""}).status_code)
        self.assertEqual(200, self.client.post(
            f"/api/adoption/requirements/{self.req.id}/waive/",
            {"reason": "guardian deceased"}).status_code)

    def test_a_psychologist_cannot_touch_a_requirement(self):
        self.client.force_authenticate(self.psy)
        self.assertEqual(403, self.client.post(
            f"/api/adoption/requirements/{self.req.id}/submit/").status_code)


class AdmitApiTests(ApiTestBase):
    def test_staff_admit_a_released_child(self):
        child = Child.objects.create(fullname="Ben Cruz", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()

        self.client.force_authenticate(self.staff)
        r = self.client.post("/api/adoption/admit/",
                             {"child": child.id, "owner": self.staff.id})
        self.assertEqual(201, r.status_code)
        self.assertTrue(AdoptionCase.objects.filter(child=child).exists())

    def test_admitting_a_child_with_no_completed_assessment_is_refused(self):
        child = Child.objects.create(fullname="Cara Diaz", assigned_psychologist=self.psy)
        self.client.force_authenticate(self.staff)
        r = self.client.post("/api/adoption/admit/",
                             {"child": child.id, "owner": self.staff.id})
        self.assertEqual(400, r.status_code)
        self.assertIn("assessment", str(r.data).lower())

    def test_a_psychologist_cannot_admit(self):
        child = Child.objects.create(fullname="Dan Reyes", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()

        self.client.force_authenticate(self.psy)
        r = self.client.post("/api/adoption/admit/",
                             {"child": child.id, "owner": self.staff.id})
        self.assertEqual(403, r.status_code)


class ChildTimelineAccessTests(ApiTestBase):
    """Spec section 6: a psychologist reads the timeline of children THEY
    assessed. That access reaches a screen through the child's own chart,
    which is the only place their role can see an adoption case at all."""

    def test_the_case_list_can_be_narrowed_to_one_child(self):
        case = self.make_case()
        self.make_case(name="Someone Else")
        self.client.force_authenticate(self.staff)

        r = self.client.get(f"/api/adoption/cases/?child={case.child_id}")
        self.assertEqual(200, r.status_code)
        self.assertEqual(1, len(r.data))
        self.assertEqual(case.id, r.data[0]["id"])

    def test_a_psychologist_sees_their_own_child_in_that_list(self):
        case = self.make_case()
        self.client.force_authenticate(self.psy)
        r = self.client.get(f"/api/adoption/cases/?child={case.child_id}")
        self.assertEqual(1, len(r.data))

    def test_a_psychologist_sees_nothing_for_another_psychologists_child(self):
        case = self.make_case(psychologist=self.other_psy)
        self.client.force_authenticate(self.psy)
        r = self.client.get(f"/api/adoption/cases/?child={case.child_id}")
        self.assertEqual(0, len(r.data))
