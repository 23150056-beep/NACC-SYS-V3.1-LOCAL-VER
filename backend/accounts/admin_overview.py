"""The ISA's console: one request that answers "who is waiting on me, is
anything broken, who has access to what" (GET /api/admin/overview/).

The ISA is the agency's IT support and not the Head of Office, so this is a
list of things for them to do and to check, not a report on the agency. It is
built from plain queries over the tables that already hold the answer; nothing
here writes, calls a gateway or asks the assistant anything. Each "Test"
button on the screen goes to the endpoint that already tests that one thing.

What it must never carry is kept out by the module's shape rather than by a
promise: it imports no model that holds case text (the case study, remarks,
reports, interviews, plans), reads no answer a child gave, and the assistant's
audit rows are taken with a `values_list` that names its columns, so a job's
text is never loaded. accounts/tests/test_admin_overview.py plants a sentinel
in each and fails if one comes back.

Every number is a count or a short list, so the query count depends on how
many kinds of thing there are and not on how many children or users exist.
"""
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Lower
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.lockout import ADDRESS_LOCK_LABEL, NOT_AN_EMAIL, readable_sign_in_name
from accounts.models import Role, SmsSend, User
from accounts.names import REPLACEMENT
from accounts.permissions import IsAdministrator
from accounts.sms import LIMITED, PROVIDERS, SENT, UNKNOWN
from activity.models import ActivityLog
from assistant.models import AssistantJob, AssistantSetting
from assistant.services import hosted_model_configured
from children.duplicates import duplicate_groups
from children.models import AssignmentRequest, Child
from clinical.care_gaps import ASSIGNMENT_ANSWER_DAYS
from clinical.models import CaseReferral, ConsentRecord, PsychologicalReport
from config.clock import clock
from config.health import _database_identity
from scheduling.models import (AvailabilityBlock, CustodianReminder, SessionReminder,
                               Unavailability)
from scheduling.visibility import case_ref

# Taken when this module is first imported, which accounts/apps.py does as the
# app starts. A module a URL pulls in lazily would be stamped by the first
# request instead, and a worker that had been up for days would say it had
# just started. BOOT_COMMIT (below `_git_commit`) is read at the same moment for
# the same reason.
STARTED_AT = timezone.now()

QUEUE_ITEMS = 10          # rows a queue shows; the count above them is exact
PEOPLE_ROWS = 100         # a safety bound; the directory is a few dozen people
LOG_ROWS = 30
LOG_DAYS = 7
DORMANT_DAYS = 30
LOCKOUT_HOURS = 24
LEAVE_DAYS_AHEAD = 6      # on_leave looks at today through today + 6
# A text claimed this long ago and still not stamped sent has stopped.
UNCONFIRMED_AFTER = timedelta(minutes=15)

LOCKED_PREFIX = "Login locked for "     # accounts/views.py LoginView._register_failure
LOCKED_SUFFIX = " after repeated failures"

# Lines that a stranger, or any signed-in account, can cause as fast as they
# like, picked out by WHO wrote them and HOW, never by their words. The words
# are the one thing an outsider controls: a sign-up under the name "Login locked
# for ..." or "... access approved as Administrator" writes a label that reads
# like another kind of line. What the code that wrote a line set is what
# counts: the action, the category, whether anybody was signed in, and for the
# updates only an administrator makes, ActivityLog.kind.
_LOCKOUT = Q(category=ActivityLog.SECURITY, action=ActivityLog.UPDATED, actor__isnull=True,
             entity_id__isnull=True, entity_type="User",
             entity_label__startswith=LOCKED_PREFIX, entity_label__endswith=LOCKED_SUFFIX)
# The lock on a whole address (accounts/lockout.py). Written by the same view
# in the same way, so it is told apart by its whole fixed sentence.
_ADDRESS_LOCKOUT = Q(category=ActivityLog.SECURITY, action=ActivityLog.UPDATED,
                     actor__isnull=True, entity_id__isnull=True, entity_type="User",
                     entity_label=ADDRESS_LOCK_LABEL)
_ANY_LOCKOUT = _LOCKOUT | _ADDRESS_LOCKOUT
_REQUEST = Q(category=ActivityLog.SECURITY, action=ActivityLog.CREATED, actor__isnull=True,
             entity_type="User")
_EXPORT = Q(action=ActivityLog.EXPORTED)
# An account reached the limit on the emails its actions cause
# (accounts/email_notifications.py). Written by a signed-in account's own
# action, as fast as once an hour, so it is a bulk kind; picked out by its
# `kind` and its category, which only that code sets, and not by the sentence.
_EMAIL_LIMIT = Q(category=ActivityLog.SECURITY, kind=ActivityLog.KIND_EMAIL_LIMIT)

NEED_KEYS = ("access_requests", "lockouts", "temp_passwords", "stuck_requests",
             "no_social_worker", "archived_holder", "names_to_fix", "duplicates")


# --- small things every section uses ---------------------------------------


def _iso(moment):
    """ISO-8601 with the server's offset, to the second."""
    if moment is None:
        return None
    return timezone.localtime(moment).replace(microsecond=0).isoformat()


def _person(user):
    """The name the ISA knows an account by. This is an administrator-only
    answer, and the directory they already read shows the address, so it is
    the fallback here where it never is on a screen anybody can open."""
    return (user.fullname or user.email) if user is not None else ""


def _name_of(first, middle, last, email):
    return " ".join(p for p in (first, middle, last) if p) or email


def _short(user):
    """"M. Bulan": enough to know who, short enough for one line."""
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    if first and last:
        return f"{first[0]}. {last}"
    return _person(user)


def _span(days):
    return "less than a day" if days < 1 else f"{days} day{'' if days == 1 else 's'}"


def _days_since(then, now):
    return max(0, (now - then).days)


def _ago(then, now):
    days = _days_since(then, now)
    return "today" if days == 0 else f"{_span(days)} ago"


def _when(moment, now):
    """"today at 3:12 PM", "yesterday at 9:05 PM", "Oct 12 at 8:00 AM"."""
    local = timezone.localtime(moment)
    day = local.date()
    today = timezone.localdate(now)
    if day == today:
        lead = "today"
    elif day == today - timedelta(days=1):
        lead = "yesterday"
    else:
        lead = f"{local:%b} {local.day}"
    return f"{lead} at {clock(local)}"


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _child_label(child):
    return f"{case_ref(child.pk)} · {child.fullname}"


def _child_link(pk):
    return f"/children?edit={pk}"


def _item(label, sub, link):
    return {"label": label, "sub": sub, "link": link}


def _queue(key, group, count, hint, link, items):
    return {"key": key, "group": group, "count": count, "hint": hint,
            "link": link, "items": items[:QUEUE_ITEMS]}


# --- needs: who is waiting on the ISA --------------------------------------


def _access_requests(now):
    pending = User.objects.filter(status=User.PENDING)
    stats = pending.aggregate(
        n=Count("id"), oldest=Min("created_at"),
        unconfirmed=Count("id", filter=Q(email_verified=False)))
    count = stats["n"]
    if not count:
        hint = "No one is waiting for access"
    else:
        hint = f"Oldest {_span(_days_since(stats['oldest'], now))}"
        if stats["unconfirmed"]:
            hint += f" · {_plural(stats['unconfirmed'], 'email', 'emails')} unconfirmed"
    items = []
    # Longest wait first: a request is a person who cannot work yet.
    for user in pending.select_related("requested_role").order_by("created_at", "id")[:QUEUE_ITEMS]:
        asked = (f"Asked for {user.requested_role.role_name}"
                 if user.requested_role else "No role stated")
        if user.google_sub:
            door = "Google sign-in"
        else:
            door = "email confirmed" if user.email_verified else "email not confirmed"
        items.append(_item(_person(user), f"{asked} · {door} · {_ago(user.created_at, now)}",
                           "/users?tab=requests"))
    return _queue("access_requests", "people", count, hint, "/users?tab=requests", items)


def _lockouts(now):
    """Lockouts that HAPPENED in the last day, not the ones still in force.

    The lock itself lives in a per-process cache (accounts/lockout.py), which
    this request cannot list and which another worker would not share. The
    audit row written when a lock is set is the only record that outlives the
    process, so that is what is counted, and the hint says so. A lock on a
    whole address is one of them: it turns away everyone behind that address,
    the ISA among them, so it is listed beside the per-account ones.
    """
    since = now - timedelta(hours=LOCKOUT_HOURS)
    rows = ActivityLog.objects.filter(_ANY_LOCKOUT, created_at__gte=since)
    count = rows.count()
    recent = list(rows.order_by("-created_at", "-id")[:QUEUE_ITEMS])
    names = [None if r.entity_label == ADDRESS_LOCK_LABEL else _locked_name(r.entity_label)
             for r in recent]
    wanted = {n.lower() for n in names if n and n != NOT_AN_EMAIL}
    known = {}
    if wanted:
        known = dict(User.objects.annotate(e=Lower("email"))
                     .filter(e__in=wanted).values_list("e", "id"))
    items = []
    for row, name in zip(recent, names):
        if name is None:
            items.append(_item(
                "Every sign-in from one address",
                f"Locked {_when(row.created_at, now)} · everyone behind that address is "
                f"turned away for {_plural(settings.LOGIN_LOCKOUT_MINUTES, 'minute', 'minutes')}",
                None))
            continue
        user_id = known.get(name.lower())
        items.append(_item(name, f"Locked {_when(row.created_at, now)}",
                           f"/users?edit={user_id}" if user_id else None))
    if not count:
        hint = "No sign-in lockouts in the last 24 hours"
    else:
        hint = f"Last 24 hours · latest: {names[0] or 'a whole address'}"
    return _queue("lockouts", "people", count, hint, None, items)


def _locked_name(label):
    """The sign-in name in a lockout line, or NOT_AN_EMAIL.

    Lines written before the login view kept only email-shaped names can hold
    anything that was typed, a password included, so the name is judged again
    here. The slice cannot go wrong on a short label: the prefix and suffix may
    overlap in one, and the middle is then simply empty.
    """
    return readable_sign_in_name(label[len(LOCKED_PREFIX):len(label) - len(LOCKED_SUFFIX)])


def _password_issued_at(user_ids):
    """When each of these accounts was last handed a temporary password.

    The audit trail is the only place that says: the account row only knows
    the flag is up. A creation and a reset both hand one over, and so does a
    reactivation (it forces a change), so those three count. The reset and the
    reactivation are found by the kind they were written with, not by their
    wording, which starts with a name anyone could have chosen. Accounts made
    or reset before the lines were told apart from an ordinary edit have no
    answer, and get None rather than a guess.
    """
    issued = {}
    if not user_ids:
        return issued
    rows = (ActivityLog.objects
            .filter(category=ActivityLog.USER, entity_type="User", entity_id__in=user_ids)
            .filter(Q(action=ActivityLog.CREATED)
                    | Q(kind__in=(ActivityLog.KIND_RESET, ActivityLog.KIND_REACTIVATE)))
            .order_by("created_at", "id")
            .values_list("entity_id", "created_at"))
    for user_id, at in rows:      # oldest first, so the newest is what stays
        issued[user_id] = at
    return issued


def _temp_password_holders(now):
    holders = list(User.objects.filter(status=User.ACTIVE, must_change_password=True)
                   .select_related("role").order_by("id"))
    issued = _password_issued_at([u.pk for u in holders])
    rows = [{"user": u, "since": issued.get(u.pk)} for u in holders]
    # Waiting longest first; an account whose date is unknown follows them.
    rows.sort(key=lambda r: (r["since"] is None, r["since"] or now, r["user"].pk))
    return rows


def _temp_passwords(now, holders):
    count = len(holders)
    known = [r["since"] for r in holders if r["since"]]
    if not count:
        hint = "Everyone has chosen their own password"
    elif known:
        hint = f"Oldest issued {_ago(min(known), now)}"
    else:
        hint = "Issued, and not yet changed by the person"
    items = []
    for r in holders[:QUEUE_ITEMS]:
        role = r["user"].role.role_name if r["user"].role else "No role"
        issued = f"issued {_ago(r['since'], now)}" if r["since"] else "issue date not recorded"
        items.append(_item(_person(r["user"]), f"{role} · {issued}", f"/users?edit={r['user'].pk}"))
    return _queue("temp_passwords", "people", count, hint, None, items)


def _stuck_requests(now):
    """A psychologist who has not answered, and a child nobody asked again.

    The first is `care_gaps.ASSIGNMENT_ANSWER_DAYS`, the same week the social
    worker's Dashboard waits before it counts a pending request as a gap. The
    second is the case the rules in children/assignment.py leave behind: a
    decline needs a reason and nothing asks anyone else, so a child whose
    latest answer was "no" and who has no psychologist and no open question
    waits for a person to notice.
    """
    cutoff = now - timedelta(days=ASSIGNMENT_ANSWER_DAYS)
    silent = AssignmentRequest.objects.filter(
        status=AssignmentRequest.PENDING, created_at__lte=cutoff, child__status=Child.ACTIVE)
    silent_count = silent.count()

    latest = AssignmentRequest.objects.filter(child=OuterRef("pk")).order_by("-created_at", "-id")
    still_open = AssignmentRequest.objects.filter(
        child=OuterRef("pk"), status=AssignmentRequest.PENDING)
    declined = (Child.objects
                .filter(status=Child.ACTIVE, assigned_psychologist__isnull=True)
                .annotate(last_status=Subquery(latest.values("status")[:1]),
                          last_request=Subquery(latest.values("pk")[:1]),
                          last_decided=Subquery(latest.values("decided_at")[:1]))
                .filter(last_status=AssignmentRequest.DECLINED)
                .filter(~Exists(still_open)))
    declined_count = declined.count()

    rows = []
    for req in (silent.select_related("child", "psychologist")
                .order_by("created_at", "id")[:QUEUE_ITEMS]):
        rows.append((req.created_at, _item(
            _child_label(req.child),
            f"Asked {_short(req.psychologist)} {_ago(req.created_at, now)}",
            _child_link(req.child_id))))
    # The longest-declined first, so the ten shown are the ten most overdue.
    ids = list(declined.order_by("last_decided", "pk")
               .values_list("last_request", flat=True)[:QUEUE_ITEMS])
    if ids:
        for req in (AssignmentRequest.objects.filter(pk__in=ids)
                    .select_related("child", "psychologist")):
            # The psychologist's reason is a clinician's own words and can
            # carry clinical detail, and this screen shows no record's text.
            # The ISA reads it on the child's record, where it already is.
            decided = req.decided_at or req.created_at
            rows.append((decided, _item(
                _child_label(req.child),
                f"Declined by {_short(req.psychologist)} {_ago(decided, now)}",
                _child_link(req.child_id))))
    rows.sort(key=lambda r: r[0])

    parts = []
    if silent_count:
        parts.append(f"{silent_count} unanswered for {ASSIGNMENT_ANSWER_DAYS}+ days")
    if declined_count:
        parts.append(f"{declined_count} declined with nobody asked again")
    hint = " · ".join(parts) or "Every request is answered or recent"
    return _queue("stuck_requests", "records", silent_count + declined_count, hint,
                  "/children", [item for _at, item in rows])


def _no_social_worker(now):
    orphans = Child.objects.filter(status=Child.ACTIVE, social_worker__isnull=True)
    count = orphans.count()
    hint = ("Only you can open them until a social worker is assigned" if count
            else "Every active record has a social worker")
    items = [_item(_child_label(c),
                   f"{c.case_type or 'No case type'} · added {_ago(c.created_at, now)}",
                   _child_link(c.pk))
             for c in orphans.order_by("created_at", "id")[:QUEUE_ITEMS]]
    return _queue("no_social_worker", "records", count, hint, "/children", items)


def _archived_holder(now):
    """Records whose social worker or psychologist can no longer sign in.
    Archiving an account moves nothing (CLAUDE.md, "Each social worker's own
    records"), so these sit with a holder nobody can reach until the ISA
    transfers them. A psychologist's caseload is the same case: the child
    stays "assigned" to an account that cannot open a single page."""
    social_gone = Q(social_worker__isnull=False) & ~Q(social_worker__status=User.ACTIVE)
    psych_gone = Q(assigned_psychologist__isnull=False) & ~Q(
        assigned_psychologist__status=User.ACTIVE)
    stranded = Child.objects.filter(status=Child.ACTIVE).filter(social_gone | psych_gone)
    stats = stranded.aggregate(
        n=Count("id", distinct=True),
        social=Count("social_worker", filter=social_gone, distinct=True),
        psych=Count("assigned_psychologist", filter=psych_gone, distinct=True))
    count = stats["n"]
    holders = stats["social"] + stats["psych"]
    hint = (f"Held by {_plural(holders, 'deactivated account', 'deactivated accounts')}"
            if count else "No record is held by a deactivated account")
    items = []
    rows = (stranded.select_related("social_worker", "assigned_psychologist")
            .order_by("id")[:QUEUE_ITEMS])
    for c in rows:
        held = []
        if c.social_worker_id and c.social_worker.status != User.ACTIVE:
            held.append(f"Social worker {_person(c.social_worker)} (inactive)")
        if c.assigned_psychologist_id and c.assigned_psychologist.status != User.ACTIVE:
            held.append(f"Psychologist {_person(c.assigned_psychologist)} (inactive)")
        items.append(_item(_child_label(c), " · ".join(held), _child_link(c.pk)))
    return _queue("archived_holder", "records", count, hint, "/children", items)


def _names_to_fix(now):
    """An account saved with U+FFFD in its name (accounts/names.py): the letter
    that did not survive a Latin-1 form body. Refused where names are written
    now, so these are the ones saved before that, or edited around it.

    Child records are not listed, though they can carry the same letter: a
    record's name cannot be changed after it is created (children/serializers.py),
    so a row for one could be opened and never cleared, and a queue that cannot
    empty stops being read. An account's name is retyped in Users.
    """
    users = (User.objects.exclude(status=User.ARCHIVED)
             .filter(Q(first_name__contains=REPLACEMENT)
                     | Q(middle_initial__contains=REPLACEMENT)
                     | Q(last_name__contains=REPLACEMENT)))
    count = users.count()
    hint = (f"{_plural(count, 'account', 'accounts')} with a letter that did not save; "
            f"retype the name" if count else "No account name has an unreadable letter")
    items = [_item(_person(u), "Account", f"/users?edit={u.pk}")
             for u in users.order_by("id")[:QUEUE_ITEMS]]
    return _queue("names_to_fix", "records", count, hint, None, items)


def _search_text(name):
    """A name as Records will look for it: one space between words, composed
    letters. Records folds both sides the same way, so a last name stored as
    N + combining tilde still finds the record that holds it composed."""
    return " ".join(unicodedata.normalize("NFC", name or "").split())


def _duplicates(now):
    groups = duplicate_groups()
    groups.sort(key=lambda g: g[0].pk)
    records = sum(len(g) for g in groups)
    hint = (f"{_plural(len(groups), 'child', 'children')} with more than one record "
            f"({records} records)" if groups else "No child has more than one record")
    # Open searches Records, which matches a plain substring of the full name.
    # The records of one child can differ in the middle name ("Ana M. Peña" and
    # "Ana Peña"), so the full name would find only one of them; the last name is
    # in every one, since `duplicate_groups` only groups records that share it.
    # A group can hold a closed record (the rule counts them), and Records opens
    # on its Active tab, so the link asks for all of them.
    items = []
    for group in groups[:QUEUE_ITEMS]:
        first = group[0]
        refs = [case_ref(c.pk) + ("" if c.status == Child.ACTIVE else " (closed)")
                for c in group]
        born = first.birth_date
        items.append(_item(
            f"{first.fullname} · born {born:%b} {born.day}, {born.year}",
            (", ".join(refs[:-1]) + f" and {refs[-1]}"),
            f"/children?q={quote(_search_text(first.last_name))}&status=all"))
    return _queue("duplicates", "records", len(groups), hint, "/children", items)


# --- health: is anything broken --------------------------------------------


def _health_item(key, status, title, detail, test=None):
    return {"key": key, "status": status, "title": title, "detail": detail, "test": test}


def _server_health():
    engine, host = _database_identity()
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:
        return _health_item("server", "bad", "Server",
                            f"Up, but the database did not answer · {type(exc).__name__}")
    ms = max(1, round((time.perf_counter() - started) * 1000))
    return _health_item("server", "ok", "Server",
                        f"Up · {engine} ({host}) · answered in {ms} ms")


_SHA = re.compile(r"^[0-9a-f]{40}$")


def _git_commit():
    """The checked-out commit, read from the repository's own files.

    No subprocess: this answers a request, git may not be installed where the
    API runs, and a plain read cannot be talked into running anything. Follows
    HEAD to its branch file, then to packed-refs, and handles a linked
    worktree (`.git` is a file pointing at the real directory, whose
    `commondir` holds the shared refs). None for anything it cannot read,
    which on a deployed image is the usual case - there is no .git there.
    """
    try:
        root = Path(settings.BASE_DIR).parent
        git = root / ".git"
        if git.is_file():
            pointer = git.read_text().strip()
            if not pointer.startswith("gitdir:"):
                return None
            git = (root / pointer[len("gitdir:"):].strip()).resolve()
        head = (git / "HEAD").read_text().strip()
        if _SHA.match(head):
            return head                       # detached
        if not head.startswith("ref:"):
            return None
        ref = head[len("ref:"):].strip()
        shared = git
        if (git / "commondir").is_file():
            shared = (git / (git / "commondir").read_text().strip()).resolve()
        for base in (git, shared):
            if (base / ref).is_file():
                sha = (base / ref).read_text().strip()
                return sha if _SHA.match(sha) else None
        packed = shared / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                sha, _, name = line.partition(" ")
                if name.strip() == ref and _SHA.match(sha):
                    return sha
    except (OSError, ValueError):
        return None
    return None


# What the checkout was on when this process started, which is the code the
# process actually loaded. Read per request it would follow a `git commit` made
# afterwards, so a development server would report a commit it is not running
# (and the web build, which reads its own once, would seem to differ).
BOOT_COMMIT = _git_commit()


def _version():
    commit = (os.environ.get("RENDER_GIT_COMMIT") or "").strip()
    source = "render" if commit else None
    if not commit:
        commit = BOOT_COMMIT or ""
        source = "git" if commit else "unknown"
    return {"api_commit": commit or None, "api_started_at": _iso(STARTED_AT), "source": source}


def _version_health(version, now):
    started = timezone.localtime(STARTED_AT)
    up = clock(started)
    if started.date() != timezone.localdate(now):
        up = f"{started:%b} {started.day}, {up}"
    if version["api_commit"]:
        return _health_item("version", "ok", "Version",
                            f"API {version['api_commit'][:7]} · up since {up}")
    return _health_item("version", "warn", "Version",
                        f"API version unknown · up since {up}")


_SMS_NAMES = {"semaphore": "Semaphore", "philsms": "PhilSMS", "textbee": "textbee"}


def _sms_health(now):
    """Whether texts can go, from settings and from what the last day shows.

    No call to the gateway: the Test button does that, on request. A refusal
    deletes its reminder row so the next run retries (scheduling/reminders.py),
    which means it leaves nothing here to count. What does stay is a claim that
    was never stamped sent: a run that stopped partway, which is the
    other way a reminder goes missing.
    """
    provider = (settings.SMS_PROVIDER or "").strip().lower()
    if provider in ("", "console"):
        return _health_item("sms", "off", "Text messages",
                            "Not set up - texts go to the server log", "sms")
    if provider not in PROVIDERS:
        return _health_item(
            "sms", "bad", "Text messages",
            f"SMS_PROVIDER is '{provider}', which is not a gateway this system knows, "
            f"so texts go to the server log", "sms")
    name = _SMS_NAMES.get(provider, provider)
    if not settings.SMS_API_KEY:
        return _health_item("sms", "bad", "Text messages",
                            f"{name} is chosen but SMS_API_KEY is not set, so every text is refused",
                            "sms")
    since = now - timedelta(hours=24)
    stalled = 0
    for model in (SessionReminder, CustodianReminder):
        seen = model.objects.aggregate(
            stalled=Count("id", filter=Q(sent_at__isnull=True, claimed_at__gte=since,
                                         claimed_at__lt=now - UNCONFIRMED_AFTER)))
        stalled += seen["stalled"]
    # The spend record (accounts.SmsSend). Counts and credits only: never the
    # text, the number, its last digits or who it went to.
    spend = SmsSend.objects.filter(created_at__gte=since).aggregate(
        texts=Count("id", filter=Q(outcome__in=(SENT, UNKNOWN))),
        credits=Sum("credits", filter=Q(outcome__in=(SENT, UNKNOWN))),
        unknown=Count("id", filter=Q(outcome=UNKNOWN)),
        limited=Count("id", filter=Q(outcome=LIMITED)))
    texts, credits = spend["texts"], spend["credits"] or 0
    limit = settings.SMS_DAILY_CREDIT_LIMIT
    plural = "text" if texts == 1 else "texts"
    detail = (f"{name} · {texts} {plural}, about {credits} credits in the last "
              f"24 hours (limit {limit} a day)")
    if spend["unknown"]:
        detail += f"; {spend['unknown']} unconfirmed"
    if spend["limited"]:
        detail += f"; {spend['limited']} stopped by a limit"
    if stalled:
        detail += f"; {stalled} reminder{'s' if stalled != 1 else ''} not confirmed sent"
    status = "ok"
    if limit > 0 and credits >= limit:
        status = "bad"
        detail += " - the daily limit is reached, so no more texts go until it clears"
    elif stalled or (limit > 0 and credits * 100 >= limit * 80):
        status = "warn"
    return _health_item("sms", status, "Text messages", detail, "sms")


def _email_health():
    if settings.BREVO_API_KEY:
        return _health_item("email", "ok", "Email",
                            f"Brevo · sends from {settings.BREVO_SENDER_EMAIL}", "email")
    return _health_item("email", "off", "Email",
                        "Not set up - emails go to the server log", "email")


def _assistant_health():
    # Not AssistantSetting.load(): that creates the row, and a read should not.
    row = AssistantSetting.objects.filter(pk=1).first()
    enabled = row.enabled if row else True
    if not enabled:
        return _health_item("assistant", "off", "Assistant",
                            "Switched off in Settings", "assistant")
    if hosted_model_configured():
        detail = f"On · hosted model {settings.ASSISTANT_MODEL_NAME}"
    else:
        model = row.model_name if row else AssistantSetting._meta.get_field("model_name").default
        detail = f"On · local model {model}"
    return _health_item("assistant", "ok", "Assistant", detail, "assistant")


def _uploads_health(now):
    newest = [PsychologicalReport.objects.aggregate(at=Max("created_at"))["at"],
              CaseReferral.objects.aggregate(at=Max("created_at"))["at"],
              ConsentRecord.objects.filter(scan__isnull=False).exclude(scan="")
              .aggregate(at=Max("created_at"))["at"]]
    newest = max((at for at in newest if at), default=None)
    latest = f"newest file {_when(newest, now)}" if newest else "no files yet"

    config = settings.STORAGES.get("default", {})
    options = config.get("OPTIONS", {})
    if "s3" in str(config.get("BACKEND", "")).lower():
        bucket = options.get("bucket_name")
        if not bucket:
            return _health_item("uploads", "bad", "File storage",
                                "Object storage is chosen but no bucket is named, "
                                "so uploads will fail")
        kind = ("Cloudflare R2" if "r2.cloudflarestorage.com" in str(options.get("endpoint_url") or "")
                else "S3-compatible storage")
        return _health_item("uploads", "ok", "File storage", f"{kind} · bucket {bucket} · {latest}")
    if not settings.DEBUG:
        return _health_item("uploads", "warn", "File storage",
                            f"Local disk, which a host wipes on every deploy · {latest}")
    return _health_item("uploads", "ok", "File storage", f"Local disk · {latest}")


# --- people: who has access to what ----------------------------------------


def _people(now, holders):
    today = timezone.localdate(now)
    day_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))

    counts = dict(User.objects.filter(status=User.ACTIVE, role__isnull=False)
                  .values_list("role__role_name").annotate(n=Count("id")))
    roles = {name: counts.get(name, 0)
             for name in (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST)}

    # Distinct people, latest sign-in each. The sign-in line is the only
    # record of one: User.last_login is stamped by the Google door alone.
    today_rows = (ActivityLog.objects
                  .filter(action=ActivityLog.LOGIN, created_at__gte=day_start,
                          actor__status=User.ACTIVE)
                  .values("actor_id", "actor__first_name", "actor__middle_initial",
                          "actor__last_name", "actor__email", "actor__role__role_name")
                  .annotate(at=Max("created_at")).order_by("-at", "actor_id")[:PEOPLE_ROWS])
    signed_in = [{"id": r["actor_id"],
                  "name": _name_of(r["actor__first_name"], r["actor__middle_initial"],
                                   r["actor__last_name"], r["actor__email"]),
                  "role": r["actor__role__role_name"], "at": _iso(r["at"])}
                 for r in today_rows]

    quiet_since = now - timedelta(days=DORMANT_DAYS)
    logins = ActivityLog.objects.filter(actor=OuterRef("pk"), action=ActivityLog.LOGIN)
    dormant = (User.objects
               .filter(status=User.ACTIVE, created_at__lte=quiet_since)
               .filter(~Exists(logins.filter(created_at__gte=quiet_since)))
               .annotate(last_in=Subquery(logins.order_by("-created_at").values("created_at")[:1]))
               .select_related("role")
               .order_by(F("last_in").asc(nulls_first=True), "id")[:PEOPLE_ROWS])
    return {
        "roles": roles,
        "signed_in_today": signed_in,
        "dormant": [{"id": u.pk, "name": _person(u),
                     "role": u.role.role_name if u.role else None,
                     "last_sign_in": _iso(u.last_in)} for u in dormant],
        "temp_passwords": [{"id": r["user"].pk, "name": _person(r["user"]),
                            "role": r["user"].role.role_name if r["user"].role else None,
                            "since": _iso(r["since"])} for r in holders[:PEOPLE_ROWS]],
    }


# --- workload: where the caseload sits -------------------------------------


def _workload(now):
    today = timezone.localdate(now)

    # A deactivated psychologist stays in the list for as long as children are
    # still assigned to them, as a deactivated social worker does below: those
    # children are in care, and a total that left them out would be short.
    psychologists = (User.objects
                     .filter(role__role_name=Role.PSYCHOLOGIST)
                     .annotate(
                         active=Count("assigned_children",
                                      filter=Q(assigned_children__status=Child.ACTIVE),
                                      distinct=True),
                         pending=Count("assignment_requests",
                                       filter=Q(assignment_requests__status=AssignmentRequest.PENDING),
                                       distinct=True))
                     .filter(Q(status=User.ACTIVE) | Q(active__gt=0))
                     .order_by("last_name", "first_name", "id"))
    # Heaviest first, what is waiting on an answer counted with it as the bars
    # draw it, then by name (the sort is stable over the query's order). The
    # bars are for comparing loads, which a list by surname hides.
    psychologist_rows = sorted(psychologists, key=lambda u: (-(u.active + u.pending), -u.active))

    social_workers = (User.objects.filter(role__role_name=Role.STAFF)
                      .annotate(active=Count("social_work_records",
                                             filter=Q(social_work_records__status=Child.ACTIVE)))
                      .filter(Q(status=User.ACTIVE) | Q(active__gt=0))
                      .order_by("last_name", "first_name", "id"))
    social_worker_rows = sorted(social_workers, key=lambda u: -u.active)

    # A window that still offers a day: any weekly one, or a dated one that
    # has not passed. A psychologist with neither cannot be booked at all.
    offering = AvailabilityBlock.objects.filter(psychologist=OuterRef("pk"), active=True).filter(
        Q(date__isnull=True, weekday__isnull=False) | Q(date__gte=today))
    unbookable = (User.objects
                  .filter(role__role_name=Role.PSYCHOLOGIST, status=User.ACTIVE)
                  .filter(~Exists(offering)).order_by("last_name", "first_name", "id"))

    away = (Unavailability.objects
            .filter(psychologist__status=User.ACTIVE, starts_on__lte=today + timedelta(days=LEAVE_DAYS_AHEAD),
                    ends_on__gte=today)
            .select_related("psychologist").order_by("starts_on", "id"))

    return {
        "psychologists": [{"id": u.pk, "name": _person(u), "active": u.active,
                           "pending": u.pending, "inactive_account": u.status != User.ACTIVE}
                          for u in psychologist_rows],
        "social_workers": [{"id": u.pk, "name": _person(u), "active": u.active,
                            "inactive_account": u.status != User.ACTIVE}
                           for u in social_worker_rows],
        "unassigned": Child.objects.filter(status=Child.ACTIVE,
                                           assigned_psychologist__isnull=True).count(),
        "no_availability": [{"id": u.pk, "name": _person(u)} for u in unbookable],
        "on_leave": [{"id": r.psychologist_id, "name": _person(r.psychologist),
                      "start": r.starts_on.isoformat(), "end": r.ends_on.isoformat()}
                     for r in away[:PEOPLE_ROWS]],
    }


# --- security log -----------------------------------------------------------

# The tail the code that wrote an administrator's update puts after the
# person's name (accounts/views.py, accounts/serializers.py). It is searched
# from the END of the label and only to take the facts out of it (which role,
# asked for what): which KIND of line it is comes from ActivityLog.kind. Each
# fact is a word or a bracketed phrase that cannot run across another tail, so a
# name that itself contains one of these phrases cannot be read as the tail.
_ASKED = r"\(asked for: (?P<asked>[^()]+)\)"
_TAILS = {
    ActivityLog.KIND_ROLE: re.compile(r" — role changed from (?P<old>\w+) to (?P<new>\w+)$"),
    ActivityLog.KIND_APPROVAL: re.compile(r" — access approved as (?P<role>\w+) " + _ASKED + "$"),
    ActivityLog.KIND_DECLINE: re.compile(r" — access request declined " + _ASKED + "$"),
    ActivityLog.KIND_RESET: re.compile(r" — password reset$"),
    ActivityLog.KIND_REACTIVATE: re.compile(r" — account reactivated$"),
}
_REQUEST_TAIL = re.compile(r" — access request " + _ASKED + "$")

NOT_A_SECURITY_EVENT = "Verified own mobile number"

# The lines anybody can write in bulk: a stranger by signing up or mistyping a
# password, a signed-in account by exporting, by asking the assistant for a
# brief or by causing more emails than the limit allows (once an hour, but for
# as many hours as it keeps going). Each shows its newest few and says how many
# more there are, so a flood
# of one cannot push the administrator's own actions (and the other floods) off
# a log of thirty rows. The lockouts and requests have a tile of their own that
# lists them. The assistant's reads come from another table and are taken by
# `_assistant_reads`, under the same cap and with the same note.
BULK_CAP = 5
READ_SCAN = 200           # jobs looked through to find BULK_CAP distinct lines
_BULK = (
    ("export", _EXPORT, "export", "exports"),
    ("lockout", _ANY_LOCKOUT, "sign-in lockout", "sign-in lockouts"),
    ("request", _REQUEST, "access request", "access requests"),
    ("email_limit", _EMAIL_LIMIT, "email limit", "email limits"),
)
_READS = ("assistant_read", "assistant read", "assistant reads")


def _bulk_text(kind, row):
    """The sentence for a line picked out by `_BULK`."""
    label = row.entity_label or ""
    if kind == "export":
        return label
    if kind == "lockout":
        if label == ADDRESS_LOCK_LABEL:
            return label
        return f"{LOCKED_PREFIX}{_locked_name(label)}{LOCKED_SUFFIX}"
    if kind == "email_limit":
        # The account is the actor; the label's own words are not read. It names
        # no colleague and no case. "A day" is the longest a hold can last (the
        # daily limit); the hourly limits lift within the hour.
        who = row.actor_label or "An account"
        return f"{who} reached the email limit; further emails are held for up to a day"
    tail = _REQUEST_TAIL.search(label)
    if tail:
        return f"Access request from {label[:tail.start()]} (asked for: {tail['asked']})"
    return f"Access request from {label}"


def _security_text(row):
    """(kind, sentence) for a line that is none of the `_BULK` ones.

    A SECURITY line from here on was written by an account that was signed in,
    with a fixed label. A USER line is an administrator acting on an account;
    the person's name opens its label and the words that follow are only
    trusted when `kind` says they were written by the code that goes with it.
    """
    label = row.entity_label or ""
    if row.category == ActivityLog.SECURITY:
        if label == "Changed own password":
            return "password", label
        if label == "Linked Google account for sign-in":
            return "google", "Linked a Google account for sign-in"
    else:
        tail = _TAILS.get(row.kind)
        found = tail.search(label) if tail else None
        if found:
            who = label[:found.start()]
            if row.kind == ActivityLog.KIND_ROLE:
                return "role", f"Changed the role of {who} from {found['old']} to {found['new']}"
            if row.kind == ActivityLog.KIND_APPROVAL:
                return "approval", f"Approved {who} as {found['role']} (asked for: {found['asked']})"
            if row.kind == ActivityLog.KIND_DECLINE:
                return "decline", f"Declined the access request from {who} (asked for: {found['asked']})"
            if row.kind == ActivityLog.KIND_RESET:
                return "reset", f"Issued a temporary password to {who}"
            return "reactivate", f"Reactivated {who}"
        if row.action == ActivityLog.ARCHIVED:
            return "archive", f"Deactivated {label}"
        if row.action == ActivityLog.CREATED:
            return "created", f"Created the account for {label}"
        if row.action == ActivityLog.UPDATED:
            return "other", f"Edited the account of {label}"
    return "other", " ".join(p for p in (row.get_action_display(), row.entity_type, label) if p)


# What a read says it was, as "to do it" (someone had the assistant ...) and as
# "did it" (nobody asked: the survey check runs by itself). Keyed by job type
# and by the document's kind, never by anything a job holds. A job type
# assistant/views.py starts logging without a sentence here still shows, as "a
# record"; test_every_logged_job_type_has_its_words says it is missing.
_READ_WORDS = {
    "brief": ("write a brief", "wrote a brief"),
    "case_brief": ("write a case brief", "wrote a case brief"),
    "doc_intelligence": ("summarise a document", "summarised a document"),
    "self_report": ("check survey answers", "checked survey answers"),
}
_READ_ANYTHING = ("read a record", "read a record")
_DOCUMENT_WORDS = {
    "report": ("summarise a report", "summarised a report"),
    "casereferral": ("summarise a case referral", "summarised a case referral"),
}


def _assistant_reads(since, cap):
    """(the newest `cap` lines, how many lines there are in all): who had the
    assistant read a child's record, from the same job types the child's own
    access log lists (assistant/views.py).

    `values_list` names the columns it takes, so a job's `output_text` is not
    loaded, let alone sent. The survey check writes one job per answer, so a
    survey is collapsed to its newest job the way the child's log does it, and
    counted that way too: the total is the number of lines there would be, not
    the number of jobs.
    """
    from assistant.views import ACCESS_LOGGED_JOB_TYPES   # a heavy import; only this needs it

    jobs = AssistantJob.objects.filter(created_at__gte=since, child__isnull=False,
                                       job_type__in=ACCESS_LOGGED_JOB_TYPES)
    total = (jobs.exclude(job_type="self_report").count()
             + jobs.filter(job_type="self_report").values("input_ref").distinct().count())
    rows = (jobs.order_by("-created_at", "-id")
            .values_list("job_type", "input_ref", "child_id", "created_at", "ok",
                         "created_by__first_name", "created_by__middle_initial",
                         "created_by__last_name", "created_by__email")[:READ_SCAN])
    seen, entries = set(), []
    for job_type, ref, child_id, at, ok, first, middle, last, email in rows:
        if len(entries) == cap:
            break
        if job_type == "self_report":
            if (job_type, ref) in seen:
                continue
            seen.add((job_type, ref))
        who = _name_of(first, middle, last, email) if email else ""
        to_do, did = _READ_WORDS.get(job_type, _READ_ANYTHING)
        if job_type == "doc_intelligence":
            to_do, did = _DOCUMENT_WORDS.get(ref.partition(":")[0], (to_do, did))
        text = (f"{who} had the assistant {to_do} for {case_ref(child_id)}" if who
                else f"The assistant {did} for {case_ref(child_id)}")
        if not ok:
            text += " (it did not finish)"
        entries.append((at, {"at": _iso(at), "kind": "assistant_read",
                             "actor": who or "System", "text": text}))
    return entries, total


def _security_log(now):
    """(the log, what it left out): at most LOG_ROWS lines of the last LOG_DAYS
    days, newest first. Every kind that is cut short, and the administrator's
    own lines when there are more than the log holds, say how many lines they
    left out, so a log that looks quiet cannot be mistaken for a complete one."""
    since = now - timedelta(days=LOG_DAYS)
    week = (ActivityLog.objects
            .filter(category__in=[ActivityLog.SECURITY, ActivityLog.USER], created_at__gte=since)
            .exclude(action=ActivityLog.LOGIN)
            .exclude(category=ActivityLog.SECURITY, entity_label=NOT_A_SECURITY_EVENT))
    entries, totals = [], {}

    bulk = Q()
    for kind, picked, _one, _many in _BULK:
        bulk |= picked
        rows = week.filter(picked).order_by("-created_at", "-id")
        totals[kind] = rows.count()
        for row in rows[:BULK_CAP]:
            entries.append((row.created_at, {"at": _iso(row.created_at), "kind": kind,
                                             "actor": row.actor_label or "System",
                                             "text": _bulk_text(kind, row)}))
    reads, totals[_READS[0]] = _assistant_reads(since, BULK_CAP)
    entries += reads

    rest = week.exclude(bulk)
    rest_total = rest.count()
    for row in rest.order_by("-created_at", "-id")[:LOG_ROWS]:
        kind, text = _security_text(row)
        entries.append((row.created_at, {"at": _iso(row.created_at), "kind": kind,
                                         "actor": row.actor_label or "System", "text": text}))
    entries.sort(key=lambda e: e[0], reverse=True)
    log = [entry for _at, entry in entries[:LOG_ROWS]]

    # What the cuts left out. Each bulk kind is judged against what the final
    # cut kept of it; the rest (everything an administrator or a signed-in
    # account did) against the lines that were not a bulk kind.
    shown = Counter(e["kind"] for e in log)
    left_out = []
    for kind, one, many in [(k, o, m) for k, _q, o, m in _BULK] + [_READS]:
        more = totals[kind] - shown[kind]
        if more > 0:
            left_out.append({"kind": kind, "count": more,
                             "text": f"and {more} more {one if more == 1 else many} not shown"})
    kept = sum(n for k, n in shown.items() if k not in totals)
    older = rest_total - kept
    if older > 0:
        left_out.append({"kind": "older", "count": older,
                         "text": f"and {older} older {'event' if older == 1 else 'events'} not shown"})
    return log, left_out


# --- the view ---------------------------------------------------------------


class AdminOverviewView(APIView):
    """GET /api/admin/overview/ - the whole console in one answer.

    Administrators only. Everything is read at one `now`, so "today", "the last
    24 hours" and "up since" cannot disagree with each other within a response.
    """
    permission_classes = [IsAdministrator]

    def get(self, request):
        now = timezone.now()
        holders = _temp_password_holders(now)
        needs = {
            "access_requests": _access_requests(now),
            "lockouts": _lockouts(now),
            "temp_passwords": _temp_passwords(now, holders),
            "stuck_requests": _stuck_requests(now),
            "no_social_worker": _no_social_worker(now),
            "archived_holder": _archived_holder(now),
            "names_to_fix": _names_to_fix(now),
            "duplicates": _duplicates(now),
        }
        version = _version()
        log, log_more = _security_log(now)
        return Response({
            "generated_at": _iso(now),
            "needs": [needs[key] for key in NEED_KEYS],
            "health": [
                _server_health(),
                _version_health(version, now),
                _sms_health(now),
                _email_health(),
                _assistant_health(),
                _uploads_health(now),
            ],
            "version": version,
            "people": _people(now, holders),
            "workload": _workload(now),
            "security_log": log,
            "security_log_more": log_more,
        })
