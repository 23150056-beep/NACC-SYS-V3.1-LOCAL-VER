"""One name for a person, and never their email address.

The rule this file exists for is the last test. `USERNAME_FIELD` on this model
is "email", so `get_username()` returns an email address - and four places used
it as their name fallback: the slot grid, the next-slots strip, the per-child
presence list and the chatbot's availability answer. A psychologist with no
first or last name therefore had their email published in an API response.
Nobody chose that; it is what the Django default means on a model that signs in
by email, and `or person.get_username()` reads like a perfectly reasonable
thing to write, which is why it needs a test rather than a comment.

Note that `fullname` is a PROPERTY composed from the name columns, not a field
of its own - so "a user with no fullname" is a user with no first, middle or
last name, and that is how these fixtures are built.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.display import display_name
from accounts.models import Role

User = get_user_model()


class DisplayNameTest(TestCase):
    def setUp(self):
        self.role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.made = 0

    def _user(self, first="", last="", username=None):
        # Both email and username are unique, and some of these tests want
        # two users at once. Pass username="" for a user who has neither a
        # name nor a username - only one of those per test.
        self.made += 1
        return User.objects.create_user(
            email=f"p{self.made}@racco1.gov.ph",
            username=f"pcruz{self.made}" if username is None else username,
            password="pass1234", first_name=first, last_name=last,
            role=self.role)

    def test_it_prefers_the_name_the_person_entered(self):
        self.assertEqual("Paz Cruz", display_name(self._user("Paz", "Cruz")))

    def test_it_falls_back_to_the_username(self):
        user = self._user()
        self.assertEqual(user.username, display_name(user))

    def test_the_caller_chooses_what_shows_when_there_is_neither(self):
        nameless = self._user(username="")
        self.assertEqual("", display_name(nameless))
        self.assertEqual("System", display_name(nameless, "System"))

    def test_a_fallback_is_not_used_when_there_is_a_name(self):
        self.assertEqual("Paz Cruz", display_name(self._user("Paz", "Cruz"), "System"))
        named_only_by_username = self._user()
        self.assertEqual(named_only_by_username.username,
                         display_name(named_only_by_username, "System"))

    def test_it_tolerates_none(self):
        """Genuinely occurs: an unassigned child, a system-generated log row."""
        self.assertEqual("", display_name(None))
        self.assertEqual("there", display_name(None, "there"))

    def test_it_never_returns_an_email_address(self):
        """The whole point: get_username() is the email on this model."""
        nameless = self._user(username="")
        self.assertEqual(nameless.email, nameless.get_username(),
                         "USERNAME_FIELD is no longer the email - re-read the "
                         "note above before relaxing anything here")
        self.assertNotIn("@", display_name(nameless, "Psychologist"))
        # Same account once it has a name: still no address anywhere in it.
        nameless.first_name, nameless.last_name = "Paz", "Cruz"
        self.assertEqual("Paz Cruz", display_name(nameless))
