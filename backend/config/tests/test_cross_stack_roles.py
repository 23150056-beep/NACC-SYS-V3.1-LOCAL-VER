"""Role lists that exist on both sides of the wire, pinned to each other.

Every list in accounts/capabilities.py is written twice - once there, once in
frontend/src/config/roles.js under the same name - because the browser cannot
import a Python tuple. That duplication is unavoidable. What is avoidable is
nobody noticing when the two stop agreeing.

The failure it produces is quiet in the worst direction. Add a role to the
frontend list only, and the app offers a screen to someone the API will
refuse: a menu entry that 403s. Remove one from the frontend only, and a role
that IS permitted loses the screen, with nothing in any log to say why. The
instrument list carries a "TO REVERT to admin-only" comment inviting exactly
that edit, and invites it in only one file.

Reading the JavaScript is crude and deliberately so - a regex over one fixed
statement shape, not a parser: `export const NAME = ['Administrator', 'Staff'];`.
If the shape of that file changes enough to defeat this, the test says so by
failing, which is the right outcome for a file this one is pinned to. The
other half of the guard - no role array typed anywhere else in the browser -
is in accounts/tests/test_capabilities.py.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

from accounts import capabilities

ROLES_JS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "config" / "roles.js"

# The one shape a list may take in roles.js.
_LIST = r"export const (?P<name>[A-Z][A-Z_]*) = \[(?P<items>(?:'[A-Za-z ]+'(?:, '[A-Za-z ]+')*)?)\];"
_ANY_LIST_START = re.compile(r"^\s*export\s+const\s+([A-Z][A-Z_0-9]*)\s*=\s*\[", re.M)


def _js_lists(source):
    """Every `export const NAME = ['A', 'B'];` statement, as {name: [items]}."""
    return {m.group("name"): re.findall(r"'([^']+)'", m.group("items"))
            for m in re.finditer(r"^" + _LIST + r"$", source, re.M)}


def _js_list(source, name):
    """The string entries of `export const <name> = [...]`, or None."""
    return _js_lists(source).get(name)


def _backend_lists():
    """Every module-level UPPERCASE non-empty tuple of strings in capabilities.py,
    as {name: sorted members}. Whether the members are real roles is a separate
    test, so a misspelt member fails by name instead of hiding its list."""
    found = {}
    for name in dir(capabilities):
        value = getattr(capabilities, name)
        if (name.isupper() and isinstance(value, tuple) and value
                and all(isinstance(v, str) for v in value)):
            found[name] = sorted(value)
    return found


class RoleListsAgreeAcrossTheWireTest(SimpleTestCase):
    def setUp(self):
        self.assertTrue(ROLES_JS.exists(), f"{ROLES_JS} has moved; this test is pinned to it")
        self.source = ROLES_JS.read_text(encoding="utf-8-sig")
        self.frontend = _js_lists(self.source)

    def test_the_backend_has_lists_to_compare(self):
        self.assertIn("INSTRUMENT_MANAGERS", _backend_lists())
        self.assertIn("EVERY_ROLE", _backend_lists())

    def test_every_list_in_roles_js_has_the_one_shape_the_pin_can_read(self):
        started = _ANY_LIST_START.findall(self.source)
        self.assertEqual(
            sorted(started), sorted(self.frontend),
            "a role list in roles.js is not a single statement of exactly the shape "
            "`export const NAME = ['Administrator', 'Staff'];` (string literals only, "
            "single quotes). If it moved or changed shape, update this test rather "
            "than deleting it - the two lists still have to agree.")

    def test_every_backend_list_is_in_roles_js_with_the_same_members(self):
        for name, members in _backend_lists().items():
            frontend = self.frontend.get(name)
            self.assertIsNotNone(
                frontend,
                f"{name} is in accounts/capabilities.py but roles.js does not export it. "
                "A screen would have to type the array itself.")
            self.assertEqual(
                members, sorted(frontend),
                f"accounts/capabilities.py and frontend/src/config/roles.js disagree "
                f"about {name}. Whichever was edited, the other needs the same edit: "
                "the frontend decides what is OFFERED and the API decides what is "
                "ALLOWED, so a mismatch is either a menu entry that 403s or a screen "
                "missing for somebody entitled to it.")

    def test_roles_js_has_no_list_the_backend_lacks(self):
        extra = sorted(set(self.frontend) - set(_backend_lists()))
        self.assertEqual(
            [], extra,
            "roles.js exports a role list accounts/capabilities.py does not name. "
            "A second list is a second answer; name it in capabilities.py first.")

    def test_every_backend_list_holds_real_roles(self):
        valid = set(capabilities.EVERY_ROLE)
        for list_name, members in _backend_lists().items():
            for name in members:
                self.assertIn(name, valid, f"capabilities.{list_name}: {name!r} is not a role this system has")

    def test_the_names_are_real_roles(self):
        """Guards a typo on either side: 'Psychologists' would pass an
        equality check against itself and match nobody."""
        valid = set(capabilities.EVERY_ROLE)
        for list_name, items in self.frontend.items():
            for name in items:
                self.assertIn(name, valid, f"{list_name}: {name!r} is not a role this system has")

    def test_the_pin_does_read_a_synthetic_file(self):
        source = ("export const A_LIST = ['Administrator', 'Staff'];\n"
                  "export const ONE = ['Staff'];\n"
                  "export const BAD = [\"Staff\", 'Staff'];\n"
                  "export function worksRecord() { return ['Staff']; }\n")
        self.assertEqual({"A_LIST": ["Administrator", "Staff"], "ONE": ["Staff"]}, _js_lists(source))
        self.assertEqual(["A_LIST", "ONE", "BAD"], _ANY_LIST_START.findall(source))
