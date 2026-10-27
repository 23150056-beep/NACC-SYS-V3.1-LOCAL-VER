"""Moving a social worker's records to another social worker, in one go
(owner, 15 Oct 2026).

Each social worker keeps their own records (accounts/scoping.py), and archiving
an account moves nothing, so a colleague who leaves or goes on long leave takes
their caseload out of reach until the ISA moves it. Until now that was one
record at a time, through the record form's Social Worker field. This is the
same move for many at once, and the ISA's alone (`POST /api/children/transfer/`).

The rules, all of them checked before anything is written:

* Who may HOLD a record is the record form's rule (`validate_social_worker`): a
  Staff account. The one the records leave may be any status - an archived
  account is the usual reason to do this - but the one they go to must be
  active, or the records would be handed to somebody who cannot open them.
* Every id must be an ACTIVE record held by the one they leave. If any is not
  - closed, gone, already moved, or someone else's - the whole request is
  refused and NOTHING moves, with the case numbers that are the problem. A
  half-applied move is the worst answer: the screen's list would be right
  about none of it.
* The rows are locked (in id order, so two moves that share records take the
  locks the same way round), moved with one conditional UPDATE, and the count
  is checked against what was asked for, so a change that lands in between
  rolls the whole move back instead of leaving part of it. A database that
  gives the transaction up because another one got there first - SQLite's
  "database is locked", PostgreSQL's deadlock - is the same answer as a
  record that changed: 409, nothing moved.

Only `social_worker` changes. The assigned psychologist, any open request to
one, the case study and the appointments stay as they are; what a social worker
sees of all of them follows the field. `updated_at` is bumped by hand, because
a bulk UPDATE skips `auto_now`, and the record form's "updated by someone else"
check reads it - a form opened before the move must not save over it.
"""
from collections import Counter

from django.contrib.auth import get_user_model
from django.db import OperationalError, transaction
from django.utils import timezone

from accounts import email_notifications
from accounts.display import display_name
from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child
from scheduling.visibility import case_ref

User = get_user_model()

MAX_RECORDS = 1000
# The largest key either database stores for a BigAutoField. A bigger number in
# a lookup makes SQLite raise OverflowError, which would be a 500 for whoever
# sent it.
MAX_ID = 2**63 - 1
# What the audit columns hold. SQLite does not enforce a length, so a label
# that is too long is a 500 only on PostgreSQL, where the move would roll back
# for the sake of a log line.
LABEL_MAX = ActivityLog._meta.get_field("entity_label").max_length
ACTOR_MAX = ActivityLog._meta.get_field("actor_label").max_length
# How many case numbers a sentence names before it says "and 12 more".
NAMED_IN_A_SENTENCE = 10

NOT_A_SOCIAL_WORKER = "Choose a social worker (SW) account."
# Said whenever the list was right when the screen drew it and is not now. The
# screen has usually read the list again by the time this is shown, so it does
# not tell anybody to.
CHANGED_UNDERFOOT = ("Some of these records changed while they were being moved. "
                     "Nothing was moved - check the list and try again.")


class Refused(Exception):
    """The request cannot be carried out. `body` is the response body and
    `status` its HTTP status: 400 for a request that is malformed or names an
    account that cannot take part, 409 for one that was true when the screen
    drew its list and no longer is."""

    def __init__(self, body, status=400):
        super().__init__(str(body))
        self.body = body
        self.status = status


def _whole_number(value):
    # bool is an int in Python; `true` is not an id.
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ID


def _named(refs):
    shown = ", ".join(refs[:NAMED_IN_A_SENTENCE])
    more = len(refs) - NAMED_IN_A_SENTENCE
    return f"{shown} and {more} more" if more > 0 else shown


def parse(data):
    """(from_id, to_id, ids) out of a request body, or Refused. Shapes only;
    nothing here reads the database."""
    if not hasattr(data, "get") or isinstance(data, (str, bytes)):
        raise Refused({"detail": "Send the social worker the records are moving from, the one "
                                 "they are moving to, and the records."})
    from_id, to_id = data.get("from_social_worker"), data.get("to_social_worker")
    if not _whole_number(from_id):
        raise Refused({"from_social_worker": "Choose the social worker the records are moving from."})
    if not _whole_number(to_id):
        raise Refused({"to_social_worker": "Choose the social worker the records are moving to."})
    ids = data.get("children")
    if not isinstance(ids, list) or not ids:
        raise Refused({"children": "Choose the records to move."})
    if len(ids) > MAX_RECORDS:
        raise Refused({"children": f"Move at most {MAX_RECORDS:,} records at a time."})
    if not all(_whole_number(i) for i in ids):
        raise Refused({"children": "Choose the records by their id."})
    if len(set(ids)) != len(ids):
        repeated = sorted(i for i, n in Counter(ids).items() if n > 1)
        raise Refused({"children": f"{_named([case_ref(i) for i in repeated])} "
                                   f"{'is' if len(repeated) == 1 else 'are'} listed more than once."})
    return from_id, to_id, ids


def _social_worker(pk, field):
    user = User.objects.select_related("role").filter(pk=pk).first()
    if user is None or getattr(user.role, "role_name", None) != Role.STAFF:
        raise Refused({field: NOT_A_SOCIAL_WORKER})
    return user


def _movable(row, source):
    return (row is not None and row["status"] == Child.ACTIVE
            and row["social_worker_id"] == source.pk)


def _why_not(row, target, holders):
    if row is None:
        return "no longer exists"
    if row["status"] != Child.ACTIVE:
        return "closed"
    held = row["social_worker_id"]
    if held == target.pk:
        return f"already with {display_name(target)}"
    if held is None:
        return "has no social worker"
    return f"held by {display_name(holders.get(held))}"


def _stale(ids, rows, source, target):
    """The 409 for ids that are not the source's active records, or None.
    Names every one, with the reason, so the ISA - who sees every record and
    so may be told whose - knows which to take off the list."""
    bad = [pk for pk in sorted(ids) if not _movable(rows.get(pk), source)]
    if not bad:
        return None
    holders = {u.pk: u for u in User.objects.filter(pk__in={
        rows[pk]["social_worker_id"] for pk in bad if pk in rows} - {None})}
    problems = [{"id": pk, "ref": case_ref(pk), "why": _why_not(rows.get(pk), target, holders)}
                for pk in bad]
    shown = "; ".join(f"{p['ref']} ({p['why']})" for p in problems[:NAMED_IN_A_SENTENCE])
    if len(problems) > NAMED_IN_A_SENTENCE:
        shown += f"; and {len(problems) - NAMED_IN_A_SENTENCE} more"
    return Refused({
        "detail": f"These are not {display_name(source)}'s active records any more: {shown}. "
                  "Nothing was moved - check the list and try again.",
        "problems": problems}, status=409)


def _line(ref, name, source, target):
    """"Moved C-0042 · Ana Cruz from R. Santos to E. Pascua", cut to fit the
    column by shortening the child's name, so the people stay in it."""
    head, tail = f"Moved {ref} · ", f" from {display_name(source)} to {display_name(target)}"
    room = LABEL_MAX - len(head) - len(tail)
    if room < 1:
        return (head + name + tail)[:LABEL_MAX]
    return head + name[:room] + tail


def _lost_a_race(exc):
    """True when the database gave this transaction up because a concurrent one
    got there first, rather than because it is broken."""
    cause = exc.__cause__
    state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    # 40001 serialization failure, 40P01 deadlock detected.
    return state in ("40001", "40P01") or "locked" in str(exc).lower()


def _lines(by, rows, ids, source, target):
    """The trail for a move, written in the same transaction as the move.

    One line per record, the audit trail of whose it was and when it changed
    hands. They are kept out of the feeds (`KIND_MOVE_DETAIL`,
    activity/views.py): the feeds show the newest 50 lines, and a leaver's
    whole caseload is the commonest move there is. What the feeds carry is ONE
    sentence per move, addressed to the worker receiving the records and, if
    that account can still sign in, to the one they left - so the move is
    heard of before Records is opened, and fills nobody's bell."""
    actor = display_name(by, "System")[:ACTOR_MAX]
    lines = [ActivityLog(
        actor=by, actor_label=actor, action=ActivityLog.TRANSFERRED,
        category=ActivityLog.RECORD, entity_type="Child", entity_id=pk,
        entity_label=_line(case_ref(pk), rows[pk]["fullname"], source, target),
        kind=ActivityLog.KIND_MOVE_DETAIL)
        for pk in sorted(ids)]
    count = f"{len(ids)} record{'' if len(ids) == 1 else 's'}"
    sentence = f"Moved {count} from {display_name(source)} to {display_name(target)}"[:LABEL_MAX]
    told = [(target, "")]
    if source.status == User.ACTIVE:
        # The administrator's feed already carries the first of these; the
        # second is the same sentence again to them, so it is marked and left out.
        told.append((source, ActivityLog.KIND_MOVE_COPY))
    lines += [ActivityLog(
        actor=by, recipient=who, actor_label=actor, action=ActivityLog.TRANSFERRED,
        category=ActivityLog.RECORD, entity_type="Child", entity_label=sentence, kind=kind)
        for who, kind in told]
    return lines


def move(by, from_id, to_id, ids):
    """Move the records `ids` from one social worker to another. Returns the
    response body; raises Refused, having changed nothing, when it cannot.

    Written to the audit trail inside the same transaction (`_lines`); a move
    whose lines could not be written is not made."""
    source = _social_worker(from_id, "from_social_worker")
    target = _social_worker(to_id, "to_social_worker")
    if target.status != User.ACTIVE:
        raise Refused({"to_social_worker": f"{display_name(target)}'s account is not active, "
                                           "so records cannot be moved to it."})
    if source.pk == target.pk:
        raise Refused({"to_social_worker": "Choose a different social worker to move them to."})

    try:
        with transaction.atomic():
            # Locked, so a record closed or moved while this checks is not moved
            # unseen, and in id order, so two moves that overlap queue up behind
            # each other instead of each holding what the other wants. No
            # select_related: PostgreSQL will not lock the nullable side of a
            # join.
            rows = {r["id"]: r for r in Child.objects.select_for_update().filter(pk__in=ids)
                    .order_by("pk").values("id", "fullname", "status", "social_worker_id")}
            stale = _stale(ids, rows, source, target)
            if stale:
                raise stale
            moved = Child.objects.filter(
                pk__in=ids, status=Child.ACTIVE, social_worker_id=source.pk,
            ).update(social_worker=target, updated_at=timezone.now())
            if moved != len(ids):
                # Rolls back the update above with it.
                raise Refused({"detail": CHANGED_UNDERFOOT}, status=409)
            ActivityLog.objects.bulk_create(_lines(by, rows, ids, source, target))
    except OperationalError as exc:
        # Another move got to the rows first and this database does not wait
        # for it (SQLite starts the transaction with a read and cannot then
        # upgrade it to a write; PostgreSQL picks a deadlock's loser). Nothing
        # was written. Anything else is the database being down, and is not
        # dressed up as a stale list.
        if not _lost_a_race(exc):
            raise
        raise Refused({"detail": CHANGED_UNDERFOOT}, status=409) from exc

    # Only a move that was made gets here. The email waits for the commit as
    # well (accounts/email_notifications.py), so a request wrapped in a larger
    # transaction that fails later mails nothing.
    email_notifications.records_transferred(target, source, len(ids), by=by)
    return {"moved": len(ids),
            "to": {"id": target.pk, "name": display_name(target)},
            "children": [{"id": pk, "ref": case_ref(pk)} for pk in sorted(ids)]}
