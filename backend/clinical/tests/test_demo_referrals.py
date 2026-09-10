"""Demo children the system will actually let you book.

Booking now requires a case referral on file, and `seed_demo_data` has never
created one - so the rule shipped and closed the calendar for all forty
seeded children at once. Every one of them refused, correctly, for a reason
that is true of the fixture rather than of the feature.

This is the same standard applied to availability: mock data the real rules
reject is not a sample of the system, it is a second system sharing a
database, and every feature built against it inherits the difference.

The documents are invented, like the children. Each one says so in its own
text, because a file that reads like a real referral is a file somebody will
eventually mistake for one.
"""
from django.contrib.auth import get_user_model

from accounts.models import Role
from children.models import Child
from clinical import demo_referrals
from clinical.models import CaseReferral
from django.test import TestCase

User = get_user_model()


class InstallReferralsTest(TestCase):
    def setUp(self):
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.children = [
            Child.objects.create(fullname=name, assigned_psychologist=self.psy)
            for name in ("Ana Lopez", "Ben Cruz", "Cara Diaz")
        ]

    def test_every_child_without_one_gets_one(self):
        made = demo_referrals.install_referrals(self.children, uploaded_by=self.staff)
        self.assertEqual(3, made)
        for child in self.children:
            self.assertTrue(CaseReferral.objects.filter(child=child).exists())

    def test_running_it_again_adds_nothing(self):
        demo_referrals.install_referrals(self.children, uploaded_by=self.staff)
        self.assertEqual(0, demo_referrals.install_referrals(
            self.children, uploaded_by=self.staff))
        self.assertEqual(3, CaseReferral.objects.count())

    def test_a_child_who_already_has_one_is_left_alone(self):
        # Never overwrite a real upload with an invented one.
        existing = CaseReferral.objects.create(
            child=self.children[0], original_filename="the-real-thing.pdf")
        demo_referrals.install_referrals(self.children, uploaded_by=self.staff)
        self.assertEqual(
            1, CaseReferral.objects.filter(child=self.children[0]).count())
        self.assertEqual(
            "the-real-thing.pdf",
            CaseReferral.objects.get(child=self.children[0]).original_filename)
        self.assertEqual(existing.pk,
                         CaseReferral.objects.get(child=self.children[0]).pk)

    def test_the_document_says_it_is_invented(self):
        demo_referrals.install_referrals(self.children, uploaded_by=self.staff)
        referral = CaseReferral.objects.get(child=self.children[0])
        referral.file.open("r")
        try:
            text = referral.file.read()
        finally:
            referral.file.close()
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        self.assertIn("Ana Lopez", text)
        self.assertIn("invented", text.lower())

    def test_it_names_the_child_in_the_filename(self):
        demo_referrals.install_referrals(self.children, uploaded_by=self.staff)
        referral = CaseReferral.objects.get(child=self.children[1])
        self.assertIn("referral", referral.original_filename.lower())


class TheChildrenAreBookableAfterwardsTest(TestCase):
    """The point of the exercise, checked against the real rule."""

    def setUp(self):
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.child = Child.objects.create(fullname="Ana Lopez",
                                          assigned_psychologist=self.psy)

    def test_before_it_runs_the_booking_rule_refuses_them(self):
        from scheduling import booking
        self.assertFalse(booking.referral_on_file(self.child))

    def test_afterwards_the_booking_rule_is_satisfied(self):
        from scheduling import booking
        demo_referrals.install_referrals([self.child])
        self.assertTrue(booking.referral_on_file(self.child))
