"""Whose name a schedule shows (the owner's decision, 24 Sep 2026).

On the calendar a social worker sees a child's name only for children they
referred themselves - the ones whose latest case referral they filed. Every
other appointment reads as the child's case reference and who referred them:
"C-0042 · Ref. E. Pascua". Administrators and psychologists see names as
before; a psychologist only ever sees their own appointments anyway.

Why the case reference rides along: with the referrer's name alone, one
social worker's fifteen referrals read as fifteen identical chips, and the
one somebody cancels is a guess. The reference is unique and names nobody.

It is applied where the data leaves the server - the appointments API, the
Dashboard's "Today" strip, the assistant's schedule answers - not in the
screen, because a name hidden by the screen is still in the response. Records
and the booking form keep names: staff enter children there, and cannot book
a child they cannot pick.
"""
from django.db.models import Prefetch

from accounts.display import display_name
from accounts.models import Role
from accounts.scoping import role_of_user


def referrals_prefetch(prefix="child__"):
    """Prefetch for a child's case referrals, newest first, with who filed them,
    so the rule costs no query per appointment."""
    from clinical.models import CaseReferral
    return Prefetch(f"{prefix}case_referrals",
                    queryset=CaseReferral.objects.select_related("uploaded_by")
                    .order_by("-created_at", "-id"))


def latest_referral(child):
    cached = getattr(child, "_prefetched_objects_cache", {}).get("case_referrals")
    if cached is not None:
        return max(cached, key=lambda r: (r.created_at, r.id), default=None)
    return (child.case_referrals.select_related("uploaded_by")
            .order_by("-created_at", "-id").first())


def case_ref(child_or_id):
    pk = getattr(child_or_id, "pk", child_or_id)
    return f"C-{int(pk):04d}"


def shows_name(user, child, referral=None):
    """True when `user` may see this child's name on a schedule. Anyone without
    a role - which should never reach here - sees none."""
    role = role_of_user(user)
    if role in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST):
        return True
    if role != Role.STAFF:
        return False
    referral = referral if referral is not None else latest_referral(child)
    return referral is not None and referral.uploaded_by_id == getattr(user, "id", None)


def who(user, child):
    """The fields a schedule row carries about its child, for this viewer."""
    referral = latest_referral(child)
    visible = shows_name(user, child, referral)
    by = referral.uploaded_by if referral is not None else None
    return {
        "child_name": child.fullname if visible else None,
        "case_ref": case_ref(child),
        "referred_by_name": (display_name(by) or None) if by is not None else None,
        # Separate from the name: a referral whose uploader's account row is
        # gone (SET_NULL) is still a referral, and must not read as "none".
        "has_referral": referral is not None,
        "name_hidden": not visible,
    }


def label(user, child):
    """One line of text for places that print a single label (the assistant)."""
    w = who(user, child)
    if w["child_name"]:
        return w["child_name"]
    ref = w["referred_by_name"]
    if ref:
        return f"{w['case_ref']} (referred by {ref})"
    if w["has_referral"]:
        return f"{w['case_ref']} (referrer unknown)"
    return f"{w['case_ref']} (no referral on file)"
