"""Whose name a schedule shows (the owner's decisions, 24 Sep 2026).

On the calendar a social worker sees a child's name only for their own
records - the children they hold as `social_worker` (accounts/scoping.py).
Every other appointment reads as the child's case reference and the social
worker whose record it is: "C-0042 · Ref. E. Pascua". Administrators and
psychologists see names as before; a psychologist only ever sees their own
appointments anyway.

This first followed whoever filed the child's latest case referral. Once each
social worker held their own records it followed the record instead, and the
referral backfill (children 0025) made the two agree on the day it changed.

Why the case reference rides along: with the social worker's name alone, one
worker's fifteen children read as fifteen identical chips, and the one somebody
cancels is a guess. The reference is unique and names nobody.

It is applied where the data leaves the server - the appointments API, the
assistant's schedule answers, the booking refusal - not in the screen, because
a name hidden by the screen is still in the response.
"""
from accounts.display import display_name
from accounts.models import Role
from accounts.scoping import role_of_user


def case_ref(child_or_id):
    pk = getattr(child_or_id, "pk", child_or_id)
    return f"C-{int(pk):04d}"


def shows_name(user, child):
    """True when `user` may see this child's name on a schedule. Anyone without
    a role - which should never reach here - sees none."""
    role = role_of_user(user)
    if role in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST):
        return True
    if role != Role.STAFF:
        return False
    return child.social_worker_id is not None and child.social_worker_id == getattr(user, "id", None)


def who(user, child):
    """The fields a schedule row carries about its child, for this viewer.
    Callers select_related("child__social_worker") so this costs no query."""
    visible = shows_name(user, child)
    return {
        "child_name": child.fullname if visible else None,
        "case_ref": case_ref(child),
        "referred_by_name": display_name(child.social_worker) or None,
        "name_hidden": not visible,
    }


def label(user, child):
    """One line of text for places that print a single label (the assistant)."""
    w = who(user, child)
    if w["child_name"]:
        return w["child_name"]
    ref = w["referred_by_name"]
    return f"{w['case_ref']} (referred by {ref})" if ref else f"{w['case_ref']} (no social worker yet)"
