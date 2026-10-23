"""A psychologist cannot open the social worker's case study (owner, 23 Oct
2026), so the case referral file is how they read why a child was referred.
These hold that door open for the ASSIGNED psychologist and shut for anyone
else: the child's page data, the list, the download - and no writes."""
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from accounts.models import Role, User
from children.models import AssignmentRequest, Child
from clinical.models import CaseReferral


def make_user(email, role_name):
    role, _ = Role.objects.get_or_create(role_name=role_name)
    return User.objects.create_user(email=email, username=email, password="pass1234", role=role)


class PsychologistReadsTheReferralTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sw = make_user("sw@t.ph", Role.STAFF)
        cls.psy = make_user("psy@t.ph", Role.PSYCHOLOGIST)
        cls.asked = make_user("asked@t.ph", Role.PSYCHOLOGIST)
        cls.other = make_user("other@t.ph", Role.PSYCHOLOGIST)
        cls.child = Child.objects.create(
            fullname="Ana Cruz", case_type="Adoption",
            social_worker=cls.sw, assigned_psychologist=cls.psy)
        cls.referral = CaseReferral.objects.create(
            child=cls.child, uploaded_by=cls.sw, original_filename="referral.pdf",
            file=SimpleUploadedFile("referral.pdf", b"%PDF-1.4 referral body"),
            extracted_text="Family background.")
        AssignmentRequest.objects.create(
            child=cls.child, psychologist=cls.asked, requested_by=cls.sw)

    def as_user(self, user):
        self.client.force_authenticate(user)
        return self.client

    def test_the_child_page_data_carries_the_referral(self):
        res = self.as_user(self.psy).get(f"/api/reports/child/{self.child.pk}/")
        self.assertEqual(200, res.status_code)
        self.assertEqual([self.referral.pk], [r["id"] for r in res.data["case_referrals"]])

    def test_the_list_carries_it(self):
        res = self.as_user(self.psy).get(f"/api/case-referrals/?child={self.child.pk}")
        self.assertEqual(200, res.status_code)
        self.assertEqual([self.referral.pk], [r["id"] for r in res.data])

    def test_the_file_downloads(self):
        res = self.as_user(self.psy).get(f"/api/case-referrals/{self.referral.pk}/download/")
        self.assertEqual(200, res.status_code)
        self.assertEqual(b"%PDF-1.4 referral body", b"".join(res.streaming_content))

    def test_a_psychologist_who_is_not_assigned_gets_nothing(self):
        for user in (self.other, self.asked):  # never had the child; only asked
            client = self.as_user(user)
            self.assertEqual([], client.get(f"/api/case-referrals/?child={self.child.pk}").data)
            self.assertEqual(
                404, client.get(f"/api/case-referrals/{self.referral.pk}/download/").status_code)
            self.assertEqual(
                404, client.get(f"/api/reports/child/{self.child.pk}/").status_code)

    def test_a_psychologist_cannot_change_or_remove_it(self):
        client = self.as_user(self.psy)
        url = f"/api/case-referrals/{self.referral.pk}/"
        self.assertEqual(403, client.patch(url, {"description": "x"}, format="json").status_code)
        self.assertEqual(403, client.delete(url).status_code)
        upload = client.post("/api/case-referrals/", {
            "child": self.child.pk,
            "file": SimpleUploadedFile("again.pdf", b"%PDF-1.4 again")}, format="multipart")
        self.assertEqual(403, upload.status_code)
        self.assertEqual(1, CaseReferral.objects.count())
