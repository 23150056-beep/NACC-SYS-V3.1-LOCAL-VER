from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import AgencyFormTemplate, OpinionnaireInvite

User = get_user_model()

LIST = "/api/opinionnaire-invites/templates/"
INVITES = "/api/opinionnaire-invites/"


def _template(title, owner=None, form_type=AgencyFormTemplate.SELF_REPORT_GOV, active=True):
    return AgencyFormTemplate.objects.create(
        title=title, form_type=form_type, owner=owner, active=active,
        fields=[{"label": "How are you?"}])


class SurveyTemplatesTest(APITestCase):
    """A social worker cannot read /form-templates/, so the survey picker has
    its own door, and invite creation is held to the same list."""

    def setUp(self):
        staff = Role.objects.create(role_name=Role.STAFF)
        psy = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        admin = Role.objects.create(role_name=Role.ADMINISTRATOR)
        mk = lambda n, r: User.objects.create_user(      # noqa: E731
            email=f"{n}@racco1.gov.ph", username=n, password="pass1234", role=r)
        self.sw, self.other_sw = mk("sw", staff), mk("sw2", staff)
        self.psy, self.other_psy = mk("psy", psy), mk("psy2", psy)
        self.admin = mk("adm", admin)
        self.child = Child.objects.create(
            fullname="Ana Reyes", social_worker=self.sw, assigned_psychologist=self.psy)
        self.not_mine = Child.objects.create(
            fullname="Ben Cruz", social_worker=self.other_sw,
            assigned_psychologist=self.other_psy)
        self.shared = _template("Shared form")
        self.hers = _template("Her psychologist's form", owner=self.psy)
        self.private = _template("Someone else's form", owner=self.other_psy)
        self.sw_own = _template("SW's own form", owner=self.sw)
        _template("Inactive form", active=False)
        _template("A consent form", form_type=AgencyFormTemplate.CONSENT)

    def _titles(self, user, child):
        self.client.force_authenticate(user)
        r = self.client.get(LIST, {"child": child.pk})
        self.assertEqual(200, r.status_code, r.data)
        for row in r.data:
            self.assertEqual({"id", "title"}, set(row))
        return {row["title"] for row in r.data}

    def test_a_sw_lists_shared_and_the_childs_psychologists_templates(self):
        self.assertEqual(
            {"Shared form", "Her psychologist's form", "SW's own form"},
            self._titles(self.sw, self.child))

    def test_a_sw_is_not_shown_a_private_template_of_a_psychologist_not_theirs(self):
        self.assertNotIn("Someone else's form", self._titles(self.sw, self.child))

    def test_a_sw_cannot_list_for_another_sws_child(self):
        self.client.force_authenticate(self.sw)
        self.assertIn(self.client.get(LIST, {"child": self.not_mine.pk}).status_code, (403, 404))

    def test_the_child_is_required(self):
        self.client.force_authenticate(self.sw)
        self.assertEqual(400, self.client.get(LIST).status_code)
        self.assertEqual(400, self.client.get(LIST, {"child": "x"}).status_code)

    def test_a_psychologist_gets_shared_and_their_own_only(self):
        self.assertEqual(
            {"Shared form", "Her psychologist's form"},
            self._titles(self.psy, self.child))
        self.client.force_authenticate(self.psy)
        self.assertIn(self.client.get(LIST, {"child": self.not_mine.pk}).status_code, (403, 404))

    def test_the_isa_may_use_any_active_self_report_form(self):
        self.assertEqual(
            {"Shared form", "Her psychologist's form", "Someone else's form",
             "SW's own form"},
            self._titles(self.admin, self.child))

    def test_anonymous_is_refused(self):
        self.assertEqual(401, self.client.get(LIST, {"child": self.child.pk}).status_code)

    def test_a_sw_creates_an_invite_with_an_allowed_template(self):
        self.client.force_authenticate(self.sw)
        for tpl in (self.shared, self.hers):
            r = self.client.post(INVITES, {"child": self.child.pk, "template": tpl.pk})
            self.assertEqual(201, r.status_code, r.data)
        self.assertEqual(2, OpinionnaireInvite.objects.filter(child=self.child).count())

    def test_a_sw_is_refused_a_template_that_is_not_offered(self):
        self.client.force_authenticate(self.sw)
        r = self.client.post(INVITES, {"child": self.child.pk, "template": self.private.pk})
        self.assertEqual(400, r.status_code)
        self.assertIn("template", r.data)
        self.assertEqual(0, OpinionnaireInvite.objects.count())

    def test_a_psychologist_cannot_attach_a_colleagues_private_template(self):
        self.client.force_authenticate(self.psy)
        r = self.client.post(INVITES, {"child": self.child.pk, "template": self.private.pk})
        self.assertEqual(400, r.status_code)
        ok = self.client.post(INVITES, {"child": self.child.pk, "template": self.hers.pk})
        self.assertEqual(201, ok.status_code, ok.data)

    def test_the_isa_creates_an_invite_as_before(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(INVITES, {"child": self.child.pk, "template": self.private.pk})
        self.assertEqual(201, r.status_code, r.data)

    def test_a_sw_cannot_create_an_invite_for_another_sws_child(self):
        self.client.force_authenticate(self.sw)
        r = self.client.post(INVITES, {"child": self.not_mine.pk, "template": self.shared.pk})
        self.assertEqual(403, r.status_code)

    def test_a_template_of_another_type_is_still_refused(self):
        consent = _template("Consent", form_type=AgencyFormTemplate.CONSENT)
        self.client.force_authenticate(self.sw)
        r = self.client.post(INVITES, {"child": self.child.pk, "template": consent.pk})
        self.assertEqual(400, r.status_code)
