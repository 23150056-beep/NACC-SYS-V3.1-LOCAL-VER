"""Whether a child can be booked at all, visible from the list.

A session cannot be booked without a case referral on file. Until now the only
way to learn that was to open the booking form, choose a day, and read the
refusal - the child list and the drawer showed nothing, so "why can I not book
this child" was a question the screen could not answer.

One boolean on the child answers it everywhere the child is rendered.

It is a method field with a prefetch check rather than a plain `.exists()`:
the list returns forty children on several screens, and forty extra queries to
draw forty chips is the kind of thing that is invisible in development and
obvious in a field office.
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import CaseReferral

User = get_user_model()


class HasCaseReferralTest(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.with_ref = Child.objects.create(social_worker=self.staff, 
            fullname="Has Referral", assigned_psychologist=self.psy)
        self.without = Child.objects.create(social_worker=self.staff, 
            fullname="No Referral", assigned_psychologist=self.psy)
        CaseReferral.objects.create(
            child=self.with_ref, uploaded_by=self.staff,
            file=SimpleUploadedFile("referral.pdf", b"%PDF-1.4 x"),
            original_filename="referral.pdf")
        self.client.force_authenticate(self.staff)

    def _by_name(self):
        rows = self.client.get("/api/children/").data
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        return {r["fullname"]: r for r in rows}

    def test_the_list_says_which_children_have_one(self):
        rows = self._by_name()
        self.assertTrue(rows["Has Referral"]["has_case_referral"])
        self.assertFalse(rows["No Referral"]["has_case_referral"])

    def test_the_detail_route_says_so_too(self):
        # The drawer opens from the list, but a deep link goes to the detail.
        response = self.client.get(f"/api/children/{self.without.id}/")
        self.assertIn("has_case_referral", response.data)
        self.assertFalse(response.data["has_case_referral"])

    def test_uploading_one_flips_it(self):
        CaseReferral.objects.create(
            child=self.without, uploaded_by=self.staff,
            file=SimpleUploadedFile("late.pdf", b"%PDF-1.4 y"),
            original_filename="late.pdf")
        self.assertTrue(self._by_name()["No Referral"]["has_case_referral"])

    def test_the_query_count_does_not_grow_with_the_caseload(self):
        """The property that matters, measured twice rather than asserted once.

        A first draft of this pinned a number it had just measured, which is
        circular - it would have passed with a query per child. Counting at two
        different sizes is what actually catches an N+1.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get("/api/children/")
            return len(ctx)

        small = count()
        for i in range(12):
            child = Child.objects.create(social_worker=self.staff, fullname=f"Bulk {i}",
                                         assigned_psychologist=self.psy)
            if i % 2 == 0:
                CaseReferral.objects.create(
                    child=child, uploaded_by=self.staff,
                    file=SimpleUploadedFile(f"r{i}.pdf", b"%PDF-1.4 z"),
                    original_filename=f"r{i}.pdf")
        self.assertEqual(small, count(),
                         "reading has_case_referral costs a query per child")
