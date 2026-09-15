from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.models import Role
# The one definition, shared with every viewset that scopes by role. This file
# held the fifth hand-written copy.
from accounts.scoping import role_of as _role_name


class IsAdministrator(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and _role_name(request) == Role.ADMINISTRATOR)


class IsAdminOrStaff(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and _role_name(request) in (Role.ADMINISTRATOR, Role.STAFF))


class RecordsAccess(BasePermission):
    """Read access for Admin/Staff/Psychologist; write access for Admin/Staff only.

    Psychologists can VIEW child/guardian records (per the RBAC matrix) but cannot
    create, edit, archive, or delete them. Per-psychologist "assigned only"
    filtering is NOT done here - it is queryset scoping, and it lives in
    accounts.scoping.scope_to_visible, which every child-related viewset applies.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        role = _role_name(request)
        if request.method in SAFE_METHODS:
            return role in (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST)
        return role in (Role.ADMINISTRATOR, Role.STAFF)


# Roles allowed to manage assessment instruments (questionnaires).
# Capstone RBAC matrix = Admin-only; Psychologist added per product decision 2026-06-27.
# TO REVERT to the capstone rule: remove Role.PSYCHOLOGIST from this tuple.
INSTRUMENT_MANAGER_ROLES = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST)


class CanManageInstruments(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and _role_name(request) in INSTRUMENT_MANAGER_ROLES)


# Roles allowed to VIEW assessment results (read-only). Staff is included for
# case coordination. V2: in-app assessment administration was removed entirely.
RESULT_VIEWER_ROLES = (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)


class CanViewResults(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and _role_name(request) in RESULT_VIEWER_ROLES)


class ChildRecordAccess(RecordsAccess):
    """RecordsAccess plus multidisciplinary collaboration: the child's
    assigned psychologist may edit (PUT/PATCH) the record. Create/archive
    stay Admin/Staff-only; queryset scoping already hides other children."""

    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        return bool(request.user and request.user.is_authenticated
                    and _role_name(request) == Role.PSYCHOLOGIST
                    and request.method in ("PUT", "PATCH"))

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        role = _role_name(request)
        if role in (Role.ADMINISTRATOR, Role.STAFF):
            return True
        return (role == Role.PSYCHOLOGIST
                and request.method in ("PUT", "PATCH")
                and obj.assigned_psychologist_id == request.user.id)


def is_admin_or_assignee(request, child):
    """An administrator, or the psychologist this child is assigned to.

    The rule behind `terminate` and `advance-status`, which was written out
    by hand at both. Two copies is a thin case for a helper anywhere else;
    for the predicate deciding who may END A CASE it is one too many, and it
    is the same reasoning that put scope_to_visible in accounts/scoping.py.

    Deliberately NOT shared with an appointment's rule in
    scheduling.views._set_status: that one turns on the appointment's own
    psychologist rather than the child's assignee, and it also lets staff
    cancel. Two rules that look alike are still two rules.
    """
    role = _role_name(request)
    return (role == Role.ADMINISTRATOR
            or (role == Role.PSYCHOLOGIST
                and child.assigned_psychologist_id == request.user.id))


class ProgressRecordAccess(BasePermission):
    """Progress log & goals. Read: admin/staff/psychologist. Write: admin or the
    child's assigned psychologist (Staff read-only). Object-level restricts a
    psychologist to their assigned children's records."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        role = _role_name(request)
        if request.method in SAFE_METHODS:
            return role in (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST)
        return role in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        role = _role_name(request)
        if role == Role.ADMINISTRATOR:
            return True
        return obj.child.assigned_psychologist_id == request.user.id
