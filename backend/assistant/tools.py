"""The chatbot's tool registry and its validator.

The model's entire job is to pick one of these and fill in its arguments. It
never sees what comes back — results go from the database to a UI component,
so a turn costs about five seconds and case data is never in a position to be
invented.

Everything the validator does exists because the spike observed the failure:

* the model emitted a key with a colon in it, `{"when: ": "today"}`
* it returned `{"when": "bukas"}` where the enum allowed only English values
* it dropped every optional free-text argument on every call

So arguments are treated as untrusted input, and scope is never read from them:
no tool declares a "which children" parameter, because the answer always comes
from `request.user`.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
import re

from accounts.display import display_name
from accounts.scoping import visible_children

# Enum values the model reached for that were not in the enum. Deterministic,
# instant, free — and the single change that took measured accuracy from 91%
# to 100% on the spike's case set.
ALIASES = {
    "when": {
        "ngayon": "today", "ngayong araw": "today", "today": "today",
        # Regression: this was mapped to "today". Kahapon is yesterday, and
        # aliasing around a missing period produced a confidently wrong answer.
        "kahapon": "yesterday", "yesterday": "yesterday",
        "bukas": "tomorrow", "tomorrow": "tomorrow",
        "ngayong linggo": "this_week", "this week": "this_week",
        "nakaraang linggo": "last_week", "noong isang linggo": "last_week",
        "last week": "last_week",
        "susunod na linggo": "next_week", "next week": "next_week",
        "ngayong buwan": "this_month", "this month": "this_month",
        "nakaraang buwan": "last_month", "noong isang buwan": "last_month",
        "last month": "last_month",
        "ngayong taon": "this_year", "this year": "this_year",
        "nakaraang taon": "last_year", "noong isang taon": "last_year",
        "last year": "last_year",
    },
    "status": {
        "aktibo": "active", "buhay": "active", "tapos": "terminated",
        "closed": "terminated", "inactive": "terminated", "archived": "terminated",
        "lahat": "any", "all": "any",
    },
    # get_statistics. The model reaches for the word the user used, so the
    # user's words are what these map from. An empty value means the default,
    # never a rejection: the routing was right.
    "measure": {
        "": "children", "child": "children", "kids": "children", "kid": "children",
        "bata": "children", "mga bata": "children", "caseload": "children",
        "cases": "children", "case": "children",
        "intakes": "intake", "admission": "intake", "admissions": "intake",
        "new": "intake", "new children": "intake", "bagong bata": "intake",
        "closure": "closures", "closed": "closures", "termination": "closures",
        "terminations": "closures", "terminated": "closures",
        "pre-assessment": "pre_assessments", "pre-assessments": "pre_assessments",
        "pre_assessment": "pre_assessments", "preassessments": "pre_assessments",
        "pending": "pre_assessments",
        "session": "sessions", "appointments": "sessions", "appointment": "sessions",
        "visits": "sessions", "attendance": "sessions", "no-show": "sessions",
        "no-shows": "sessions", "no show": "sessions", "no shows": "sessions",
        "noshow": "sessions", "noshows": "sessions", "no-show rate": "sessions",
        "wait": "first_session_wait", "waiting": "first_session_wait",
        "wait time": "first_session_wait", "wait_time": "first_session_wait",
        "waiting time": "first_session_wait", "waiting_time": "first_session_wait",
        "first session": "first_session_wait", "first_session": "first_session_wait",
        "time to first session": "first_session_wait",
        "time_to_first_session": "first_session_wait",
        "days to first session": "first_session_wait",
        "days_to_first_session": "first_session_wait",
        "intake to first session": "first_session_wait",
        "wait for first session": "first_session_wait",
        "wait_for_first_session": "first_session_wait",
        "unang session": "first_session_wait", "paghihintay": "first_session_wait",
        "pre-assessment time": "pre_assessment_duration",
        "pre_assessment_time": "pre_assessment_duration",
        "time in pre-assessment": "pre_assessment_duration",
        "time_in_pre_assessment": "pre_assessment_duration",
        "pre-assessment duration": "pre_assessment_duration",
        "pre_assessment_length": "pre_assessment_duration",
        "pre-assessment length": "pre_assessment_duration",
        "assessment time": "pre_assessment_duration", "duration": "pre_assessment_duration",
        "turnaround": "pre_assessment_duration",
        "tagal ng pre-assessment": "pre_assessment_duration",
    },
    "by": {
        "": "none", "total": "none", "all": "none", "nothing": "none",
        "type": "case_type", "case type": "case_type", "casetype": "case_type",
        "stage": "case_stage", "case stage": "case_stage", "case_status": "case_stage",
        "age": "age_band", "age group": "age_band", "age groups": "age_band",
        "age_group": "age_band", "edad": "age_band",
        "gender": "sex", "kasarian": "sex",
        "psychologists": "psychologist", "assigned psychologist": "psychologist",
        "per psychologist": "psychologist", "caseload": "psychologist",
        "reasons": "reason", "why": "reason", "dahilan": "reason",
        "months": "month", "monthly": "month", "per month": "month", "buwan": "month",
        "outcome": "status", "outcomes": "status", "attendance": "status",
    },
    # Measured: asked "how many users are in the system?", the model answered
    # role="any" against an enum offering "anyone". The near-miss is what this
    # table is for — rejecting a correctly-routed call over one synonym turns a
    # right answer into an apology.
    "role": {
        "any": "anyone", "all": "anyone", "everyone": "anyone",
        "everybody": "anyone", "user": "anyone", "users": "anyone",
        "lahat": "anyone",
        "psychologists": "psychologist", "psikologo": "psychologist",
        "administrators": "administrator", "admin": "administrator",
        "admins": "administrator", "staffs": "staff", "kawani": "staff",
    },
}

# The table is keyed by PARAMETER NAME, and two tools name their time argument
# differently — appointments call it `when`, flags call it `period`. Without
# this, "ngayong taon" would resolve on one and be rejected on the other for no
# reason a user could see.
ALIASES["period"] = ALIASES["when"]

_PUNCT = re.compile(r"[^\w]+")


@dataclass
class ToolCall:
    """The result of validating what the model produced."""
    tool: str
    args: dict = field(default_factory=dict)
    ok: bool = True
    error: str = ""
    echo: str = ""


def _clean_key(key):
    """`"when: "` and `"  status  "` both become the parameter's real name."""
    return _PUNCT.sub("", str(key)).strip().lower()


def _coerce(param, value, meta):
    """Return (value, error). Enums are matched case-insensitively, then via
    the alias table, and only then rejected."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.strip()

    if "enum" not in meta:
        return (value, "") if value else (
            "", f"'{param}' cannot be empty.")

    low = value.lower()
    if low in meta["enum"]:
        return low, ""
    aliased = ALIASES.get(param, {}).get(low)
    if aliased in meta["enum"]:
        return aliased, ""
    return "", (f"'{param}' must be one of {', '.join(meta['enum'])} "
                f"— got '{value}'.")


def validate(tool, raw_args):
    """Turn the model's output into a ToolCall, or explain why it cannot be."""
    spec = REGISTRY.get(tool)
    if spec is None:
        return ToolCall(tool=tool, ok=False,
                        error=f"'{tool}' is not something I can do.")

    schema = spec["schema"]
    supplied = {_clean_key(k): v for k, v in (raw_args or {}).items()}
    args, errors = {}, []

    for param, meta in schema.items():
        if param not in supplied:
            if meta.get("required"):
                errors.append(f"'{param}' is required.")
            elif "default" in meta:
                # Filled here rather than in the resolver so the echo, the log
                # and the eval all see the arguments the answer actually used.
                args[param] = meta["default"]
            continue
        value, err = _coerce(param, supplied[param], meta)
        if err:
            errors.append(err)
        else:
            args[param] = value

    # Anything the schema does not declare is discarded, never passed through.
    # An invented "assigned_to_me" must not reach a queryset.
    if errors:
        return ToolCall(tool=tool, ok=False, error=" ".join(errors))
    return ToolCall(tool=tool, args=args, echo=spec["echo"](args))



# --- misroute guard --------------------------------------------------------
# Observed in the browser: "how many psychologist are in the system?" answered
# "40 active children". The cause was count_my_children's own description,
# which said "Use for questions starting 'how many'". Rewording it fixed two
# phrasings out of four — not good enough for a tool that answers with a
# confident number, and a confidently wrong answer is the worst failure this
# chatbot has. So the wording change is backed by a deterministic check.

_PEOPLE_WHO_WORK_HERE = (
    "psychologist", "psychologists", "psych", "psikologo", "psychologo",
    "staff", "employee", "employees", "worker", "workers", "kawani",
    "tauhan", "empleyado", "user", "users", "account", "accounts",
    "administrator", "administrators", "admin", "admins", "doctor", "doctors",
    "social worker", "social workers",
)

_ABOUT_CHILDREN = (
    "child", "children", "kid", "kids", "bata", "batang", "case", "cases",
    "caseload", "ward", "wards", "client", "clients",
)


def correct_obvious_misroute(question, call):
    """Stop a child count being served as the answer to a staff question.

    Now a backstop rather than the answer. count_people exists, so the model
    should route these itself; this catches the times it does not. It is
    deliberately NOT changed to re-route to count_people, because choosing
    that tool means also choosing a role, and a guard that picks a role from
    keywords is doing the model's job with worse equipment — it would turn a
    safe refusal into a confident number.

    Only downgrades to `answer_directly`, never upgrades or re-routes: the
    guard can make the assistant decline, and cannot make it assert anything.
    A question naming both — "how many children were referred by staff?" — is
    left alone, because children are the subject and the tool can answer it.
    """
    # get_statistics counts children when `measure` is children, which is what
    # count_my_children did before it was folded in — and the misroute this
    # guards against came from exactly that count.
    if (not call.ok or call.tool != "get_statistics"
            or call.args.get("measure") != "children"):
        return call
    low = _PUNCT.sub(" ", str(question or "").lower())
    words = f" {low} "
    names_people = any(f" {w} " in words for w in _PEOPLE_WHO_WORK_HERE)
    names_children = any(f" {w} " in words for w in _ABOUT_CHILDREN)
    if names_people and not names_children:
        return ToolCall(tool="answer_directly", args={"reason": "unsupported"},
                        echo="", )
    return call


# Imperatives, not topics. "schedule" is absent on purpose — it appears in
# "what is my schedule today?", which is a question this assistant answers.
# "send" is deliberately absent. "Send me the list of children needing
# follow-up" is a request for information, and refusing it with "I can't
# change anything" is worse than the rare "send the report" this would catch.
_ACTION_VERBS = (
    "book", "cancel", "create", "add", "delete", "remove", "reset", "update",
    "edit", "assign", "reassign", "upload", "approve", "deactivate",
    "magdagdag", "magbook", "burahin", "palitan", "tanggalin", "idagdag",
)


def correct_action_request(question, call):
    """Answer "book Ana for Friday" with "I can't change anything".

    Measured: the model routes that to list_my_appointments 3 times out of 3,
    so a request to CREATE a booking was answered with a LIST of bookings.
    Guarding only answer_directly never fired, because the model had already
    picked a data tool.

    So this downgrades whatever was chosen, exactly as
    correct_obvious_misroute does for a staff question. The safety property is
    unchanged and is the reason both are allowed to exist: they can only make
    the assistant decline, never make it assert anything.

    `action_request` is server-side only: this constructs its ToolCall
    directly, so validate() never sees the value and the schema the model
    reads does not grow by it.
    """
    if not call.ok:
        return call
    words = _PUNCT.sub(" ", str(question or "").lower()).split()
    if not words:
        return call
    # Tagalog forms an imperative by prefixing the verb: "i-reset mo ang
    # password". _PUNCT has already turned that hyphen into a space, so the
    # verb is the second word and "i" is the first — checking the pair covers
    # every i- verb without listing them.
    leading = words[0]
    if leading == "i" and len(words) > 1:
        leading = words[1]
    if leading in _ACTION_VERBS:
        return ToolCall(tool="answer_directly",
                        args={"reason": "action_request"}, echo="")
    return call


# Openings and sign-offs, English and Tagalog.
_GREETING_WORDS = (
    "hello", "hi", "hey", "good", "morning", "afternoon", "evening",
    "thanks", "thank", "cheers", "bye", "goodbye", "ok", "okay", "noted",
    "salamat", "magandang", "kumusta", "kamusta", "paalam", "sige", "opo",
)


def correct_greeting(question, call):
    """Recover greeting_or_closing when the model left `reason` out.

    Measured: on "Good morning!" the model omits `reason` 2 times in 3. The
    argument is optional on purpose — requiring it turned a correct routing
    decision into a failed turn — so the reason has to be recoverable without
    the model, or the greeting reply is dead code most of the time and
    "salamat po" still gets a list of features.

    Short questions only: "Good morning, how many children do I have?" is a
    question with a greeting attached, not a greeting. Five words, because
    "Magandang umaga po sa inyo" is a greeting and nothing else.
    """
    if not call.ok or call.tool != "answer_directly":
        return call
    if call.args.get("reason") == "action_request":
        return call                       # already classified, and not a greeting
    words = _PUNCT.sub(" ", str(question or "").lower()).split()
    if words and len(words) <= 5 and words[0] in _GREETING_WORDS:
        return ToolCall(tool="answer_directly",
                        args={"reason": "greeting_or_closing"}, echo="")
    return call


# --- resolvers ------------------------------------------------------------
# Each takes (request, validated args) and returns a plain dict the frontend
# renders. The model never sees any of this — which is precisely why a
# hallucinated child name is impossible here: names come out of the database.
#
# Scope is taken from request.user through the same helper the rest of the app
# uses. No resolver reads scope from the model's arguments.

# Periods. Weeks, months and years are calendar-aligned; days are offsets.
# Rolling windows ("today through +7") do not survive `last_month`, and mixing
# the two would have "this week" and "this month" answering on different logic.
PERIODS = ("today", "yesterday", "tomorrow",
           "this_week", "last_week", "next_week",
           "this_month", "last_month",
           "this_year", "last_year")

# Appointments stop at the month. Measured: offering the year sent "what have
# I got this year?" to list_care_gaps 3 times out of 3, and no psychologist
# asks to see a year of appointments anyway — the value bought nothing and
# cost a misroute. Review questions about flags do reach a year, so the full
# vocabulary stays available there.
APPOINTMENT_PERIODS = tuple(p for p in PERIODS if not p.endswith("_year"))

# Periods that contain no past day. Everything else contains one — `today`
# included, because a session completed this morning belongs in the answer.
FUTURE_ONLY = {"tomorrow", "next_week"}

# How many sessions a single schedule answer lists. `total` carries the rest.
APPOINTMENT_PAGE = 25


def _week_start(day):
    """The Sunday on or before `day`.

    Sunday because Schedule.jsx builds its calendar with date-fns startOfWeek
    under the en-US locale, and the chatbot must agree with the screen the user
    is looking at. Python's weekday() is Monday=0..Sunday=6, so the number of
    days since Sunday is (weekday() + 1) % 7.
    """
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _month_start(day):
    return day.replace(day=1)


def _next_month(first):
    return date(first.year + (first.month == 12),
                1 if first.month == 12 else first.month + 1, 1)


def _previous_month(first):
    return date(first.year - (first.month == 1),
                12 if first.month == 1 else first.month - 1, 1)


def period_range(period, today=None):
    """(start, end) for a period name. End is EXCLUSIVE.

    `today` is injectable so tests can pin a weekday instead of depending on
    the day the suite happens to run.
    """
    if today is None:
        from django.utils import timezone
        today = timezone.localdate()

    if period == "today":
        return today, today + timedelta(days=1)
    if period == "yesterday":
        return today - timedelta(days=1), today
    if period == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=2)

    week = _week_start(today)
    if period == "this_week":
        return week, week + timedelta(days=7)
    if period == "last_week":
        return week - timedelta(days=7), week
    if period == "next_week":
        return week + timedelta(days=7), week + timedelta(days=14)

    month = _month_start(today)
    if period == "this_month":
        return month, _next_month(month)
    if period == "last_month":
        return _previous_month(month), month

    if period == "this_year":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1)
    if period == "last_year":
        return date(today.year - 1, 1, 1), date(today.year, 1, 1)

    raise KeyError(period)

# What each role can actually ask, in its own words. Built here rather than in
# the prompt because the model never sees it: role-awareness therefore costs
# nothing, and CHAT_SYSTEM stays byte-identical so the prefix cache stays warm.
_CAN_ASK = {
    "Psychologist": (
        "your schedule, numbers about your caseload — how many children, by "
        "case type, stage, age or sex, new intakes or closures, your "
        "attendance and no-shows, how long your children waited for a "
        "first session, and how long pre-assessments took — children "
        "with a particular concern, a summary of one child, who needs "
        "follow-up, and which children have flagged something in their own "
        "words"),
    "Administrator": (
        "the agency's schedule, the agency's numbers — children by case type, "
        "stage, age, sex or psychologist, new intakes, closures and why, "
        "attendance and no-shows, the wait for a first session, and time in "
        "pre-assessment — "
        "children with a particular concern, a summary of one child, who needs "
        "follow-up, and which children have flagged something in their own "
        "words"),
    "Staff": (
        "the schedule, the agency's numbers — children by case type, stage, "
        "age, sex or psychologist, new intakes, closures and why, "
        "attendance and no-shows, the wait for a first session, and time in "
        "pre-assessment — "
        "children with a particular concern, a summary of one child, and who "
        "needs follow-up"),
}
_CAN_ASK_DEFAULT = _CAN_ASK["Psychologist"]

# Shown in the empty panel. Questions, not features — someone who arrives by
# clicking a button has typed nothing and needs a starting point, not a menu.
_EXAMPLES = {
    "Psychologist": ["Who am I seeing today?",
                     "How many children do I have?",
                     "Who flagged something worrying?",
                     "Who needs follow-up?"],
    # One statistics question per role that has the Agency Summary, taken
    # from what that screen answers. Each one is an ai_eval case: a suggested
    # question that routes wrong is worse than no suggestion.
    "Administrator": ["Who needs follow-up?",
                      "Who flagged something worrying?",
                      "Active children by case type?",
                      "What was scheduled last week?"],
    # "Tell me about a child by name" went: it is an instruction, not a
    # question, and typed as-is it names no child.
    "Staff": ["Who needs follow-up?",
              "Any children with anxiety?",
              "What's on this week?",
              "Caseload per psychologist?"],
}

GREETING_REPLY = "Hello — what would you like to look up?"

ACTION_REPLY = (
    "I can look things up, but I can't change anything. Bookings, records and "
    "accounts are edited on their own screens.")


def capability_text(role):
    """One sentence naming what this role can ask. Public because the panel
    serves it too — there must not be a server answer and a frontend one."""
    return f"You can ask me about {_CAN_ASK.get(role, _CAN_ASK_DEFAULT)}."


def capability_examples(role):
    return list(_EXAMPLES.get(role, _EXAMPLES["Psychologist"]))


def _scope(request):
    return visible_children(request)


def _scope_appointments(request, qs):
    """(queryset, own) under the schedule's rule — the Dashboard's, the
    Calendar's and the appointments API's: only a psychologist is narrowed to
    their own calendar. One copy, used by the schedule answer and by the
    attendance numbers, so the two cannot scope a session differently."""
    from accounts.models import Role
    from accounts.scoping import role_of
    own = role_of(request) == Role.PSYCHOLOGIST
    return (qs.filter(psychologist=request.user) if own else qs), own


def _resolve_appointments(request, args):
    """The schedule, scoped the way the Dashboard scopes it.

    A psychologist sees their own sessions. Staff and administrators hold none,
    so they see the agency's — which is what the Dashboard's schedule strip
    already shows them, and what the panel's own suggested questions ask for:
    "What was scheduled last week?" is an administrator's example. Filtering
    every role by `psychologist=request.user` answered that example "Nothing
    recorded" for two roles out of three, while the screen behind the panel
    listed the day's sessions. A confident empty answer contradicting the
    screen is the worst failure this chatbot has.

    Paged like the flags, and for the same reason: a month across the agency
    runs to hundreds of rows, and a list cut short without saying so reads as
    the whole schedule. `total` always carries the real count.
    """
    from django.utils import timezone
    from scheduling.models import Appointment

    period = args["when"]
    start, end = period_range(period)

    # A period with no past day is a plan, so only what is still going to
    # happen belongs in it. Any other period has finished work in it, and
    # hiding that answers "what did I do this week" with silence. CANCELLED is
    # excluded either way: a cancelled appointment is not a session.
    statuses = ([Appointment.SCHEDULED] if period in FUTURE_ONLY
                else [Appointment.SCHEDULED, Appointment.COMPLETED,
                      Appointment.NO_SHOW])

    appts, own = _scope_appointments(request, Appointment.objects.filter(
        status__in=statuses, start__date__gte=start, start__date__lt=end))
    appts = appts.select_related("child", "psychologist").order_by("start")

    total = appts.count()
    return {"kind": "appointments", "when": period,
            "scope": "own" if own else "agency", "total": total, "items": [
                {"child": a.child.fullname,
                 "psychologist": display_name(a.psychologist),
                 "when": timezone.localtime(a.start).strftime("%a %d %b, %H:%M"),
                 "purpose": a.get_purpose_display(),
                 "status": a.status} for a in appts[:APPOINTMENT_PAGE]]}


# --- statistics -------------------------------------------------------------
#
# Only numbers a screen already shows, counted the way that screen counts them,
# so the chatbot and the screen cannot disagree:
#
#   children by case type or case stage .... the Dashboard's census
#   children by age group, sex, psychologist  the Agency Summary
#   intake and closures by month ............ the Dashboard's intake vs termination
#   closures by reason ...................... the Agency Summary
#   pending pre-assessments ................. the Dashboard
#   sessions and the no-show rate ........... the Agency Summary's attendance
#   the wait for a first session ............ the Agency Summary's wait card
#   time in pre-assessment .................. the Agency Summary's pre-assessment card
#
# A measure no screen has defined yet waits until one does, or the two will
# count it differently. The no-show rate waited for exactly that: its
# definition lives in clinical.reports.attendance, and the screen came first.

STAT_MEASURES = ("children", "intake", "closures", "pre_assessments", "sessions",
                 "first_session_wait", "pre_assessment_duration")
STAT_BREAKDOWNS = ("none", "case_type", "case_stage", "age_band", "sex",
                   "psychologist", "reason", "month", "status")
STAT_BY = {
    "children": {"none", "case_type", "case_stage", "age_band", "sex", "psychologist"},
    "intake": {"none", "month"},
    "closures": {"none", "reason", "month"},
    "pre_assessments": {"none"},
    # Attendance, by clinical.reports.attendance — the Agency Summary's card.
    # Deliberately no "by psychologist": a no-show is the child's absence, and
    # a table of no-show rates per psychologist reads as a scorecard of the
    # psychologists. That is a decision for the agency, not for a chatbot.
    "sessions": {"none", "status"},
    # By clinical.reports.first_session_wait — the Agency Summary's wait card.
    # No "by psychologist", for attendance's reason: a wait is the agency's.
    "first_session_wait": {"none"},
    # By clinical.reports.pre_assessment_duration, the Agency Summary's card.
    "pre_assessment_duration": {"none"},
}
# Events in time. Children and pending pre-assessments are counted as of today.
# A wait is dated by the day it ended: the child's first completed session.
# A pre-assessment's duration likewise, by the day it was completed.
STAT_DATED = {"intake", "closures", "sessions", "first_session_wait",
              "pre_assessment_duration"}

_BY_WORDS = {"case_type": "case type", "case_stage": "case stage",
             "age_band": "age group", "sex": "sex", "psychologist": "psychologist",
             "reason": "reason", "month": "month", "status": "status"}
_MEASURE_WORDS = {"children": "children", "intake": "new intakes",
                  "closures": "case closures", "pre_assessments": "pending pre-assessments",
                  "sessions": "sessions",
                  "first_session_wait": "waits for a first session",
                  "pre_assessment_duration": "time in pre-assessment"}
# Not categories, so they sort last whatever their count.
_LEFTOVERS = {"Unspecified", "Unassigned"}

_DASHBOARD = {"label": "Dashboard", "path": "/"}
_SUMMARY = {"label": "Agency Summary", "path": "/reports/summary"}


def _stat_screen(measure, by, role):
    """The screen that shows this number, if the caller can open it."""
    from accounts.models import Role
    summary = ((measure == "children" and by in ("age_band", "sex", "psychologist"))
               or (measure == "closures" and by == "reason")
               or measure in ("sessions", "first_session_wait", "pre_assessment_duration"))
    if not summary:
        return _DASHBOARD
    # The Agency Summary is Administrators and Staff only. A link somebody
    # cannot open is worse than no link.
    return None if role == Role.PSYCHOLOGIST else _SUMMARY


def _stat_subject(measure, status, by, span, total):
    """What the number counts — "active children, by case type". Built once
    here so the panel cannot word it differently; the headline is the total
    followed by this, and the panel shows the two apart for a plain total."""
    one = total == 1
    if measure == "children":
        head = {"active": f"active {'child' if one else 'children'}",
                "terminated": f"{'child' if one else 'children'} with a closed case",
                "any": f"{'child' if one else 'children'} on record"}[status]
    elif measure == "intake":
        head = f"new {'child' if one else 'children'} added"
    elif measure == "closures":
        head = f"{'case' if one else 'cases'} closed"
    elif measure == "sessions":
        head = "session" if one else "sessions"
    elif measure == "first_session_wait":
        head = f"{'child' if one else 'children'} seen for a first session"
    elif measure == "pre_assessment_duration":
        head = f"pre-assessment{'' if one else 's'} completed"
    else:
        head = f"pending pre-assessment{'' if one else 's'}"
    if measure in STAT_DATED:
        # `span` is the window actually counted, never the one asked for: a
        # by-month answer with no period counts six months, and saying "all
        # time" over it would state a number for a window it did not count.
        head += span
    if by != "none":
        head += f", by {_BY_WORDS[by]}"
    return head


def _sorted_rows(counts, order=None):
    """Rows in a stated order, or most first with the leftovers last."""
    if order is not None:
        rows = [(label, counts.get(label, 0)) for label in order]
        rows += [(label, n) for label, n in counts.items() if label not in order and n]
    else:
        rows = sorted(counts.items(),
                      key=lambda kv: (kv[0] in _LEFTOVERS, -kv[1], kv[0]))
    return [{"label": label, "count": n} for label, n in rows]


def _child_rows(children, by, today):
    """Children counted by one attribute, each the way its screen counts it."""
    from children.models import Child
    from clinical.reports import AGE_BANDS, UNSPECIFIED_AGE, age_band

    counts, order = {}, None
    for c in children:
        if by == "case_type":                      # Dashboard census
            label = c.case_type or "Unspecified"
        elif by == "case_stage":                   # Dashboard census
            label = c.get_case_status_display() if c.case_status else "Unspecified"
        elif by == "age_band":                     # Agency Summary, shared rule
            label = age_band(c.birth_date, today)
        elif by == "sex":                          # Agency Summary
            label = c.gender if c.gender in ("Male", "Female") else "Unspecified"
        else:                                      # psychologist — Agency Summary
            label = display_name(c.assigned_psychologist) or "Unassigned"
        counts[label] = counts.get(label, 0) + 1

    # The official form's order for age, and every band shown even at zero,
    # because the form shows them all. Stages and sexes likewise.
    if by == "age_band":
        order = [label for label, _, _ in AGE_BANDS] + (
            [UNSPECIFIED_AGE] if counts.get(UNSPECIFIED_AGE) else [])
    elif by == "case_stage":
        order = [label for value, label in Child.CASE_STATUS_CHOICES
                 if value != Child.STAGE_TERMINATED or counts.get(label)]
    elif by == "sex":
        order = ["Male", "Female"] + (["Unspecified"] if counts.get("Unspecified") else [])
    return _sorted_rows(counts, order)


def _month_rows(dates, start, end, today):
    """Month by month, oldest first, zero months included — a month with no
    intakes is part of the answer. Never past the current month."""
    from clinical.reports import bucket
    stop = min(end, _next_month(_month_start(today)))
    counts = {}
    for d in dates:
        key = bucket(d, "monthly")
        counts[key] = counts.get(key, 0) + 1
    rows, m = [], _month_start(start)
    while m < stop:
        rows.append({"label": m.strftime("%b %Y"),
                     "count": counts.get(bucket(m, "monthly"), 0)})
        m = _next_month(m)
    return rows


def _resolve_statistics(request, args):
    """Counts and breakdowns, scoped exactly as the screens scope them.

    Replaces count_my_children, which is `measure=children` here with its
    defaults filled in. A breakdown a measure does not have, or a period on
    something counted as of today, is answered with the total and a note —
    never refused, because the routing was right and the question deserves
    its number.
    """
    from django.utils import timezone
    from accounts.scoping import role_of, scope_to_visible
    from children.models import Child, TerminationRecord
    from clinical.models import PreAssessment

    measure, status = args.get("measure", "children"), args.get("status", "active")
    by, period = args.get("by", "none"), args.get("period")
    today = timezone.localdate()
    notes = []

    if by not in STAT_BY[measure]:
        notes.append(f"{_MEASURE_WORDS[measure].capitalize()} can't be broken down "
                     f"by {_BY_WORDS[by]} yet, so this is the total.")
        by = "none"
    if period and measure not in STAT_DATED:
        notes.append("Counted as of today — a period doesn't apply. For children "
                     "added in a period, ask about new intakes.")
        period = None
    start = end = None
    if period:
        start, end = period_range(period, today)
    elif by == "month":
        # No period asked for: the last six months, as the Dashboard shows.
        start = _month_start(today)
        for _ in range(5):
            start = _previous_month(start)
        end = _next_month(_month_start(today))

    # `median` is (days, what) for a measure the question asks about in days.
    rows, median = [], None
    if measure == "children":
        qs = scope_to_visible(Child.objects.all(), request, path=None)
        if status == "active":
            qs = qs.filter(status=Child.ACTIVE)
        elif status == "terminated":
            qs = qs.exclude(status=Child.ACTIVE)
        if by == "psychologist":
            qs = qs.select_related("assigned_psychologist")
        children = list(qs)
        total = len(children)
        if by != "none":
            rows = _child_rows(children, by, today)
    elif measure == "intake":
        qs = scope_to_visible(Child.objects.all(), request, path=None)
        if start:
            qs = qs.filter(created_at__date__gte=start, created_at__date__lt=end)
        dates = [timezone.localtime(c.created_at).date() for c in qs.only("created_at")]
        total = len(dates)
        if by == "month":
            rows = _month_rows(dates, start, end, today)
    elif measure == "closures":
        qs = scope_to_visible(TerminationRecord.objects.all(), request)
        if start:
            qs = qs.filter(date__gte=start, date__lt=end)
        closures = list(qs.only("date", "reason_category"))
        total = len(closures)
        if by == "reason":                         # Agency Summary
            counts = {}
            for t in closures:
                counts[t.reason_category] = counts.get(t.reason_category, 0) + 1
            rows = _sorted_rows(counts)
        elif by == "month":
            rows = _month_rows([t.date for t in closures], start, end, today)
    elif measure == "sessions":                    # Agency Summary's attendance card
        from clinical.reports import attendance
        from scheduling.models import Appointment
        qs, _own = _scope_appointments(request, Appointment.objects.only("status", "start"))
        if start:
            qs = qs.filter(start__date__gte=start, start__date__lt=end)
        att = attendance(qs, timezone.now())
        total = att["sessions"]
        if by == "status":
            rows = [{"label": label, "count": att[key]} for label, key in (
                ("Completed", "completed"), ("No-show", "no_show"),
                ("Not yet recorded", "unrecorded"), ("Upcoming", "upcoming"))
                if att[key] or key in ("completed", "no_show")]
        notes.insert(0, _attendance_note(att))
    elif measure == "first_session_wait":          # Agency Summary's wait card
        from clinical.reports import first_session_wait
        wait = first_session_wait(scope_to_visible(Child.objects.all(), request, path=None),
                                  today, start, end)
        total = wait["seen"]
        notes.insert(0, _wait_note(wait, dated=start is not None))
        median = (wait["median_days"], "median wait for a first session")
    elif measure == "pre_assessment_duration":     # Agency Summary's pre-assessment card
        from clinical.reports import pre_assessment_duration
        dur = pre_assessment_duration(scope_to_visible(PreAssessment.objects.all(), request),
                                      start, end)
        total = dur["completed"]
        notes.insert(0, _duration_note(dur, dated=start is not None))
        median = (dur["median_days"], "median time in pre-assessment")
    else:                                          # pending pre-assessments — Dashboard
        total = scope_to_visible(
            PreAssessment.objects.exclude(status=PreAssessment.COMPLETED), request).count()

    if period:
        span = " " + period.replace("_", " ")
    elif start:
        span = " in the last six months"
    else:
        span = ", all time"
    subject = _stat_subject(measure, status, by, span, total)
    # The figure the question asked for, when it is not the count: "how long
    # do children wait?" is answered in days. `total` stays the number of
    # children or pre-assessments behind it, so an answer about nobody still
    # reads as empty.
    figure = None
    if median and median[0] is not None:
        figure = {"value": _days(median[0]), "label": median[1] + span}
    return {"kind": "breakdown", "measure": measure, "by": by,
            "status": status if measure == "children" else None,
            "period": period, "total": total, "rows": rows,
            "subject": subject, "title": f"{total} {subject}",
            "note": " ".join(notes), "figure": figure,
            "screen": _stat_screen(measure, by, role_of(request))}


def _attendance_note(att):
    """The rate, and what it leaves out — said every time, because a rate whose
    denominator nobody can see invites the wrong reading."""
    parts = []
    if att["took_place"]:
        parts.append(f"No-show rate {att['no_show_rate']}% — {att['no_show']} of the "
                     f"{att['took_place']} sessions that took place.")
    else:
        parts.append("No session in this period has taken place yet, so there is "
                     "no no-show rate.")
    if att["unrecorded"]:
        parts.append(f"{att['unrecorded']} past "
                     f"{'session has' if att['unrecorded'] == 1 else 'sessions have'} "
                     "not been recorded yet and "
                     f"{'is' if att['unrecorded'] == 1 else 'are'} left out of the rate.")
    if att["cancelled"]:
        parts.append(f"{att['cancelled']} cancelled, not counted.")
    return " ".join(parts)


def _days(n):
    return f"{n} {'day' if n == 1 else 'days'}"


def _wait_note(wait, dated):
    """Who the median is about and who it leaves out — said every time, for
    the reason the no-show rate states its denominator."""
    seen = wait["seen"]
    if seen:
        parts = [f"{seen} {'child' if seen == 1 else 'children'} seen, counted from the "
                 "day the record was created to the first completed session. "
                 f"Longest wait {_days(wait['longest_days'])}."]
    elif dated:
        parts = ["No child had a first completed session in this period, so there "
                 "is no wait to measure."]
    else:
        parts = ["No child has had a completed session yet, so there is no wait "
                 "to measure."]
    if wait["waiting"]:
        n = wait["waiting"]
        parts.append(f"{n} active {'child is' if n == 1 else 'children are'} still "
                     f"waiting, the longest for {_days(wait['longest_waiting_days'])} "
                     "so far — not in the median.")
    if wait["before_record"]:
        n = wait["before_record"]
        parts.append(f"{n} not counted: the first session is dated before the "
                     f"record was created.")
    return " ".join(parts)


def _duration_note(dur, dated):
    """What the median counts and what it leaves out, as the wait's note does."""
    n = dur["completed"]
    if n:
        parts = [f"{n} {'pre-assessment' if n == 1 else 'pre-assessments'} completed, "
                 "counted from the start date to the day it was completed. "
                 f"Longest {_days(dur['longest_days'])}."]
    elif dated:
        parts = ["No pre-assessment was completed in this period, so there is no "
                 "time to measure."]
    else:
        parts = ["No pre-assessment has been completed yet, so there is no time "
                 "to measure."]
    if dur["open"]:
        parts.append(f"{dur['open']} still open — not counted until completed.")
    if dur["no_completion_date"]:
        parts.append(f"{dur['no_completion_date']} completed with no completion date "
                     "recorded — not counted.")
    if dur["completed_before_start"]:
        k = dur["completed_before_start"]
        parts.append(f"{k} marked completed before {'its' if k == 1 else 'their'} start "
                     "date — not counted.")
    return " ".join(parts)


def _stats_echo(a):
    """Built from the arguments alone, like every echo — it cannot see the
    caller, so it never says "your"."""
    measure = a.get("measure", "children")
    what = (f"{a.get('status', 'active')} children" if measure == "children"
            else _MEASURE_WORDS[measure])
    if a.get("period"):
        what += " " + a["period"].replace("_", " ")
    if a.get("by", "none") != "none":
        what += f" by {_BY_WORDS[a['by']]}"
    return f"Looking up: {what}"


def _singular(word):
    """"emotions" -> "emotion", "difficulties" -> "difficulty".

    icontains only looks one way: a record reading "expressing emotion" does
    not contain "emotions", so the plural the user typed has to be reduced
    before it is matched. Observed live on exactly that question.
    """
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 4:
        return word[:-1]
    return word


def _stem_ing(word):
    """"sleeping" -> "sleep".

    icontains only matches a shorter needle in a longer record, so the record
    "Sleep disturbance" does not contain "sleeping". Measured: "Who has trouble
    sleeping?" returned nothing 3 times out of 3 for exactly that reason, which
    is the confident-empty answer this search already exists to prevent.
    """
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    return word


# Function words long enough to survive the length filter and carry no
# clinical meaning. "with" is the one that was measured doing damage: it is a
# substring of "Withdrawal from peers", so "struggling with emotions" listed 10
# of 34 children whose actual concern was withdrawal — a confident, specific,
# wrong answer. Anything added here must be a word no concern is ever recorded
# as; "sleep" and "school" are not stopwords however common they look.
_CONCERN_STOPWORDS = {
    "with", "that", "this", "these", "those", "they", "them", "their",
    "there", "here", "have", "having", "been", "being", "from", "about",
    "what", "when", "which", "whom", "does", "doing", "will", "would",
    "could", "should", "than", "then", "also", "very", "much", "more",
    "most", "just", "like", "into", "onto", "upon", "your", "yours",
    "kung", "para", "yung", "iyong", "nila", "siya", "ang", "mga", "nang",
}


def _search_words(term):
    """Words worth matching on, singular and un-inflected forms included.

    Short words are dropped: "of" and "the" appear inside so many records that
    matching them would return the whole caseload as a false hit. Length alone
    is not enough — see _CONCERN_STOPWORDS.
    """
    words = {w for w in re.findall(r"[\w']+", term.lower())
             if len(w) >= 4 and w not in _CONCERN_STOPWORDS}
    return sorted(words | {_singular(w) for w in words}
                  | {_stem_ing(w) for w in words})


# Articles and honorifics that arrive attached to a name: "si Maria",
# "kay Ana po". They are not part of anybody's name.
_NAME_NOISE = {"si", "ni", "kay", "kina", "sina", "po", "ho", "ang", "yung",
               "iyong", "mr", "ms", "mrs", "sir", "maam", "ma"}


def _name_words(term):
    """Words from a name worth matching on.

    _search_words is not reusable here: it drops anything under four
    characters, which discards "Ana", "Jun" and "Lito" — exactly the names
    people are most likely to type on their own.
    """
    words = {w for w in re.findall(r"[\w']+", str(term or "").lower())
             if len(w) >= 2}
    return sorted(words - _NAME_NOISE)


def _resolve_concern(request, args):
    """Match on shared words, not on the whole phrase.

    Measured against live data: the model says "school refusal"; this agency
    records "School attendance difficulty". An exact substring search connects
    those never — it returned zero for the English term as readily as for the
    Tagalog one, and a confident empty answer is the worst kind.

    Matching any significant word bridges the two vocabularies. When nothing
    matches at all, the recorded concerns are returned instead, so a dead end
    becomes something the user can act on — which is also what catches a
    Tagalog phrase the model did not translate.
    """
    from django.db.models import Q
    from clinical.models import ProblemEntry

    term = args["concern"]
    open_problems = (ProblemEntry.objects
                     .filter(child__in=_scope(request), resolved=False)
                     .select_related("child"))

    hits = []
    query = Q()
    for word in _search_words(term):
        query |= Q(description__icontains=word) | Q(category__icontains=word)
    if query:
        hits = list(open_problems.filter(query))

    # Names, never the problem text: ProblemEntry is one of the two models the
    # carry-history block does not filter, and returning descriptions would
    # walk straight into that gap.
    seen, items = set(), []
    for p in hits:
        if p.child_id not in seen:
            seen.add(p.child_id)
            items.append({"id": p.child_id, "name": p.child.fullname})

    out = {"kind": "children", "concern": term, "items": items}
    if not items:
        out["available"] = sorted(
            {p.description for p in open_problems if p.description})[:12]
    return out


def _resolve_summary(request, args):
    from assistant.views import _brief_only_author, _role
    from clinical.care_gaps import compute_alerts
    from children.models import Child

    from django.db.models import Q

    # The exact phrase first, so a full name keeps matching exactly as before
    # and nothing gets looser. Only when that finds nothing does the search
    # widen to any single word — which is what rescues "Maria Reyes" for a
    # record reading "Maria Santos", and "si Maria" for "Maria".
    #
    # Before either: a name that is EXACTLY one child's recorded name means that
    # child, even when it also sits inside another record — "Maria Santos"
    # inside "Maria Santos-Cruz". Without this, choosing a name from the
    # "which one?" list asked the same question again. Two children recorded
    # under the identical name still come back as "several": a name cannot
    # tell them apart, and guessing between them would be worse.
    exact = list(_scope(request).filter(fullname__iexact=args["name"].strip())[:2])
    matches = (exact if len(exact) == 1 else
               list(_scope(request).filter(fullname__icontains=args["name"])[:6]))
    if not matches:
        query = Q()
        for word in _name_words(args["name"]):
            query |= Q(fullname__icontains=word)
        if query:
            matches = list(_scope(request).filter(query)[:6])
    if not matches:
        return {"kind": "summary", "match": "none", "name": args["name"]}
    if len(matches) > 1:
        return {"kind": "summary", "match": "several", "name": args["name"],
                "items": [{"id": c.id, "name": c.fullname} for c in matches]}

    child = matches[0]
    remarks = child.remarks.select_related("author")
    only = _brief_only_author(child, request.user, _role(request))
    if only is not None:
        remarks = remarks.filter(author=only)
    gaps = compute_alerts(Child.objects.filter(pk=child.pk))
    return {"kind": "summary", "match": "one", "child": {
        "id": child.id, "name": child.fullname, "status": child.status,
        "psychologist": display_name(child.assigned_psychologist) or None},
        "remarks": [{"date": str(r.date), "text": r.text} for r in remarks[:5]],
        "gaps": [a.get("type") for a in gaps]}


def _resolve_care_gaps(request, args):
    """Reuses the same alerts the Monitoring screen shows, so the chatbot can
    never disagree with the table the user is looking at.

    `message` is carried through rather than `type`: the type is a slug
    ("consent_missing") that means nothing to a psychologist, while the message
    is the sentence the screen already shows them.
    """
    from clinical.care_gaps import compute_alerts
    alerts = compute_alerts(_scope(request))
    return {"kind": "care_gaps", "items": [
        {"child": a.get("child_name") or a.get("child"),
         "type": a.get("type"), "message": a.get("message"),
         "severity": a.get("severity")}
        for a in alerts]}


# How many flags a single answer shows. The rest are counted, never silently
# dropped.
FLAG_PAGE = 20

# Role names as the model may say them, mapped to what the database stores.
_COUNTABLE_ROLES = {"psychologist": "Psychologist", "staff": "Staff",
                    "administrator": "Administrator"}


# Booking looks forward. "Who was free last week" is not a question anyone
# asks, and a past window cannot be booked — the serializer refuses a start in
# the past — so offering one produces a slot that can only be declined.
BOOKING_PERIODS = ("today", "tomorrow", "this_week", "next_week")


def _resolve_availability(request, args):
    """Which psychologists can take a child, and when.

    Staff and administrators see everyone, because staff are the ones who
    book. A psychologist sees their own, matching the calendar they manage.

    The windows come from scheduling.availability — the same function behind
    the booking screen's slot hints — so the assistant can never offer a slot
    the booking endpoint would then refuse.
    """
    from accounts.models import Role
    from assistant.views import _role
    from django.contrib.auth import get_user_model
    from scheduling import availability     # the module, so a test can patch it

    start, end = period_range(args["when"])
    people = get_user_model().objects.filter(
        role__role_name=Role.PSYCHOLOGIST, status="active")
    if _role(request) == Role.PSYCHOLOGIST:
        people = people.filter(pk=request.user.pk)

    items = []
    for person in people.order_by("last_name", "first_name"):
        for window in availability.free_windows(person, start, end):
            items.append({
                "psychologist": display_name(person),
                "email": person.email,
                **window,
            })
    return {"kind": "availability", "when": args["when"], "items": items[:25]}


def _resolve_count_people(request, args):
    """How many colleagues, by role.

    This is the question the assistant used to refuse. "How many psychologists
    are in the system?" came back "40 active children" in the browser, and the
    fix was a guard forcing it into a refusal — the right call for a missing
    tool, and the wrong one once the tool exists.

    Not scoped by caller: headcount is not case data, and refusing a
    psychologist the number of psychologists would be privacy theatre. Active
    accounts only, which is how the Users screen counts.
    """
    from django.contrib.auth import get_user_model

    role = args.get("role", "anyone")
    qs = get_user_model().objects.filter(status="active")
    if role != "anyone":
        qs = qs.filter(role__role_name=_COUNTABLE_ROLES[role])
    return {"kind": "people_count", "role": role, "count": qs.count()}


def _resolve_unassigned_children(request, args):
    """Children with no psychologist — a gap somebody has to close.

    Runs through _scope like everything else, which means a psychologist gets
    an empty list: their scope is children assigned to them, so an unassigned
    child cannot be in it. Empty is the honest answer, and the alternative
    would hand them the agency's whole caseload.
    """
    from children.models import Child

    qs = (_scope(request)
          .filter(assigned_psychologist__isnull=True, status=Child.ACTIVE)
          .order_by("fullname"))
    # Carries its own empty sentence. This reuses the `children` kind for the
    # list itself, and that renderer's own "no open concern matches that
    # wording" is about a different question entirely — seen in the browser
    # answering "which children have no psychologist?" with it.
    return {"kind": "children", "concern": "no assigned psychologist",
            "empty": "Every active child has a psychologist assigned.",
            "items": [{"id": c.id, "name": c.fullname} for c in qs[:40]]}


def _resolve_self_report_flags(request, args):
    """Children who said something worth reading, in their own words.

    Self-reports are exempt from the carry-history control: a child's own
    words are not a previous psychologist's opinions, so no author filter
    applies here. Scope still does, through the same helper as every other
    tool.

    The answer text is included because the child report screen already shows
    these expanded above the case notes, and a flag without the words is not
    something anyone can act on.
    """
    from django.utils import timezone
    from clinical.models import SelfReportFlag

    state = args.get("state", "unreviewed")
    qs = (SelfReportFlag.objects
          .filter(child__in=_scope(request))
          .select_related("child"))
    if state != "all":
        qs = qs.filter(reviewed_at__isnull=True)

    period = args.get("period")
    if period:
        start, end = period_range(period)
        qs = qs.filter(created_at__date__gte=start, created_at__date__lt=end)

    # The count comes back with the rows. Twenty of a hundred and sixty, shown
    # without saying so, reads as "twenty children flagged something" — and
    # these are children reporting distress, so a reader who believes they
    # have seen the whole list has been misled about the thing that matters
    # most here. The panel says how many were not shown.
    total = qs.count()
    return {"kind": "self_report_flags", "state": state, "total": total, "items": [
        {"child_id": f.child_id, "child": f.child.fullname,
         "question": f.question, "answer": f.answer,
         "date": str(timezone.localtime(f.created_at).date()),
         "reviewed": f.reviewed_at is not None}
        for f in qs[:FLAG_PAGE]]}


def _resolve_direct(request, args):
    from assistant.views import _role          # local: avoids a cycle

    reason = args.get("reason", "unsupported")
    if reason == "greeting_or_closing":
        text = GREETING_REPLY
    elif reason == "action_request":
        text = ACTION_REPLY
    else:
        text = capability_text(_role(request))
    return {"kind": "message", "reason": reason, "text": text}


REGISTRY = {
    "list_my_appointments": {
        "description": (
            # The past half of this was missing. The enum grew to cover
            # yesterday and last week while the description still described
            # only what was coming, so "What did I do last week?" went to
            # list_care_gaps 3 times out of 3 — the tool could answer it and
            # never said so.
            "The signed-in user's own schedule AND their session history: "
            "appointments, sessions and visits — who they ARE SEEING on a "
            "coming day, and what they ALREADY DID on a past one. Use for any "
            "question about the calendar, the schedule, or work already done "
            "in a period. Do NOT use this to search for children."),
        "schema": {"when": {"enum": list(APPOINTMENT_PERIODS), "required": True}},
        # Not "your": the echo sees only the arguments, and an administrator
        # asking this is looking at the agency's calendar, not their own.
        "echo": lambda a: f"Looking up: appointments {a['when'].replace('_', ' ')}",
        "resolve": _resolve_appointments,
    },
    "get_statistics": {
        # Replaces count_my_children, which is measure=children here, in the
        # same position in the tools array.
        #
        # Deliberately no "use for questions starting 'how many'". That phrase
        # in count_my_children's description is what answered "how many
        # psychologists are in the system?" with a child count. What this
        # counts is named by its subject, children and cases, not by the shape
        # of the question.
        "description": (
            "Numbers about children and cases: the count of children, and how "
            "they break down by case type, case stage, age group, sex or the "
            "psychologist they are assigned to; new intakes and case closures "
            "in a period, and closures by reason; pre-assessments still "
            "pending; attendance — sessions held, no-shows and the no-show "
            "rate in a period; how long children wait for a first session; "
            "how long pre-assessments take from start to completion. Use for a "
            "count of children or cases, for attendance, waiting times or "
            "pre-assessment times, or for breaking them down by a category or "
            "by month."),
        "schema": {
            "measure": {"enum": list(STAT_MEASURES), "required": False,
                        "default": "children"},
            "status": {"enum": ["active", "terminated", "any"], "required": False,
                       "default": "active"},
            "by": {"enum": list(STAT_BREAKDOWNS), "required": False,
                   "default": "none"},
            "period": {"enum": list(PERIODS), "required": False},
        },
        "echo": _stats_echo,
        "resolve": _resolve_statistics,
    },
    "search_children_by_concern": {
        "description": (
            # An example here becomes an argument: adding 'trouble sleeping'
            # made the model pass it verbatim, and "sleeping" does not match
            # the recorded "Sleep disturbance" — 3 empty answers out of 3. Any
            # example added here has to exist in the agency's own vocabulary.
            "Find children whose presenting concern or problem matches a "
            "description, for example 'school refusal', 'anxiety', "
            "'withdrawal', 'struggling with emotions'. "
            "This is the tool for ANY question about what children are "
            "struggling with, however it is worded. "
            "Always pass the concern the user named."),
        "schema": {"concern": {"required": True}},
        "echo": lambda a: f"Looking up: children with '{a['concern']}'",
        "resolve": _resolve_concern,
    },
    "get_child_summary": {
        "description": (
            "The case summary for ONE named child. Use whenever the user names "
            "a specific child and wants their history, rundown or summary."),
        "schema": {"name": {"required": True}},
        "echo": lambda a: f"Looking up: {a['name']}",
        "resolve": _resolve_summary,
    },
    "list_care_gaps": {
        "description": (
            "Children with overdue follow-ups, missing pre-assessments or "
            "missing reports. Use for 'who needs follow-up', 'who is overdue'."),
        "schema": {},
        "echo": lambda a: "Looking up: children needing follow-up",
        "resolve": _resolve_care_gaps,
    },
    "find_availability": {
        "description": (
            # The distinction that matters is booked versus bookable.
            # list_my_appointments answers what is already ON the calendar;
            # this answers what is still OPEN on it.
            "FREE slots that can still be BOOKED — which psychologists have "
            "room to take a child, and when. Use for 'who is free', 'who is "
            "available', 'when can we book'. Do NOT use for what is already "
            "scheduled; list_my_appointments answers that."),
        "schema": {"when": {"enum": list(BOOKING_PERIODS), "required": True}},
        "echo": lambda a: f"Looking up: free slots {a['when'].replace('_', ' ')}",
        "resolve": _resolve_availability,
    },
    "count_people": {
        "description": (
            "How many PEOPLE WHO WORK HERE — psychologists, staff, "
            "administrators, user accounts. Use for 'how many psychologists', "
            "'ilan ang staff', 'how many users are in the system'. This "
            "counts colleagues, never children."),
        "schema": {"role": {"enum": ["psychologist", "staff", "administrator",
                                     "anyone"], "required": True}},
        "echo": lambda a: (f"Looking up: how many {a['role']}s"
                           if a["role"] != "anyone"
                           else "Looking up: how many people work here"),
        "resolve": _resolve_count_people,
    },
    "list_unassigned_children": {
        "description": (
            # No Tagalog example here, deliberately. 'sino ang walang
            # psychologist' pulled "sino ang mga bata na ayaw pumasok sa
            # eskwela" — a concern question — into this tool 3 times out of 3,
            # because the model matched the shape of the phrase rather than its
            # subject. CHAT_SYSTEM carries the Tagalog framing for every tool;
            # a niche tool does not need its own.
            "Children who have NO PSYCHOLOGIST ASSIGNED to them — an "
            "assignment gap, not a clinical concern. Use only for 'who is "
            "unassigned' or 'which children have no psychologist yet'. For "
            "what a child is struggling with, use "
            "search_children_by_concern."),
        "schema": {},
        "echo": lambda a: "Looking up: children with no psychologist",
        "resolve": _resolve_unassigned_children,
    },
    "list_self_report_flags": {
        "description": (
            # Purely positive, and it names no clinical vocabulary at all.
            # A "Do NOT use for anxiety, emotions, sleep" clause was measured
            # WORSE: the router reads the keywords and drops the negation, so
            # listing what this tool is not for is how it got picked for
            # "struggling with emotions" 3 times in 3.
            "Children flagged from a survey THE CHILD FILLED IN THEMSELVES. "
            "Use only when the question asks what has been 'flagged', or what "
            "children have said or written about themselves in their own "
            "words."),
        "schema": {
            "state": {"enum": ["unreviewed", "all"], "required": False},
            "period": {"enum": list(PERIODS), "required": False},
        },
        "echo": lambda a: "Looking up: flagged self-reports",
        "resolve": _resolve_self_report_flags,
    },
    "answer_directly": {
        "description": (
            # It used to claim "how many psychologists, staff or users" as
            # well — written before count_people existed, and never removed
            # when that tool took the question over. Two descriptions claiming
            # one question is a routing coin-toss, and the tools array is read
            # as one prompt.
            "Use when NO other tool fits: greetings, thanks, sign-offs, general "
            "knowledge, questions about what words mean, questions about the "
            "system itself, or anything not about this user's schedule, "
            "children or caseload."),
        # `reason` is telemetry, not logic — the response is the same fixed
        # copy either way. Requiring it turned the one case in 28 where the
        # model omitted it into a failed turn, so it defaults instead.
        "schema": {"reason": {"enum": ["greeting_or_closing", "general_knowledge",
                                       "unsupported"],
                              "required": False}},
        "echo": lambda a: "",
        "resolve": _resolve_direct,
    },
}


# --- follow-ups -------------------------------------------------------------
#
# Refinements of an answer, offered as ready-made calls the panel runs WITHOUT
# the model: "By sex?", "This week?", a name from a "which one?" list. They
# cannot misroute because nothing routes them, and they answer in milliseconds
# rather than seconds.
#
# They are not a new trust boundary. The model's output was always untrusted
# input to validate(); a call the panel sends goes through the same validator,
# and every resolver takes scope from request.user, never from arguments — so a
# forged chip reaches nothing a perfectly behaved model could not.

FOLLOWUP_LIMIT = 4

# Periods one step either side, for the schedule and availability answers.
_NEAR_WHEN = {
    "today": ("tomorrow", "this_week"), "tomorrow": ("today", "this_week"),
    "yesterday": ("today", "last_week"), "this_week": ("last_week", "next_week"),
    "last_week": ("this_week", "last_month"), "next_week": ("this_week",),
    "this_month": ("last_month", "this_week"), "last_month": ("this_month",),
}
_NEAR_BOOKING = {"today": ("tomorrow", "this_week"), "tomorrow": ("this_week", "next_week"),
                 "this_week": ("next_week",), "next_week": ("this_week",)}
_PERIOD_CHIP = {p: p.replace("_", " ").capitalize() + "?" for p in PERIODS}
_BY_CHIP = {"case_type": "By case type?", "age_band": "By age group?", "sex": "By sex?",
            "psychologist": "By psychologist?", "case_stage": "By case stage?",
            "reason": "By reason?", "month": "Month by month?", "status": "By status?"}
# Most asked-for first: the NACC form's own breakdowns, then caseload.
_BY_ORDER = ("case_type", "age_band", "sex", "psychologist", "case_stage",
             "reason", "month", "status")


def _followup_statistics(args, result, role):
    from accounts.models import Role
    measure, by = result["measure"], result["by"]
    for b in _BY_ORDER:
        # A psychologist's "by psychologist" is one row with their own name.
        if (b in STAT_BY[measure] and b != by
                and not (b == "psychologist" and role == Role.PSYCHOLOGIST)):
            yield _BY_CHIP[b], {**args, "by": b}
    if measure in STAT_DATED:
        for p in ("this_month", "this_year", "last_year"):
            if p != result["period"]:
                yield _PERIOD_CHIP[p], {**args, "period": p}


def _followup_appointments(args, result, role):
    for p in _NEAR_WHEN.get(args["when"], ()):
        yield _PERIOD_CHIP[p], {"when": p}


def _followup_availability(args, result, role):
    for p in _NEAR_BOOKING.get(args["when"], ()):
        yield _PERIOD_CHIP[p], {"when": p}


def _followup_flags(args, result, role):
    if args.get("state", "unreviewed") != "all":
        yield "Include reviewed ones?", {**args, "state": "all"}
    for p in ("this_month", "this_year"):
        if p != args.get("period"):
            yield _PERIOD_CHIP[p], {**args, "period": p}


def _followup_summary(args, result, role):
    # "Several children match — which one?" becomes a choice, not a retype.
    # Exact full names: the resolver lets an exact match win, so this lands.
    if result.get("match") == "several":
        for item in result["items"]:
            yield item["name"], {"name": item["name"]}


_FOLLOWUPS = {
    "get_statistics": _followup_statistics,
    "list_my_appointments": _followup_appointments,
    "find_availability": _followup_availability,
    "list_self_report_flags": _followup_flags,
    "get_child_summary": _followup_summary,
}
# The only tools the follow-up endpoint will run: the ones chips come from.
FOLLOWUP_TOOLS = frozenset(_FOLLOWUPS)


def followups(call, result, role):
    """Refinements of this answer as [{label, tool, args}], ready to send back.

    Each offer is put through validate() before it is made, so a chip can never
    produce a call the server would refuse — and the args sent back are the
    validated ones, defaults and all.
    """
    make = _FOLLOWUPS.get(call.tool)
    if not call.ok or make is None or result.get("kind") == "message":
        return []
    limit = 6 if call.tool == "get_child_summary" else FOLLOWUP_LIMIT
    out = []
    for label, raw in make(call.args, result, role):
        offer = validate(call.tool, raw)
        if offer.ok:
            out.append({"label": label, "tool": call.tool, "args": offer.args})
        if len(out) == limit:
            break
    return out


def result_size(result):
    """How much a lookup found. One definition, used by the chat log and by
    ai_eval, so "answered with nothing" means the same thing in both.

    None when the reply was not a lookup at all: a greeting or a refusal has
    no size, and counting it as 0 would file every "salamat po" under the
    empty answers.

    A found child is ONE answer, not zero. The summary has no `items` when it
    finds exactly one, and an item count read "Tell me about Maria" — answered
    in full — as found nothing.
    """
    kind = result.get("kind")
    if kind == "message":
        return None
    if kind == "summary":
        match = result.get("match")
        if match == "one":
            return 1
        return len(result.get("items", [])) if match == "several" else 0
    # Counts are the answer themselves; paged lists carry their real total.
    for key in ("count", "total"):
        if key in result:
            return result[key]
    return len(result.get("items", []))


def ollama_payload():
    """The tools array for /api/chat, derived from REGISTRY.

    Built rather than hand-written so a tool cannot be added to one and
    forgotten in the other.
    """
    out = []
    for name, spec in REGISTRY.items():
        props, required = {}, []
        for param, meta in spec["schema"].items():
            prop = {"type": "string"}
            if "enum" in meta:
                prop["enum"] = meta["enum"]
            props[param] = prop
            if meta.get("required"):
                required.append(param)
        out.append({"type": "function", "function": {
            "name": name, "description": spec["description"],
            "parameters": {"type": "object", "properties": props,
                           "required": required}}})
    return out
