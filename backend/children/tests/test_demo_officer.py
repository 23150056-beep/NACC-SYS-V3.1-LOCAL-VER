"""The demo's RACC Officer (WP3 of the Officer plan): one account that holds
nothing, so the screens built for it can be seen against seeded data.

`seed_demo_data` makes it in the style of the other demo accounts; the dealing
of social workers (children/demo_owners.py) is to Staff alone, so an Officer
present is never dealt a child.
"""
import shutil
from io import StringIO
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role
from children import demo_owners
from children.models import Child

User = get_user_model()
MEDIA = Path(tempfile.gettempdir()) / "demo-officer-test-media"


@override_settings(DEBUG=True, MEDIA_ROOT=str(MEDIA))
class TheSeederMakesAnOfficerTest(TestCase):
    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def seed(self, **options):
        from locations.models import Barangay, Municipality, Province
        if not Province.objects.exists():
            province = Province.objects.create(psgc_code="013300000", name="La Union")
            town = Municipality.objects.create(psgc_code="013314000", name="San Fernando",
                                               province=province)
            Barangay.objects.create(psgc_code="013314001", name="Catbangen", municipality=town)
        call_command("seed_demo_data", children=6, stdout=StringIO(), **options)

    def test_one_officer_in_the_style_of_the_others(self):
        self.seed()
        (officer,) = User.objects.filter(role__role_name=Role.OFFICER)
        self.assertTrue(officer.email.endswith("@racco1.gov.ph"))
        self.assertEqual((officer.email, "Corazon", "Dizon"),
                         (officer.username, officer.first_name, officer.last_name))
        self.assertEqual((User.ACTIVE, False), (officer.status, officer.must_change_password))
        self.assertTrue(officer.check_password("demo1234"))
        for other in User.objects.filter(role__role_name__in=(Role.STAFF, Role.PSYCHOLOGIST)):
            self.assertTrue(other.check_password("demo1234"), other.email)

    def test_running_it_again_adds_no_second_officer(self):
        self.seed()
        self.seed(force=True)
        self.assertEqual(1, User.objects.filter(role__role_name=Role.OFFICER).count())

    def test_the_officer_holds_nothing_and_every_child_goes_to_a_social_worker(self):
        self.seed()
        officer = User.objects.get(role__role_name=Role.OFFICER)
        self.assertFalse(Child.objects.filter(social_worker=officer).exists())
        self.assertFalse(Child.objects.filter(social_worker__isnull=True).exists())
        self.assertEqual(
            {Role.STAFF},
            set(Child.objects.values_list("social_worker__role__role_name", flat=True)))

    def test_it_signs_in_and_sees_every_seeded_child(self):
        self.seed()
        client = APIClient()
        res = client.post("/api/auth/login/", {"email": "c.dizon@racco1.gov.ph",
                                               "password": "demo1234"}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + res.data["access"])
        self.assertEqual(Child.objects.count(), len(client.get("/api/children/").data))


class TheDealingIsToStaffAloneTest(TestCase):
    def setUp(self):
        self.officer = User.objects.create_user(
            email="o@racco1.gov.ph", username="o", password="pass1234",
            role=Role.objects.get_or_create(role_name=Role.OFFICER)[0])
        self.children = [Child.objects.create(fullname=f"Child {n}") for n in range(5)]

    def test_an_officer_present_is_never_dealt_a_child(self):
        for name in ("Editha", "Rosa"):
            User.objects.create_user(
                email=f"{name}@racco1.gov.ph", username=name, password="x",
                role=Role.objects.get_or_create(role_name=Role.STAFF)[0])
        self.assertEqual(5, demo_owners.assign_social_workers(self.children))
        self.assertFalse(Child.objects.filter(social_worker=self.officer).exists())
        self.assertNotIn(self.officer, demo_owners.active_staff())

    def test_with_only_an_officer_nobody_is_dealt_anything(self):
        self.assertEqual(0, demo_owners.assign_social_workers(self.children))
        self.assertFalse(Child.objects.filter(social_worker__isnull=False).exists())
