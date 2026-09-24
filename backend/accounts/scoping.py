"""Who may see which children — in one place.

Before this module the answer was written out by hand in eleven places across
five apps, and the helper that reads a user's role was defined five times,
byte-identical. That is not a tidiness problem. This is the predicate standing
between one psychologist and another psychologist's case notes, and eleven
hand-maintained copies is eleven chances for the twelfth call site to be
written slightly differently, or to forget.

The rule is deliberately narrow:

* Administrators see every child.
* A social worker (Staff) sees only the records they hold - `social_worker`,
  set to whoever added the record (owner's decision, 24 Sep 2026: each SW has
  their own records, not one shared list). Until then staff saw every child.
* A Psychologist sees only the children assigned to them.

`children` is imported inside the functions rather than at module scope: the
children app imports `accounts.permissions`, and a top-level import here would
close that circle.
"""
from accounts.models import Role


def role_of_user(user):
    """A user's role name, or None.

    Tolerates an anonymous user and a user with no role — both of which occur:
    an account awaiting approval has `role = None` by design.

    The request-shaped `role_of` below is what almost every caller wants; this
    exists for the background brief generator, which is handed a user rather
    than a request.
    """
    return getattr(getattr(user, "role", None), "role_name", None)


def role_of(request):
    """The requesting user's role name, or None."""
    return role_of_user(getattr(request, "user", None))


def visible_children(request):
    """The Child queryset this request may see.

    Scope always comes from `request.user`. No endpoint accepts an
    "assigned to me" parameter, so no caller can widen its own view by
    asking, and an argument the model invents is discarded before it
    reaches a queryset.

    Delegates rather than repeating the filter: this used to write the
    psychologist clause out again, which is how it came to differ from the
    copy in the assistant that was actually being called.
    """
    from children.models import Child

    return scope_to_visible(Child.objects.all(), request, path=None)


def scope_to_visible(qs, request, path="child"):
    """Narrow any child-related queryset to what this request may see.

    `path` is the lookup from the queryset's model to Child — "child" for a
    remark or a consent, and None for a queryset of children themselves.

    Returns the queryset untouched for Administrators, which is why this is
    safe to apply unconditionally at every call site: the caller no longer has
    to remember to write the role check as well. Anyone else - a role this
    does not know, or none - sees nothing.
    """
    role = role_of(request)
    if role == Role.ADMINISTRATOR:
        return qs
    if role == Role.PSYCHOLOGIST:
        owner = "assigned_psychologist"
    elif role == Role.STAFF:
        owner = "social_worker"
    else:
        return qs.none()
    field = owner if path is None else f"{path}__{owner}"
    return qs.filter(**{field: request.user})
