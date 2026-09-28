"""Assigning is asking (owner's request, 28 Sep 2026).

Picking a psychologist on a record no longer puts the child in their records.
It writes an AssignmentRequest, and `Child.assigned_psychologist` changes
only when the psychologist accepts. Every scoping rule reads that one field
(accounts/scoping.py), so leaving it untouched until then is the whole of
"not added to their records": a pending child is absent from their Records,
Monitoring, calendar and assistant without any of those learning a new rule.

Every change of state is a conditional UPDATE on status='pending', so a
request answered in one tab and withdrawn in another is settled once, and the
second caller is told what happened rather than overwriting it.

Design: docs/superpowers/specs/2026-09-28-assignment-acceptance-design.md
"""
from django.db import transaction
from django.utils import timezone

from accounts.display import display_name
from accounts.models import Role
from accounts.sms_notifications import notify_new_assignment
from activity.models import ActivityLog
from activity.services import log_activity
from children.models import AssignmentRequest, Child
from children.notifications import send_assignment_notification

PENDING = AssignmentRequest.PENDING

# Long enough for a real reason, short enough that it is not a case note.
REASON_MAX = 1000


class AssignmentError(Exception):
    """A refusal the API passes on as-is: `status` 400, 403 or 409."""

    def __init__(self, message, status=400, field="detail"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field


def is_active_psychologist(user):
    return (user is not None and user.is_active
            and getattr(getattr(user, "role", None), "role_name", None) == Role.PSYCHOLOGIST)


def pending_for(child):
    return child.assignment_requests.filter(status=PENDING).first()


def _log(actor, action, req, recipient):
    log_activity(actor, action, ActivityLog.RECORD,
                 entity_type="Assignment", entity_label=req.child.fullname,
                 entity_id=req.child_id, recipient=recipient)


def _record_holder(req):
    """Who hears the answer: the social worker whose record it is, or
    whoever asked where the record has none yet."""
    return req.child.social_worker or req.requested_by


def request_assignment(child, psychologist, *, by, carry_history=None):
    """Ask `psychologist` to take `child`, and tell them.

    Returns the open request, or None when there is nothing to ask: the
    psychologist already holds the child, which also settles any question
    left open about somebody else. Asking a second psychologist withdraws the
    first request - one question per child at a time.

    `carry_history` None means "not said": a new request carries the history
    (the model's default), and an open one keeps the choice it was made with.
    """
    if psychologist.pk == child.assigned_psychologist_id:
        withdraw_pending(child, by=by)
        return None
    current = pending_for(child)
    if current is not None and current.psychologist_id == psychologist.pk:
        if carry_history is not None and current.carry_history != carry_history:
            current.carry_history = carry_history
            current.save(update_fields=["carry_history"])
        return current
    if current is not None:
        withdraw(current, by=by)
    req = AssignmentRequest.objects.create(
        child=child, psychologist=psychologist, requested_by=by,
        previous_psychologist=child.assigned_psychologist,
        carry_history=True if carry_history is None else carry_history)
    _log(by, ActivityLog.REQUESTED, req, psychologist)
    # Same two messages an assignment used to send, now saying there is a
    # question to answer. Neither carries the child's name.
    send_assignment_notification(child, psychologist)
    notify_new_assignment(child, psychologist)
    return req


def _settle(req, status, by, **fields):
    """Move a pending request to `status`, once. False when somebody else
    got there first."""
    return bool(AssignmentRequest.objects
                .filter(pk=req.pk, status=PENDING)
                .update(status=status, decided_at=timezone.now(),
                        decided_by=by, **fields))


def _already(req):
    req.refresh_from_db()
    who = display_name(req.decided_by) if req.decided_by else ""
    verb = {AssignmentRequest.ACCEPTED: "accepted",
            AssignmentRequest.DECLINED: "declined",
            AssignmentRequest.WITHDRAWN: "withdrawn"}.get(req.status, req.status)
    return AssignmentError(
        f"This request was already {verb}{f' by {who}' if who else ''}.", status=409)


def accept(req, *, by):
    """The psychologist takes the child: from here on it is in their records."""
    with transaction.atomic():
        child = Child.objects.select_for_update().get(pk=req.child_id)
        if child.status == Child.INACTIVE:
            raise AssignmentError(
                "This case was closed after you were asked, so there is nothing "
                "to accept.", status=409)
        if not _settle(req, AssignmentRequest.ACCEPTED, by):
            raise _already(req)
        child.assigned_psychologist = req.psychologist
        child.assignee_sees_history = req.carry_history
        child.save(update_fields=["assigned_psychologist", "assignee_sees_history",
                                  "updated_at"])
    req.refresh_from_db()
    # One row. A social worker's feed already carries every Assignment event
    # on their own records (activity/views.py), so the recipient only matters
    # to a psychologist - and on a transfer that is the one who held the
    # child, who would otherwise lose them with nothing said. A second row
    # for them showed the social worker the same acceptance twice.
    moved_from = (req.previous_psychologist
                  if req.previous_psychologist_id not in (None, req.psychologist_id) else None)
    _log(by, ActivityLog.ACCEPTED, req, moved_from or _record_holder(req))
    return req


def decline(req, *, by, reason):
    reason = (reason or "").strip()
    if not reason:
        raise AssignmentError("Say why, so the social worker knows whom to ask next.",
                              field="reason")
    if len(reason) > REASON_MAX:
        raise AssignmentError(f"Keep the reason under {REASON_MAX} characters.",
                              field="reason")
    if not _settle(req, AssignmentRequest.DECLINED, by, reason=reason):
        raise _already(req)
    req.refresh_from_db()
    _log(by, ActivityLog.DECLINED, req, _record_holder(req))
    return req


def withdraw(req, *, by):
    if not _settle(req, AssignmentRequest.WITHDRAWN, by):
        raise _already(req)
    req.refresh_from_db()
    # To the psychologist, so the request does not vanish from their panel
    # with nothing to say why.
    _log(by, ActivityLog.WITHDRAWN, req, req.psychologist)
    return req


def withdraw_pending(child, *, by):
    """Withdraw whatever is open on this child, if anything. Quietly a no-op
    when the question was settled a moment ago by somebody else."""
    req = pending_for(child)
    if req is None:
        return None
    try:
        return withdraw(req, by=by)
    except AssignmentError:
        return None
