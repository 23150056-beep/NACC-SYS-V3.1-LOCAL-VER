"""The role lists (accounts/capabilities.py), the guard that keeps them the only
place a role is enumerated, and the authentication backstop for a role the code
does not know.

A role decision is a list read through the module, so that adding a role is one
edit to the lists it belongs in, and a role in no list is refused. Before the
lists, a decision was a tuple or an if/else at each call site, and an account
whose role the code did not name fell into whichever branch came last.
"""
import ast
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from accounts import capabilities
from accounts.capabilities import may_close_case, may_hold_records, works_record
from accounts.models import Role
from accounts.tests.probe import PROBE

User = get_user_model()

BACKEND = Path(__file__).resolve().parents[2]
ROLE_NAMES = set(Role.NAMES)
ROLE_ATTRS = {"ADMINISTRATOR", "PSYCHOLOGIST", "STAFF", "OFFICER"}

# The one place besides capabilities.py that enumerates roles: Role.NAMES,
# "the role rows that must exist" (not who may use the system, which is
# capabilities.EVERY_ROLE). Exempted by name and place, nothing else is.
ROLE_NAMES_TUPLE = ("accounts/models.py", "Role", "NAMES")


def _is_role_names_tuple(rel, tree, node):
    """Is this literal the value of `NAMES = (...)` inside `class Role` in
    accounts/models.py?"""
    if rel.as_posix() != ROLE_NAMES_TUPLE[0]:
        return False
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == ROLE_NAMES_TUPLE[1]:
            for stmt in cls.body:
                if (isinstance(stmt, ast.Assign) and stmt.value is node
                        and any(isinstance(t, ast.Name) and t.id == ROLE_NAMES_TUPLE[2]
                                for t in stmt.targets)):
                    return True
    return False

A, P, S = Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF


class TheListsHoldExactlyTheirMembers(SimpleTestCase):
    def test_members(self):
        c = capabilities
        self.assertEqual((A, P, S), c.EVERY_ROLE)
        self.assertEqual((A,), c.SEES_EVERY_RECORD)
        self.assertEqual((A, S), c.KEEPS_RECORDS)
        self.assertEqual((S,), c.SOCIAL_WORK)
        self.assertEqual((A,), c.OVERSEES_CASEWORK)
        self.assertEqual((S,), c.HOLDS_RECORDS)
        self.assertEqual((P, S), c.CLAIMABLE_AT_SIGNUP)
        self.assertEqual((P, S), c.SIGNS_IN_WITH_GOOGLE)
        self.assertEqual((A, P), c.WRITES_CLINICAL)
        self.assertEqual((A, P), c.MANAGES_AVAILABILITY)
        self.assertEqual((A, P), c.INSTRUMENT_MANAGERS)
        self.assertEqual((A, P, S), c.RECORDS_OWN_SESSION_OUTCOMES)

    def test_what_may_sign_up_or_use_google_is_a_subset_of_who_may_use_the_system(self):
        """The Google door refuses an unknown role by SIGNS_IN_WITH_GOOGLE
        alone, before the backstop could; that only holds while it is a subset."""
        every = set(capabilities.EVERY_ROLE)
        self.assertLessEqual(set(capabilities.SIGNS_IN_WITH_GOOGLE), every)
        self.assertLessEqual(set(capabilities.CLAIMABLE_AT_SIGNUP), every)

    def test_every_list_is_an_immutable_tuple(self):
        for name in dir(capabilities):
            if name.isupper():
                self.assertIsInstance(getattr(capabilities, name), tuple, name)


def _python_files():
    skip = {"tests", "migrations", ".venv", "venv", "node_modules", "__pycache__",
            "site-packages", "staticfiles"}
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if skip & set(rel.parts) or rel.as_posix() == "accounts/capabilities.py":
            continue
        yield path, rel


def _role_refs(node):
    """Role references directly inside a literal's elements."""
    refs = []
    for el in node.elts:
        if (isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name)
                and el.value.id == "Role" and el.attr in ROLE_ATTRS):
            refs.append(f"Role.{el.attr}")
        elif isinstance(el, ast.Constant) and el.value in ROLE_NAMES:
            refs.append(repr(el.value))
    return refs


CAPABILITIES = "accounts.capabilities"


def _absolute_module(node, rel):
    """The module an ImportFrom names, a relative import resolved against the
    file's own package (`from .capabilities import X` in accounts/)."""
    if not node.level:
        return node.module or ""
    package = list(rel.parts[:-1])
    base = package[:len(package) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def _uppercase_imports(tree, rel):
    """`from accounts.capabilities import KEEPS_RECORDS`, or `import *`, a
    relative form included: each binds a list as it was at import."""
    return [f"{rel.as_posix()}:{node.lineno} {alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and _absolute_module(node, rel) == CAPABILITIES
            for alias in node.names if alias.name.isupper() or alias.name == "*"]


def _module_names(tree, rel):
    """The names this file binds to the capabilities module (`capabilities`,
    or an alias such as `caps`), and whether it reads the module through the
    dotted `accounts.capabilities`."""
    names, dotted = set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _absolute_module(node, rel) == "accounts":
            names |= {a.asname or a.name for a in node.names if a.name == "capabilities"}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == CAPABILITIES:
                    if alias.asname:
                        names.add(alias.asname)
                    else:
                        dotted = True
    return names, dotted


def _list_named(node, names, dotted):
    """The list a node reads (`capabilities.X`, `caps.X` or
    `accounts.capabilities.X`), or None."""
    if not (isinstance(node, ast.Attribute) and node.attr.isupper()):
        return None
    owner = node.value
    if isinstance(owner, ast.Name) and owner.id in names:
        return node.attr
    if (dotted and isinstance(owner, ast.Attribute) and owner.attr == "capabilities"
            and isinstance(owner.value, ast.Name) and owner.value.id == "accounts"):
        return node.attr
    return None


_TRY = tuple(t for t in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if t)
_MATCH = getattr(ast, "Match", ())


def _at_import(body):
    """The expressions Python evaluates while it imports a module: every
    statement at module level and in a class body, through if, try, with, for,
    while and match blocks, and each function's decorators and default values;
    never a function's body, which runs when it is called."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from node.decorator_list
            yield from node.args.defaults
            yield from (d for d in node.args.kw_defaults if d is not None)
        elif isinstance(node, ast.ClassDef):
            yield from node.decorator_list
            yield from node.bases
            yield from (k.value for k in node.keywords)
            yield from _at_import(node.body)
        elif isinstance(node, (ast.If, ast.While)):
            yield node.test
            yield from _at_import(node.body)
            yield from _at_import(node.orelse)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            yield node.iter
            yield from _at_import(node.body)
            yield from _at_import(node.orelse)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            yield from (item.context_expr for item in node.items)
            yield from _at_import(node.body)
        elif isinstance(node, _TRY):
            yield from _at_import(node.body)
            for handler in node.handlers:
                if handler.type is not None:
                    yield handler.type
                yield from _at_import(handler.body)
            yield from _at_import(node.orelse)
            yield from _at_import(node.finalbody)
        elif isinstance(node, _MATCH):
            yield node.subject
            for case in node.cases:
                if case.guard is not None:
                    yield case.guard
                yield from _at_import(case.body)
        else:
            yield from (n for n in ast.iter_child_nodes(node) if isinstance(n, ast.expr))


def _eager(expr):
    """Every node evaluated along with the expression: ast.walk, except that a
    lambda's body waits until the lambda is called (its defaults do not)."""
    stack = [expr]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.Lambda):
            stack.extend(node.args.defaults)
            stack.extend(d for d in node.args.kw_defaults if d is not None)
        else:
            stack.extend(ast.iter_child_nodes(node))


def _import_time_reads(tree, rel):
    """Every list a module reads while it is being imported: a copy frozen
    before any test can widen the list."""
    names, dotted = _module_names(tree, rel)
    if not (names or dotted):
        return []
    return [f"{rel.as_posix()}:{node.lineno} {name}"
            for expr in _at_import(tree.body) for node in _eager(expr)
            if (name := _list_named(node, names, dotted))]


class RoleListsAreNotWrittenAnywhereElse(SimpleTestCase):
    """The guard: a role list is read from accounts.capabilities, never typed."""

    def test_no_literal_holds_two_or_more_roles(self):
        found = []
        for path, rel in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    refs = _role_refs(node)
                    if _is_role_names_tuple(rel, tree, node):
                        continue
                    if len(refs) >= 2:
                        found.append(f"{rel}:{node.lineno} {refs}")
        self.assertEqual([], found,
                         "name the list once in accounts/capabilities.py and read it from there")

    def test_role_names_is_the_four_rows_and_not_who_may_sign_in(self):
        self.assertEqual((A, P, S, "Officer"), Role.NAMES)
        self.assertEqual("Officer", Role.OFFICER)
        # The rows that exist are not who may use the system (until WP3).
        self.assertNotIn(Role.OFFICER, capabilities.EVERY_ROLE)

    def test_the_exemption_is_only_role_names(self):
        src = "class Role:\n    NAMES = (A, B)\n    OTHER = (A, B)\n"
        tree = ast.parse(src)
        tuples = [n for n in ast.walk(tree) if isinstance(n, ast.Tuple)]
        rel = Path("accounts/models.py")
        self.assertEqual([True, False], [_is_role_names_tuple(rel, tree, n) for n in tuples])
        self.assertFalse(_is_role_names_tuple(Path("accounts/other.py"), tree, tuples[0]))

    def test_lists_are_read_through_the_module(self):
        found = []
        for path, rel in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            found += _uppercase_imports(tree, rel)
        self.assertEqual([], found,
                         "use `from accounts import capabilities`, so a test can widen a list")

    def test_a_list_is_not_read_while_the_module_is_imported(self):
        """`X = capabilities.KEEPS_RECORDS` at module or class level - or in an
        if or try block there, a function's default, or under an alias - freezes
        the list when the module loads, so a test cannot widen it."""
        found = []
        for path, rel in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            found += _import_time_reads(tree, rel)
        self.assertEqual([], found, "read the list where it is used, not at import")

    def test_the_import_time_guard_does_catch_a_copy(self):
        source = textwrap.dedent("""\
            from accounts import capabilities
            from accounts import capabilities as caps
            import accounts.capabilities
            A = capabilities.KEEPS_RECORDS
            B = caps.SOCIAL_WORK
            C = accounts.capabilities.HOLDS_RECORDS
            if True:
                D = capabilities.EVERY_ROLE
            try:
                E = capabilities.SEES_EVERY_RECORD
            except ImportError:
                F = capabilities.OVERSEES_CASEWORK
            def f(roles=capabilities.WRITES_CLINICAL, *, more=caps.MANAGES_AVAILABILITY):
                return capabilities.KEEPS_RECORDS
            class K:
                G = capabilities.INSTRUMENT_MANAGERS
                def m(self, roles=caps.CLAIMABLE_AT_SIGNUP):
                    def inner(r=capabilities.SIGNS_IN_WITH_GOOGLE):
                        return r
                    return inner
            H = lambda r=capabilities.RECORDS_OWN_SESSION_OUTCOMES: r in capabilities.KEEPS_RECORDS
            fine = capabilities.works_record
            """)
        found = sorted(entry.split(" ", 1)[1] for entry in
                       _import_time_reads(ast.parse(source), Path("x/y.py")))
        # Not caught, rightly: f's body, inner's default (it is defined when
        # m runs), the lambda's body, and a function of the module.
        self.assertEqual(sorted([
            "KEEPS_RECORDS", "SOCIAL_WORK", "HOLDS_RECORDS", "EVERY_ROLE",
            "SEES_EVERY_RECORD", "OVERSEES_CASEWORK", "WRITES_CLINICAL",
            "MANAGES_AVAILABILITY", "INSTRUMENT_MANAGERS", "CLAIMABLE_AT_SIGNUP",
            "RECORDS_OWN_SESSION_OUTCOMES"]), found)

    def test_a_relative_import_is_resolved(self):
        tree = ast.parse("from . import capabilities as caps\n"
                         "from .capabilities import KEEPS_RECORDS\n"
                         "X = caps.SOCIAL_WORK\n"
                         "from accounts.capabilities import *\n")
        rel = Path("accounts/views.py")
        self.assertEqual(["accounts/views.py:3 SOCIAL_WORK"], _import_time_reads(tree, rel))
        self.assertEqual(["accounts/views.py:2 KEEPS_RECORDS", "accounts/views.py:4 *"],
                         _uppercase_imports(tree, rel))
        deep = ast.parse("from ...capabilities import EVERY_ROLE\n")
        self.assertEqual(["accounts/management/commands/x.py:1 EVERY_ROLE"],
                         _uppercase_imports(deep, Path("accounts/management/commands/x.py")))

    def test_the_guard_does_catch_a_list(self):
        tree = ast.parse("x = (Role.STAFF, 'Psychologist')\ny = [Role.STAFF]\n")
        counts = [len(_role_refs(n)) for n in ast.walk(tree)
                  if isinstance(n, (ast.Tuple, ast.List))]
        self.assertEqual([2, 1], counts)


FRONTEND_SRC = BACKEND.parent / "frontend" / "src"
ROLES_JS_PATH = FRONTEND_SRC / "config" / "roles.js"


def _js_role_arrays(source, role_names=None):
    return _js_scan(source, role_names)[0]


def _js_role_strings(source, role_names=None):
    """Every string literal in this JavaScript that is a role name, wherever it
    sits: a list of (line, text). Same scanner as _js_role_arrays."""
    return _js_scan(source, role_names)[1]


def _js_scan(source, role_names=None):
    """Where an array literal in this JavaScript holds two or more strings that
    are role names: a list of (line of its `[`, the role names it holds).

    A scanner, not a parser: it follows quotes, template literals, comments
    and regular expressions well enough to count the string literals sitting
    directly inside each `[ ]` (never those in a call or an object inside it).
    A quote that is not closed on its own line is not a string - an apostrophe
    in JSX text must not swallow the rest of the file. Multi-line arrays count.
    An array holding one role name among other words (a table heading) passes.
    Role names are read from capabilities.EVERY_ROLE when called, so a role
    added there is counted at once.

    What it does NOT see (WP5 inventory): a spread of single-role arrays
    (`[...['Staff'], ...['Administrator']]`), a template literal with `${}`,
    role-keyed objects, `||` chains of role comparisons, `!==` negations, and
    arrays assembled from constants.
    """
    role_names = set(capabilities.EVERY_ROLE) if role_names is None else set(role_names)
    found = []
    strings = []          # (line, text) of every role-name string literal
    stack = []            # frames: [kind, line, role strings]
    i, n, line = 0, len(source), 1
    last = ""             # last significant character, to tell a regex from a division

    def top_string(text):
        if text in role_names:
            strings.append((line, text))
            if stack and stack[-1][0] == "[":
                stack[-1][2].append(text)

    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif source.startswith("//", i):
            while i < n and source[i] != "\n":
                i += 1
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += source.count("\n", i, end)
            i = end
        elif c in "'\"":
            j = i + 1
            while j < n and source[j] != c and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            if j < n and source[j] == c:
                top_string(source[i + 1:j])
                i, last = j + 1, c
            else:
                i += 1    # an apostrophe in text, not a string
        elif c == "`":
            j = i + 1
            while j < n and source[j] != "`":
                j += 2 if source[j] == "\\" else 1
            if "${" not in source[i + 1:j]:
                top_string(source[i + 1:j])    # a plain template literal is a string
            line += source.count("\n", i, j)
            i, last = j + 1, c
        elif c == "/" and (last in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";")
                           or re.search(r"\b(?:return|typeof|case|in|of|delete|void|throw)\s*$",
                                        source[max(0, i - 12):i])):
            j = i + 1
            in_class = False
            while j < n and source[j] != "\n" and (in_class or source[j] != "/"):
                if source[j] == "\\":
                    j += 1
                elif source[j] == "[":
                    in_class = True
                elif source[j] == "]":
                    in_class = False
                j += 1
            i, last = (j + 1 if j < n and source[j] == "/" else j), "/"
        else:
            if c in "[({":
                stack.append([c, line, []])
            elif c in "])}" and stack:
                kind, start, roles = stack.pop()
                if kind == "[" and len(roles) >= 2:
                    found.append((start, roles))
            last = c
            i += 1
    return found, strings


_ROLES_JS_SHAPE = re.compile(r"^export const [A-Z][A-Z_]* = \['[A-Za-z ]+'(?:, '[A-Za-z ]+')*\];$")


def _roles_js_off_shape(source, role_names=None):
    """What in roles.js holds a role name outside the one statement shape the
    pin reads: (line, what) for every role array of two or more that starts on
    a line the shape does not match, and every role-name string literal on such
    a line - so a single-role list in another shape is seen too."""
    lines = source.split("\n")
    on_shape = lambda n: bool(_ROLES_JS_SHAPE.match(lines[n - 1]))
    off = [(n, roles) for n, roles in _js_role_arrays(source, role_names) if not on_shape(n)]
    off += [(n, text) for n, text in _js_role_strings(source, role_names) if not on_shape(n)]
    return off


def _frontend_files():
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix in (".js", ".jsx") and path != ROLES_JS_PATH and "node_modules" not in path.parts:
            yield path


class RoleListsAreNotWrittenInTheBrowserEither(SimpleTestCase):
    """The frontend half of the guard: a screen reads a list from
    frontend/src/config/roles.js and never types a role array of its own."""

    def test_no_array_in_the_frontend_holds_two_or_more_roles(self):
        found = []
        for path in _frontend_files():
            for line, roles in _js_role_arrays(path.read_text(encoding="utf-8-sig")):
                found.append(f"{path.relative_to(FRONTEND_SRC.parent).as_posix()}:{line} {roles}")
        self.assertEqual([], found,
                         "name the list once in accounts/capabilities.py and "
                         "frontend/src/config/roles.js and read it from there")

    def test_roles_js_holds_no_list_but_the_pinned_statement_shape(self):
        """roles.js is exempt from the scan above, so it is held to the one
        shape the pin reads: a role list there in any other shape (Set,
        Object.freeze, let, a non-exported const, a camelCase name, a single
        role written some other way) is a list the pin cannot see."""
        source = ROLES_JS_PATH.read_text(encoding="utf-8-sig")
        self.assertEqual([], _roles_js_off_shape(source),
                         "a role list in roles.js must be one "
                         "`export const NAME = ['Administrator', 'Staff'];` statement")

    def test_the_roles_js_shape_check_does_catch_another_shape(self):
        roles = {"Administrator", "Psychologist", "Staff"}
        source = textwrap.dedent("""\
            export const GOOD = ['Administrator', 'Staff'];
            export const ONE = ['Staff'];
            // a comment naming 'Staff' and 'Administrator' is not a list
            export const SETTY = new Set(['Administrator', 'Staff']);
            export const FROZEN = Object.freeze(['Administrator', 'Staff']);
            export let LET_LIST = ['Administrator', 'Staff'];
            const HIDDEN = ['Administrator', 'Staff'];
            export const officeRoles = ['Administrator', 'Staff'];
            export const LONE = Object.freeze(['Psychologist']);
            export const SPLIT = [
              'Administrator', 'Staff'];
            export function f(u) { return u.role_name === 'Staff'; }
            """)
        self.assertEqual(
            [4, 5, 6, 7, 8, 9, 10, 11, 12],
            sorted({n for n, _ in _roles_js_off_shape(source, roles)}))

    def test_the_frontend_guard_has_files_to_scan(self):
        self.assertTrue(ROLES_JS_PATH.exists())
        self.assertGreater(len(list(_frontend_files())), 20)

    def test_the_frontend_guard_does_catch_a_list(self):
        source = textwrap.dedent("""\
            const a = ['Administrator', 'Staff'];
            const b = [
              "Staff",
              // a comment, then another
              'Psychologist',
            ];
            const c = ['Psychologist', child.psychologist_name];
            const d = ['Date', 'Status', 'Psychologist'].map((h) => h);
            const e = [f('Staff', 'Psychologist')];
            const g = [{ role: 'Staff' }, { role: 'Psychologist' }];
            const h = { roles: ['Staff', 'Administrator', 'Psychologist'] };
            const i = x['Staff'];
            const j = [/['"]/, 'Staff'];
            const k = ['SW (Staff)', 'Psychologist'];
            const l = <p>Don't use 'Staff' here, it's ['Administrator']</p>;
            // const m = ['Staff', 'Administrator'];
            const n = `['Staff', 'Administrator']`;
            function o(s) { return /[",]/.test(s) ? 1 : 2; }
            const p = ['Staff', 'Administrator'];
            const q = [`Staff`, `Administrator`];
            const r = [`Staff`, `${x}`];
            """)
        self.assertEqual(
            [(1, ["Administrator", "Staff"]), (2, ["Staff", "Psychologist"]),
             (11, ["Staff", "Administrator", "Psychologist"]),
             (19, ["Staff", "Administrator"]), (20, ["Staff", "Administrator"])],
            sorted(_js_role_arrays(source, {"Administrator", "Psychologist", "Staff"})))
        # a role the capabilities gain later counts the moment it is there
        self.assertEqual([(1, ["Officer", "Staff"])],
                         _js_role_arrays("const s = ['Officer', 'Staff'];", {"Officer", "Staff"}))


def _user(role_name, pk=1):
    role = SimpleNamespace(role_name=role_name) if role_name else None
    return SimpleNamespace(id=pk, role=role)


class TheFunctions(SimpleTestCase):
    def test_works_record_is_the_social_workers_own_record(self):
        child = SimpleNamespace(social_worker_id=7)
        self.assertTrue(works_record(_user(S, 7), child))
        self.assertFalse(works_record(_user(S, 8), child))
        self.assertFalse(works_record(_user(A, 7), child))
        self.assertFalse(works_record(_user(P, 7), child))
        self.assertFalse(works_record(_user("Probe", 7), child))
        self.assertFalse(works_record(_user(None, 7), child))

    def test_a_record_nobody_holds_is_not_worked_by_anyone(self):
        self.assertFalse(works_record(_user(S, 7), SimpleNamespace(social_worker_id=None)))

    def test_works_record_follows_the_list(self):
        child = SimpleNamespace(social_worker_id=7)
        with patch.object(capabilities, "SOCIAL_WORK", (S, "Probe")):
            self.assertTrue(works_record(_user("Probe", 7), child))
        self.assertFalse(works_record(_user("Probe", 7), child))

    def test_may_hold_records(self):
        self.assertTrue(may_hold_records(_user(S)))
        for name in (A, P, "Probe", None):
            self.assertFalse(may_hold_records(_user(name)), name)
        self.assertFalse(may_hold_records(SimpleNamespace(role=None)))
        self.assertFalse(may_hold_records(None))

    def test_may_close_case(self):
        child = SimpleNamespace(assigned_psychologist_id=3)
        ask = lambda role, pk: SimpleNamespace(user=_user(role, pk))      # noqa: E731
        self.assertTrue(may_close_case(ask(A, 1), child))
        self.assertTrue(may_close_case(ask(P, 3), child))
        self.assertFalse(may_close_case(ask(P, 4), child))
        self.assertFalse(may_close_case(ask(S, 3), child))
        self.assertFalse(may_close_case(ask("Probe", 3), child))
        self.assertFalse(may_close_case(ask(None, 3), child))


class AnAccountWhoseRoleTheCodeDoesNotKnow(APITestCase):
    """The backstop: refused at the authentication door, at sign-in and at
    refresh, one helper for the rule (accounts.token_claims.refuse_unknown_role)."""

    PASSWORD = "Sup3r-secret-2026"

    def _account(self, role_name, email):
        role = Role.objects.get_or_create(role_name=role_name)[0] if role_name else None
        return User.objects.create_user(email=email, username=email.split("@")[0],
                                        password=self.PASSWORD, role=role)

    def _login(self, email):
        return self.client.post("/api/auth/login/", {"email": email, "password": self.PASSWORD})

    def test_the_three_roles_are_unaffected(self):
        for name in (A, P, S):
            email = f"{name.lower()}@racco1.gov.ph"
            self._account(name, email)
            res = self._login(email)
            self.assertEqual(200, res.status_code, name)
            self.client.credentials(HTTP_AUTHORIZATION="Bearer " + res.data["access"])
            self.assertEqual(200, self.client.get("/api/auth/me/").status_code, name)
            self.client.credentials()
            refreshed = self.client.post("/api/auth/refresh/", {"refresh": res.data["refresh"]})
            self.assertEqual(200, refreshed.status_code, name)

    def test_an_unknown_role_is_refused_at_me_with_its_own_code(self):
        user = self._account(S, "later@racco1.gov.ph")
        access = self._login("later@racco1.gov.ph").data["access"]
        user.role = Role.objects.create(role_name="Probe")
        user.save(update_fields=["role"])
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        res = self.client.get("/api/auth/me/")
        self.assertEqual(401, res.status_code)
        self.assertEqual("unknown_role", res.data["detail"].code)
        self.assertIn("not set up in this version", str(res.data["detail"]))
        self.assertEqual("unknown_role", res.json()["code"])

    def test_an_unknown_role_cannot_sign_in(self):
        self._account("Probe", "probe@racco1.gov.ph")
        res = self._login("probe@racco1.gov.ph")
        self.assertEqual(401, res.status_code)
        self.assertNotIn("access", res.data)
        self.assertNotIn("refresh", res.data)
        self.assertEqual("unknown_role", res.data["detail"].code)

    def test_the_sign_in_refusal_carries_its_code_in_the_body(self):
        """The sign-in page reads `code` from the body to show the server's
        sentence instead of "Invalid username or password."."""
        from accounts.token_claims import UNKNOWN_ROLE
        self._account("Probe", "bodycode@racco1.gov.ph")
        res = self._login("bodycode@racco1.gov.ph")
        self.assertEqual(401, res.status_code)
        body = res.json()
        self.assertEqual({"detail": UNKNOWN_ROLE, "code": "unknown_role"}, body)

    def test_a_wrong_password_body_is_unchanged(self):
        self._account(S, "wrongpw@racco1.gov.ph")
        res = self.client.post("/api/auth/login/", {"email": "wrongpw@racco1.gov.ph",
                                                   "password": "not-the-password"})
        self.assertEqual(401, res.status_code)
        body = res.json()
        self.assertEqual(["detail"], list(body))
        self.assertNotIn("code", body)
        # the same answer an address nobody holds gets
        other = self.client.post("/api/auth/login/", {"email": "nobody@racco1.gov.ph",
                                                     "password": "not-the-password"})
        self.assertEqual(body, other.json())

    def test_an_unknown_role_cannot_renew_a_session(self):
        user = self._account(S, "renew@racco1.gov.ph")
        refresh = self._login("renew@racco1.gov.ph").data["refresh"]
        user.role = Role.objects.create(role_name="Probe")
        user.save(update_fields=["role"])
        res = self.client.post("/api/auth/refresh/", {"refresh": refresh})
        self.assertEqual(401, res.status_code)
        self.assertNotIn("access", res.data)

    def test_a_role_the_code_is_widened_to_admit_is_let_in(self):
        self._account("Probe", "wide@racco1.gov.ph")
        with patch.object(capabilities, "EVERY_ROLE", capabilities.EVERY_ROLE + ("Probe",)):
            res = self._login("wide@racco1.gov.ph")
            self.assertEqual(200, res.status_code)
            self.client.credentials(HTTP_AUTHORIZATION="Bearer " + res.data["access"])
            self.assertEqual(200, self.client.get("/api/auth/me/").status_code)

    def test_no_role_is_still_its_own_refusal(self):
        user = self._account(S, "none@racco1.gov.ph")
        access = self._login("none@racco1.gov.ph").data["access"]
        user.role = None
        user.save(update_fields=["role"])
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        res = self.client.get("/api/auth/me/")
        self.assertEqual(401, res.status_code)
        self.assertEqual("no_role", res.data["detail"].code)
        from accounts.authentication import NO_ROLE
        self.assertEqual({"detail": NO_ROLE, "code": "no_role"}, res.json())


GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
VERIFY = "accounts.google_auth.id_token.verify_oauth2_token"


def _google_claims(email):
    return {"iss": "https://accounts.google.com", "aud": GOOGLE_CLIENT_ID,
            "sub": f"sub-{email}", "email": email, "email_verified": True}


@override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
class AClaimReadsTheClaimList(APITestCase):
    """What a stranger may ASK FOR (CLAIMABLE_AT_SIGNUP) and who may SIGN IN
    with Google (SIGNS_IN_WITH_GOOGLE) hold the same roles today, and the plan
    puts the Officer in the second only. Every door that records a claim must
    therefore read the first. Each list is widened alone here, with a role row
    the code does not know, so the tests can tell which list a door reads."""

    def setUp(self):
        cache.clear()    # the sign-up counters live in the process cache
        for name in (A, P, S, PROBE):
            Role.objects.get_or_create(role_name=name)
        self.probe_role = Role.objects.get(role_name=PROBE)

    def _widen(self, name):
        p = patch.object(capabilities, name, getattr(capabilities, name) + (PROBE,))
        p.start()
        self.addCleanup(p.stop)

    def _google(self, email, requested_role=None):
        body = {"credential": "tok"}
        if requested_role is not None:
            body["requested_role"] = requested_role
        with patch(VERIFY, return_value=_google_claims(email)):
            return self.client.post("/api/auth/google/", body, format="json")

    def _typed(self, email, requested_role):
        return self.client.post("/api/auth/signup/", {
            "first_name": "Maria", "last_name": "Santos", "email": email,
            "password": "a-long-enough-passphrase-9", "requested_role": requested_role},
            format="json")

    def _asked(self, email):
        return User.objects.get(email=email).requested_role

    def test_a_new_google_request_cannot_claim_a_role_that_only_signs_in(self):
        self._widen("SIGNS_IN_WITH_GOOGLE")
        res = self._google("new@gmail.com", PROBE)
        self.assertEqual(403, res.status_code)
        self.assertTrue(res.data["role_required"])
        self.assertIsNone(self._asked("new@gmail.com"))

    def test_a_pending_google_users_later_claim_is_ignored(self):
        self._widen("SIGNS_IN_WITH_GOOGLE")
        self._google("later@gmail.com")              # a request with no role stated
        res = self._google("later@gmail.com", PROBE)
        self.assertEqual(403, res.status_code)
        self.assertTrue(res.data["role_required"])
        self.assertIsNone(self._asked("later@gmail.com"))

    def test_the_control_widening_the_claim_list_records_the_google_claim(self):
        self._widen("CLAIMABLE_AT_SIGNUP")
        self._google("new@gmail.com", PROBE)
        self.assertEqual(self.probe_role, self._asked("new@gmail.com"))
        self._google("later@gmail.com")
        self._google("later@gmail.com", PROBE)
        self.assertEqual(self.probe_role, self._asked("later@gmail.com"))

    def test_the_typed_form_refuses_a_role_that_only_signs_in(self):
        self._widen("SIGNS_IN_WITH_GOOGLE")
        res = self._typed("typed@gmail.com", PROBE)
        self.assertEqual(400, res.status_code)
        self.assertIn("requested_role", res.data)
        self.assertFalse(User.objects.filter(email="typed@gmail.com").exists())

    def test_the_control_widening_the_claim_list_records_the_typed_claim(self):
        self._widen("CLAIMABLE_AT_SIGNUP")
        res = self._typed("typed@gmail.com", PROBE)
        self.assertEqual(202, res.status_code, res.data)
        self.assertEqual(self.probe_role, self._asked("typed@gmail.com"))
