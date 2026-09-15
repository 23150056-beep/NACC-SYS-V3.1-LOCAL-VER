"""Role lists that exist on both sides of the wire, pinned to each other.

`INSTRUMENT_MANAGER_ROLES` is written twice - once in accounts/permissions.py,
once in frontend/src/config/roles.js - because the browser cannot import a
Python tuple. That duplication is unavoidable. What is avoidable is nobody
noticing when the two stop agreeing.

The failure it produces is quiet in the worst direction. Add a role to the
frontend list only, and the app offers Instruments to someone the API will
refuse: a menu entry that 403s. Remove one from the frontend only, and a role
that IS permitted loses the screen, with nothing in any log to say why. Both
carry a "TO REVERT to admin-only" comment inviting exactly that edit, and both
invite it in only one file.

Reading the JavaScript is crude and deliberately so - a regex over a literal,
not a parser. If the shape of that file changes enough to defeat this, the
test says so by failing, which is the right outcome for a file this one is
pinned to.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from accounts.models import Role
from accounts.permissions import INSTRUMENT_MANAGER_ROLES

ROLES_JS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "config" / "roles.js"


def _js_list(source, name):
    """The string entries of `export const <name> = [...]`."""
    match = re.search(
        r"export\s+const\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\]", source, re.S)
    if match is None:
        return None
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


class InstrumentManagerRolesAgreeAcrossTheWireTest(SimpleTestCase):
    def setUp(self):
        self.assertTrue(ROLES_JS.exists(), f"{ROLES_JS} has moved; this test is pinned to it")
        self.source = ROLES_JS.read_text(encoding="utf-8")

    def test_the_frontend_list_is_still_there_to_compare(self):
        self.assertIsNotNone(
            _js_list(self.source, "INSTRUMENT_MANAGER_ROLES"),
            "INSTRUMENT_MANAGER_ROLES is no longer an array literal in roles.js. "
            "If it moved or changed shape, update this test rather than deleting "
            "it - the two lists still have to agree.")

    def test_both_sides_name_the_same_roles(self):
        frontend = _js_list(self.source, "INSTRUMENT_MANAGER_ROLES")
        backend = list(INSTRUMENT_MANAGER_ROLES)
        self.assertEqual(
            sorted(backend), sorted(frontend),
            "accounts/permissions.py and frontend/src/config/roles.js disagree "
            "about who may manage instruments. Whichever was edited, the other "
            "needs the same edit: the frontend decides what is OFFERED and the "
            "API decides what is ALLOWED, so a mismatch is either a menu entry "
            "that 403s or a screen missing for somebody entitled to it.")

    def test_the_names_are_real_roles(self):
        """Guards a typo on either side: 'Psychologists' would pass an
        equality check against itself and match nobody."""
        valid = {Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF}
        for name in _js_list(self.source, "INSTRUMENT_MANAGER_ROLES"):
            self.assertIn(name, valid, f"{name!r} is not a role this system has")
