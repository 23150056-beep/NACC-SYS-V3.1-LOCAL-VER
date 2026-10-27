"""The catalogue of text messages, and the guard that keeps it complete.

A new SMS feature is a catalogue entry plus a `send_sms(kind=...)` call. These
tests are what make that true rather than hoped: they fail on a message nobody
declared, one that is longer than a segment at its worst, one that Semaphore
would silently discard, one with neither limits nor a reason for having none,
and a call that names a kind the catalogue does not hold. The call sites are
read from the source, the way the chatbot's tool count is pinned, because a
test that only exercises the calls somebody remembered to write misses the one
that was added last week.
"""
import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from accounts import sms_catalogue
from accounts.sms import _GSM7, _GSM7_ESCAPED, fits_one_segment, segments

# Kinds written for a later phase: declared and measured now, called from
# nowhere yet. Anything else with no call site is a dead entry.
NOT_CALLED_YET = ("custodian_reminder_group",)

SENDERS = ("send_sms", "queue_sms")
# The one function allowed to compute a kind instead of writing it out.
KIND_BUILDERS = ("custodian_event_kind",)


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def sender_calls(source):
    """(line, kind-node-or-None) for each send_sms / queue_sms call."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) in SENDERS:
            kind = node.args[2] if len(node.args) >= 3 else None
            for kw in node.keywords:
                if kw.arg == "kind":
                    kind = kw.value
            found.append((node.lineno, kind))
    return found


def problems_in(source):
    """Why each sender call in `source` is not acceptable, as sentences."""
    problems, referenced = [], set()
    for line, kind in sender_calls(source):
        if kind is None:
            problems.append(f"line {line}: no kind is passed")
        elif isinstance(kind, ast.Constant) and isinstance(kind.value, str):
            if kind.value in sms_catalogue.ENTRIES:
                referenced.add(kind.value)
            else:
                problems.append(f"line {line}: {kind.value!r} is not in the catalogue")
        elif isinstance(kind, ast.Call) and _call_name(kind) in KIND_BUILDERS:
            referenced.update(f"custodian_{e}" for e in sms_catalogue.CUSTODIAN_EVENTS)
        else:
            problems.append(f"line {line}: the kind is computed; build it with "
                            f"{' or '.join(KIND_BUILDERS)}")
    return problems, referenced


def backend_sources():
    root = Path(settings.BASE_DIR)
    skip = {"tests", "migrations", ".venv", "venv", "node_modules", "site-packages"}
    for path in sorted(root.rglob("*.py")):
        parts = set(path.relative_to(root).parts)
        if parts & skip or path.name.startswith("test_") or path.name == "tests.py":
            continue
        # The sender itself hands its own `kind` parameter on (queue_sms to
        # send_sms); it is where the check is made, not a caller of it.
        if path == root / "accounts" / "sms.py":
            continue
        yield path


class TheWordingTest(SimpleTestCase):
    def test_every_entry_fits_one_plain_segment_at_its_worst(self):
        for key in sms_catalogue.ENTRIES:
            text = sms_catalogue.render_sample(key)
            filled = text.replace("{otp}", "482913")
            with self.subTest(kind=key):
                self.assertNotIn("{", filled, "an unfilled placeholder")
                self.assertTrue(fits_one_segment(filled), (len(filled), filled))
                self.assertEqual(1, segments(filled), filled)
                self.assertTrue(all(ch in _GSM7 and ch not in _GSM7_ESCAPED
                                    for ch in filled),
                                "not pure GSM-7: " + filled)
                self.assertFalse(filled.lstrip().upper().startswith("TEST"),
                                 "Semaphore discards a message opening with TEST")

    def test_the_samples_are_the_longest_a_value_can_be(self):
        # A pleasant sample would make the length test prove nothing.
        self.assertEqual("C-99999", sms_catalogue.ENTRIES["assignment"].samples["case_ref"])
        self.assertEqual(99, sms_catalogue.ENTRIES["session_reminder"].samples["count"])

    def test_a_code_message_keeps_its_placeholder_for_the_sender(self):
        for key, entry in sms_catalogue.ENTRIES.items():
            if entry.route == sms_catalogue.OTP:
                self.assertIn("{otp}", sms_catalogue.render(key), key)

    def test_the_group_reminder_says_what_the_owner_approved(self):
        text = sms_catalogue.render(
            "custodian_reminder_group", day="tomorrow", date="Wed 30 Sep",
            times="9:00 AM and 10:30 AM", office=sms_catalogue.CUSTODIAN_OFFICE)
        self.assertEqual(
            "NACC RACCO I reminder: the children in your care have appointments "
            "tomorrow, Wed 30 Sep, 9:00 AM and 10:30 AM at the RACCO I office.",
            text)


class TheDeclarationsTest(SimpleTestCase):
    def test_every_entry_declares_route_recipient_and_limits(self):
        for key, entry in sms_catalogue.ENTRIES.items():
            with self.subTest(kind=key):
                self.assertIn(entry.route, (sms_catalogue.NORMAL, sms_catalogue.OTP))
                self.assertIn(entry.recipient, (sms_catalogue.STAFF,
                                                sms_catalogue.CUSTODIAN,
                                                sms_catalogue.CALLER))
                self.assertTrue(entry.rules or entry.no_rule_reason.strip(),
                                "no rules and no reason for having none")
                self.assertFalse(entry.rules and entry.no_rule_reason,
                                 "rules and a reason for having none")
                for rule in entry.rules:
                    self.assertGreater(rule.count, 0)
                    self.assertGreater(rule.seconds, 0)
                    self.assertIn(rule.by, (sms_catalogue.BY_NUMBER,
                                            sms_catalogue.BY_NUMBER_SUBJECT,
                                            sms_catalogue.BY_REQUESTER))
                    self.assertTrue(rule.group)

    def test_the_two_codes_share_one_pool(self):
        self.assertEqual({"phone_code", "custodian_code"},
                         set(sms_catalogue.kinds_in_group("code")))

    def test_a_computed_kind_is_built_by_the_catalogue(self):
        for event in sms_catalogue.CUSTODIAN_EVENTS:
            self.assertIn(sms_catalogue.custodian_event_kind(event),
                          sms_catalogue.ENTRIES)
        with self.assertRaises(KeyError):
            sms_catalogue.custodian_event_kind("exploded")


class TheCallSitesTest(SimpleTestCase):
    def test_the_scanner_catches_what_it_is_for(self):
        bad = ("send_sms(n, t)\n"
               "send_sms(n, t, 'invented')\n"
               "queue_sms(n, t, kind='also_invented')\n"
               "send_sms(n, t, f'custodian_{event}')\n"
               "x.queue_sms(n, t, sms_catalogue.custodian_event_kind(e))\n"
               "send_sms(n, t, 'config_test')\n")
        problems, referenced = problems_in(bad)
        self.assertEqual(4, len(problems), problems)
        self.assertIn("config_test", referenced)
        self.assertIn("custodian_booked", referenced)

    def test_every_call_in_the_backend_names_a_declared_kind(self):
        problems, referenced, calls = [], set(), 0
        for path in backend_sources():
            source = path.read_text(encoding="utf-8")
            if not any(name in source for name in SENDERS):
                continue
            found, kinds = problems_in(source)
            calls += len(sender_calls(source))
            problems += [f"{path.name}: {p}" for p in found]
            referenced |= kinds
        self.assertGreater(calls, 5, "the scan found almost no call sites")
        self.assertEqual([], problems)

        # The other direction: an entry nothing sends is a message somebody
        # declared and never wired up, or wired up and then removed.
        unused = [k for k in sms_catalogue.ENTRIES
                  if k not in referenced and not k.startswith(NOT_CALLED_YET)]
        self.assertEqual([], unused)
