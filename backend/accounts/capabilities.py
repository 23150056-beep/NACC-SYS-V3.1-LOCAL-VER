"""Every list of roles, named once, by the question it answers.

Before this module a role decision was written as a tuple or an if/else at each
call site, and an account whose role the code did not name fell into whichever
branch happened to be last - often the ISA's. Now a decision reads a list from
here, and whatever role is in no list is refused or sees nothing.

RULE: read a list through the module -
``from accounts import capabilities`` then ``capabilities.KEEPS_RECORDS`` -
never ``from accounts.capabilities import KEEPS_RECORDS``, and never copy a
list into a module-level or class-level constant at import time, so a test can
widen one list. Functions may be imported by name.

This module imports only `accounts.models`, so anything may import it without
closing a circle.
"""
from accounts.models import Role

# May use the system at all; the authentication door refuses any other role.
EVERY_ROLE = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)

# Scope: sees every record, no narrowing.
SEES_EVERY_RECORD = (Role.ADMINISTRATOR,)

# Writes a child's record; every place that said "Admin or Staff".
KEEPS_RECORDS = (Role.ADMINISTRATOR, Role.STAFF)

# Follows the social worker's rule where it differs from the ISA's (care gaps,
# next steps, the drafting table, cancel-only).
SOCIAL_WORK = (Role.STAFF,)

# Chooses holders, transfers records, closes by outcome, removes a duplicate.
OVERSEES_CASEWORK = (Role.ADMINISTRATOR,)

# May be Child.social_worker.
HOLDS_RECORDS = (Role.STAFF,)

# What a stranger may say they are when asking for an account, at either door
# (the typed form and Google).
CLAIMABLE_AT_SIGNUP = (Role.PSYCHOLOGIST, Role.STAFF)

# Who may sign in through the Google door once approved; not what may be
# claimed there, which is CLAIMABLE_AT_SIGNUP.
SIGNS_IN_WITH_GOOGLE = (Role.PSYCHOLOGIST, Role.STAFF)

# Writes clinical records, and may polish a remark.
WRITES_CLINICAL = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST)

# May combine availability windows.
MANAGES_AVAILABILITY = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST)

# Records completed or no-show on a session the account holds as its
# psychologist (and may cancel it); the RACC Officer is never in it (the plan's
# rule 6: the Officer is not a psychologist).
RECORDS_OWN_SESSION_OUTCOMES = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)

# Roles allowed to manage assessment instruments (questionnaires).
# Capstone RBAC matrix = Admin-only; Psychologist added per product decision 2026-06-27.
# TO REVERT to the capstone rule: remove Role.PSYCHOLOGIST from this tuple.
INSTRUMENT_MANAGERS = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST)


def _role_name(user):
    return getattr(getattr(user, "role", None), "role_name", None)


def works_record(user, child):
    """Does this user follow the social worker's rule for this child: their
    role is in SOCIAL_WORK and they are the child's social worker?"""
    return (_role_name(user) in SOCIAL_WORK
            and child.social_worker_id is not None
            and child.social_worker_id == getattr(user, "id", None))


def may_hold_records(user):
    """May this account be a child's social worker (role only; status checks
    stay with the caller)?"""
    return _role_name(user) in HOLDS_RECORDS


def may_close_case(request, child):
    """May this request end this child's case: an administrator, or the
    psychologist the child is assigned to."""
    role = _role_name(getattr(request, "user", None))
    return (role in OVERSEES_CASEWORK
            or (role == Role.PSYCHOLOGIST
                and child.assigned_psychologist_id == request.user.id))
