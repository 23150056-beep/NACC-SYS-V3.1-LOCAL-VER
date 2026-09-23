"""Score what the assistant actually writes, against real records.

This is an instrument, not a gate. It needs a running Ollama, so it can never
live in the test suite — but without it, "the briefs seem fine" is an
impression rather than a number, and an impression is how a hallucinated child
name reached a clinical draft unnoticed.

    manage.py ai_eval                      # every feature, 2 reps
    manage.py ai_eval --feature polish     # one feature
    manage.py ai_eval --reps 5 --limit 6   # more evidence, more children
"""
import time

from django.core.management.base import BaseCommand

from assistant import evaluation, prompts, tools
from assistant.models import AssistantSetting
from assistant.services import AIUnavailable, get_ai_client
from children.models import Child

# Fixed polish inputs: Taglish as the notes are actually written, heavy Tagalog,
# and an English control. The control is what tells us whether a failure is
# about language or about the feature.
POLISH_CASES = [
    ("taglish",
     "Settling in well. Nakikisalamuha na sa ibang bata during recreation."),
    ("heavy tagalog",
     "Nag-aalala pa rin tungkol sa school. Hindi masyado nagsasalita ngayon."),
    ("english control",
     "Settling in well. Mixing with the other children during recreation."),
]


# Chat cases, in both registers. `expect_hits` is the column that matters: a
# question that routes perfectly and then returns nothing is the failure this
# eval exists to catch. Searching the phrase "school refusal" as a substring
# scored 100% on routing and 0% on answers for a full day, because nothing
# measured the second half.
CHAT_CASES = [
    # (label, question, expected tool, expect a non-empty result, expected args)
    # The last element is checked as a SUBSET of the validated call's args, or
    # None to check none. Routing alone is not enough: kahapon routed to the
    # right tool and then asked for the wrong day, and that scored as a pass.
    ("appointments en", "Who am I seeing tomorrow?", "list_my_appointments", False,
     {"when": "tomorrow"}),
    ("appointments tl", "Sino ang makikita ko bukas?", "list_my_appointments", False,
     {"when": "tomorrow"}),
    ("count en", "How many children am I handling?", "count_my_children", True, None),
    ("count tl", "Ilan ang mga bata ko?", "count_my_children", True, None),
    ("concern en", "Any children with school refusal?",
     "search_children_by_concern", True, None),
    ("concern tl", "Sino ang mga bata na ayaw pumasok sa eskwela?",
     "search_children_by_concern", True, None),
    ("concern sleep", "Who has trouble sleeping?",
     "search_children_by_concern", True, None),
    ("concern plural", "Any kids struggling with emotions?",
     "search_children_by_concern", True, None),
    ("gaps en", "Who still needs a follow-up?", "list_care_gaps", False, None),
    ("gaps tl", "Sino ang kailangan ng follow-up?", "list_care_gaps", False, None),
    ("chitchat", "Good morning!", "answer_directly", False,
     {"reason": "greeting_or_closing"}),
    # Regression: seen in the browser answering "40 active children". The
    # answer was a refusal until count_people existed; it is a number now, and
    # correct_obvious_misroute stays behind it as the backstop.
    ("staff count en", "how many psychologist are in the system?",
     "count_people", True, {"role": "psychologist"}),
    ("staff count tl", "Ilan ang mga psychologist dito?", "count_people", True,
     {"role": "psychologist"}),
    ("staff count any", "How many users are in the system?", "count_people",
     True, None),
    ("unassigned en", "Which children have no psychologist?",
     "list_unassigned_children", False, None),
    # Booked vs bookable: the pair most likely to be confused for each other,
    # so both directions are scored.
    ("availability en", "Who is free tomorrow?", "find_availability", False,
     {"when": "tomorrow"}),
    ("availability tl", "Sino ang bakante bukas?", "find_availability", False,
     {"when": "tomorrow"}),
    ("booked not bookable", "What appointments do I have tomorrow?",
     "list_my_appointments", False, {"when": "tomorrow"}),
    # Regression: kahapon was aliased to today, so this answered with today's
    # appointments. Routing alone cannot catch it — the argument has to be
    # checked.
    ("past tl", "Sino ang nakita ko kahapon?", "list_my_appointments", False,
     {"when": "yesterday"}),
    ("past en", "Who did I see yesterday?", "list_my_appointments", False,
     {"when": "yesterday"}),
    ("last week en", "What did I do last week?", "list_my_appointments", False,
     {"when": "last_week"}),
    ("month tl", "Ilan ang appointments ko ngayong buwan?",
     "list_my_appointments", False, {"when": "this_month"}),
    # Appointments stop at the month: "what have I got this year?" went to
    # list_care_gaps 3/3, and a year of appointments is not a question anyone
    # asks. The year lives on the flags tool, where reviewing over one is real.
    ("month en", "What appointments do I have this month?",
     "list_my_appointments", False, {"when": "this_month"}),
    # The panel's own suggested questions for administrators and staff. Neither
    # was measured, and both were answered "Nothing recorded" for those roles
    # until the schedule followed the Dashboard's scope. No result is expected:
    # demo sessions sit on five fixed dates, so any given week may hold none.
    # The scope itself is pinned by resolver tests; what a live model can get
    # wrong here is the routing and the period.
    ("agency last week en", "What was scheduled last week?",
     "list_my_appointments", False, {"when": "last_week"}),
    ("agency this week en", "What's on this week?",
     "list_my_appointments", False, {"when": "this_week"}),
    ("flags year en", "Has anything been flagged this year?",
     "list_self_report_flags", False, {"period": "this_year"}),
    ("flags en", "Who flagged something worrying?",
     "list_self_report_flags", False, None),
    ("flags tl", "Sino ang may nakakabahala sa sinulat nila?",
     "list_self_report_flags", False, None),
    ("greeting tl", "Salamat po!", "answer_directly", False,
     {"reason": "greeting_or_closing"}),
    ("name partial", "Tell me about Maria", "get_child_summary", False, None),
    ("action en", "Book Ana for Friday", "answer_directly", False, None),
]


class _EvalRequest:
    """The two attributes the scope helper reads. A management command has no
    real request, and scope must still come from a user rather than a flag."""

    def __init__(self, user):
        self.user = user


def eval_callers():
    """One caller per role that has an account: [(role name, request)].

    The eval used to ask everything as one psychologist, so staff and
    administrators were never measured — which is how the schedule answered
    "Nothing recorded" for both, including to the questions the panel itself
    suggests to them. Routing does not depend on who asks (the prompt and the
    tools array are the same for everyone); the ANSWER does. So each question
    is routed once and resolved under every caller.

    The psychologist is one with a caseload, or "found nothing" measures an
    empty caseload rather than the tool. A role with no active account is left
    out and the report says so, rather than skipping the whole feature.
    """
    from django.contrib.auth import get_user_model
    from accounts.models import Role

    User = get_user_model()
    callers = []
    psy = (Child.objects.exclude(assigned_psychologist=None)
           .values_list("assigned_psychologist", flat=True).first())
    if psy is not None:
        callers.append((Role.PSYCHOLOGIST, _EvalRequest(User.objects.get(pk=psy))))
    for role in (Role.STAFF, Role.ADMINISTRATOR):
        user = (User.objects.filter(role__role_name=role, status=User.ACTIVE)
                .order_by("pk").first())
        if user is not None:
            callers.append((role, _EvalRequest(user)))
    return callers


FEELING_Q = "How are you feeling this week?"
WORRY_Q = "Is there anything worrying you?"

# Hand-labelled (question, answer) pairs, every string taken from the live
# database. The label is a human judgement, not ground truth from the agency.
# Both columns matter: a detector that flags everything has perfect recall and
# is worthless, so false alarms are scored too.
SELF_REPORT_CASES = [
    ("tl distress", FEELING_Q, "Gusto ko na umuwi. Lagi akong umiiyak sa gabi.", True),
    ("tl distress", WORRY_Q, "Hindi ko masabi kasi baka magalit sila. Masakit ang dibdib ko.", True),
    ("tl distress", FEELING_Q, "I feel alone. Ayaw ko na dito, gusto ko na lang matulog.", True),
    ("mixed distress", WORRY_Q, "Nobody listens to me here. Wala akong makausap.", True),
    ("ilocano distress", FEELING_Q, "Mabutbuteng. I am scared but I don't tell them.", True),
    ("ilocano distress", WORRY_Q, "Adda met bassit nga problema but I don't want to say.", True),
    ("ambiguous", FEELING_Q, "Sometimes I cannot sleep. Naiisip ko yung bahay namin.", True),
    ("calm control", FEELING_Q, "I feel safe. Ang bait ng nag-aalaga sa akin.", False),
    ("calm control", FEELING_Q, "Okay lang. I like the food and my bed.", False),
    ("calm control", FEELING_Q, "Masaya naman ako dito. May kaibigan na ako.", False),
    ("ilocano calm", FEELING_Q, "Naimbag met. I can sleep at night now.", False),
    ("calm control", FEELING_Q, "I miss my sister. But the people here are kind.", False),
]


_TAGALOG_HINT = ("naki", "nag-", "ang ", " sa ", " ng ", "mga ", "hindi", "bata")


def _looks_taglish(text):
    low = text.lower()
    return any(h in low for h in _TAGALOG_HINT)


class Command(BaseCommand):
    help = "Evaluate the assistant's drafting output against real records."

    def add_arguments(self, parser):
        parser.add_argument("--feature",
                            choices=["brief", "polish", "chat", "self_report", "all"],
                            default="all")
        parser.add_argument("--reps", type=int, default=2,
                            help="Runs per case; the model is not deterministic.")
        parser.add_argument("--limit", type=int, default=3,
                            help="Children to sample for briefs.")

    def handle(self, *args, **options):
        cfg = AssistantSetting.load()
        if not cfg.enabled:
            self.stdout.write("The assistant is switched off — nothing to evaluate.")
            raise SystemExit(1)

        self.client = get_ai_client()
        self.stdout.write(f"Model: {cfg.model_name}   reps: {options['reps']}\n")

        totals = []
        if options["feature"] in ("brief", "all"):
            totals.append(self._briefs(options["reps"], options["limit"]))
        if options["feature"] in ("polish", "all"):
            totals.append(self._polish(options["reps"]))
        if options["feature"] in ("chat", "all"):
            totals.append(self._chat(options["reps"]))
        if options["feature"] in ("self_report", "all"):
            totals.append(self._self_report(options["reps"]))

        self.stdout.write("\n" + "=" * 62)
        self.stdout.write("SUMMARY")
        for name, runs, flags, latency in totals:
            if not runs:
                continue
            self.stdout.write(f"\n{name}  ({runs} runs, median {latency} ms)")
            for label, n in flags.items():
                pct = 100 * n / runs
                self.stdout.write(f"  {label:22} {n}/{runs}  ({pct:.0f}%)")

    # -- features ---------------------------------------------------------

    def _generate(self, prompt, system):
        started = time.monotonic()
        text = self.client.generate(prompt, system=system)
        return text, int((time.monotonic() - started) * 1000)

    def _score(self, prompt, text, expect_english):
        flags = {}
        names = evaluation.invented_names(prompt, text)
        if names:
            flags["invented names"] = names
        repeats = evaluation.repeated_lines(text)
        if repeats:
            flags["repeated lines"] = repeats
        stutters = evaluation.repeated_phrases(text)
        if stutters:
            flags["repeated words"] = stutters
        if expect_english:
            drift = evaluation.language_drift(text)
            if drift:
                flags["language drift"] = drift
        return flags

    def _briefs(self, reps, limit):
        children = list(Child.objects.filter(remarks__isnull=False)
                        .distinct().order_by("id")[:limit])
        self.stdout.write("\n" + "=" * 62)
        self.stdout.write(f"BRIEFS — {len(children)} children x {reps} reps")

        runs, latencies = 0, []
        counts = {"invented names": 0, "repeated lines": 0,
                  "repeated words": 0}

        for child in children:
            prompt = prompts.build_brief_prompt(child)
            tag = "taglish" if _looks_taglish(prompt) else "english"
            self.stdout.write(f"\n  {child.fullname} (id={child.id}) [{tag}]")
            for rep in range(reps):
                try:
                    text, ms = self._generate(prompt, prompts.BRIEF_SYSTEM)
                except AIUnavailable as exc:
                    self.stdout.write(f"    rep{rep}: UNAVAILABLE — {exc}")
                    continue
                runs += 1
                latencies.append(ms)
                # BRIEF_SYSTEM does not ask for English — it asks for the
                # facts it was given. A brief quoting a Taglish remark is
                # correct, so scoring drift here measured a requirement that
                # does not exist and reported 8% against briefs that were fine.
                flags = self._score(prompt, text, expect_english=False)
                for key in flags:
                    counts[key] += 1
                self._report(rep, ms, flags, text=text)

        return ("BRIEFS", runs, counts, self._median(latencies))

    def _polish(self, reps):
        self.stdout.write("\n" + "=" * 62)
        self.stdout.write(f"REMARK POLISH — {len(POLISH_CASES)} cases x {reps} reps")

        runs, latencies = 0, []
        counts = {"invented names": 0, "repeated lines": 0,
                  "repeated words": 0, "language drift": 0}

        for label, raw in POLISH_CASES:
            prompt = prompts.build_remark_prompt(raw)
            self.stdout.write(f"\n  [{label}] {raw[:60]}")
            for rep in range(reps):
                try:
                    text, ms = self._generate(prompt, prompts.REMARK_POLISH_SYSTEM)
                except AIUnavailable as exc:
                    self.stdout.write(f"    rep{rep}: UNAVAILABLE — {exc}")
                    continue
                runs += 1
                latencies.append(ms)
                flags = self._score(prompt, text, expect_english=True)
                for key in flags:
                    counts[key] += 1
                self._report(rep, ms, flags, sample=text, text=text)

        return ("REMARK POLISH", runs, counts, self._median(latencies))


    def _chat(self, reps):
        """Route a question, validate it, and run the resolver for real.

        The resolver runs against the live database under each role's own
        scope, so "found nothing" is measured rather than assumed — for staff
        and administrators as well as psychologists. See eval_callers().
        """
        from accounts.models import Role

        callers = eval_callers()
        if not callers:
            self.stdout.write("\nCHAT — no account to ask as; skipped.")
            return ("chat", 0, {}, 0)

        self.stdout.write("\n" + "=" * 62)
        self.stdout.write(f"CHAT — {len(CHAT_CASES)} cases x {reps} reps")
        for role, req in callers:
            self.stdout.write(f"Caller: {role:<14} {req.user.email}")
        present = {role for role, _ in callers}
        for role in (Role.PSYCHOLOGIST, Role.STAFF, Role.ADMINISTRATOR):
            if role not in present:
                self.stdout.write(f"Caller: {role:<14} none — NOT MEASURED")

        runs, flags, latencies = 0, {}, []
        payload = tools.ollama_payload()
        for label, question, expected, expect_hits, expected_args in CHAT_CASES:
            self.stdout.write(f"\n  {label}: {question}")
            for rep in range(1, reps + 1):
                started = time.monotonic()
                try:
                    tool, raw = self.client.choose_tool(
                        question, payload, prompts.CHAT_SYSTEM)
                except AIUnavailable as exc:
                    self.stdout.write(f"    rep{rep}: unavailable — {exc}")
                    continue
                ms = int((time.monotonic() - started) * 1000)
                latencies.append(ms)
                runs += 1

                found = {}
                call = tools.validate(tool or "answer_directly", raw)
                call = tools.correct_obvious_misroute(question, call)
                call = tools.correct_action_request(question, call)
                call = tools.correct_greeting(question, call)
                # Scored AFTER the guards, because the guards ship. Scoring the
                # model's raw pick reported a defect the user never sees and
                # hid the fact that a correction had happened at all — the
                # action-request downgrade looked like a 3/3 failure while
                # working correctly.
                if call.tool != expected:
                    found["wrong tool"] = [f"{call.tool or 'prose'} != {expected}"]
                elif tool != expected:
                    found["model misrouted, guard corrected"] = [
                        f"{tool or 'prose'} -> {call.tool}"]
                if not call.ok:
                    found["rejected"] = [call.error]
                elif expect_hits:
                    # Per role: the same routed call can be a full answer for a
                    # psychologist and an empty one for an administrator.
                    for role, req in callers:
                        result = tools.REGISTRY[call.tool]["resolve"](req, call.args)
                        n = result.get("count", result.get(
                            "total", len(result.get("items", []))))
                        if not n:
                            # The silent failure: a confident empty answer.
                            found[f"empty answer ({role})"] = [
                                f"{call.args or 'no args'}"]
                # The other silent failure: the right tool asked the wrong
                # question. kahapon routed perfectly and requested today.
                if call.ok and expected_args:
                    wrong = {k: call.args.get(k) for k, v in expected_args.items()
                             if call.args.get(k) != v}
                    if wrong:
                        found["wrong argument"] = [f"{wrong} != {expected_args}"]
                for key, items in found.items():
                    flags.setdefault(key, 0)
                    flags[key] += 1
                self._report(rep, ms, {k: v for k, v in found.items()})

        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else 0
        return ("CHAT", runs, flags, median)


    def _self_report(self, reps):
        """Score the model detector against hand-labelled pairs.

        Reports misses AND false alarms. A detector that flags everything has
        perfect recall and is worthless, so both columns are printed.
        """
        from clinical.self_report_model_check import _parse

        self.stdout.write("\n" + "=" * 62)
        self.stdout.write(f"SELF-REPORT - {len(SELF_REPORT_CASES)} cases x {reps} reps")

        runs, flags, latencies = 0, {}, []
        for label, question, answer, expected in SELF_REPORT_CASES:
            self.stdout.write(f"\n  {label}: {answer[:56]}")
            for rep in range(1, reps + 1):
                started = time.monotonic()
                try:
                    reply = self.client.generate(
                        prompts.build_self_report_prompt(question, answer),
                        system=prompts.SELF_REPORT_SYSTEM)
                except AIUnavailable as exc:
                    self.stdout.write(f"    rep{rep}: unavailable - {exc}")
                    continue
                ms = int((time.monotonic() - started) * 1000)
                latencies.append(ms)
                runs += 1

                got = _parse(reply) is not None
                found = {}
                if expected and not got:
                    found["MISS"] = [answer[:48]]
                elif got and not expected:
                    found["false alarm"] = [answer[:48]]
                for key in found:
                    flags[key] = flags.get(key, 0) + 1
                self._report(rep, ms, found, sample=str(reply))

        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else 0
        return ("SELF-REPORT", runs, flags, median)

    # -- output -----------------------------------------------------------

    def _report(self, rep, ms, flags, sample=None, text=None):
        if not flags:
            self.stdout.write(f"    rep{rep}: clean  ({ms} ms)")
            return
        parts = ", ".join(f"{k}={v}" for k, v in flags.items())
        self.stdout.write(f"    rep{rep}: {parts}  ({ms} ms)")
        # A flag without its surrounding text is a number nobody can act on —
        # it forces whoever reads the run to go and reproduce it by hand, which
        # is how two false-positive detectors survived long enough to put wrong
        # rates in a report. Every flag shows where it fired.
        if text:
            for label, items in flags.items():
                if label == "language drift":
                    continue          # markers are scattered; the sample below shows them
                for item in items[:3]:
                    self.stdout.write(f"        {label}: {self._context(text, item)}")
        if sample:
            self.stdout.write(f"        out: {sample.strip()[:140]}")

    @staticmethod
    def _context(text, needle, width=60):
        """The offending phrase with enough either side to judge it."""
        flat = " ".join(text.split())
        i = flat.find(needle)
        if i < 0:
            return f"(…{needle}…)"
        start = max(0, i - width // 2)
        return "…" + flat[start:i + len(needle) + width // 2] + "…"

    @staticmethod
    def _median(values):
        if not values:
            return 0
        ordered = sorted(values)
        return ordered[len(ordered) // 2]
