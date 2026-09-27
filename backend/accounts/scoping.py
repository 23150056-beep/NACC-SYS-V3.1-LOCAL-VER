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


def hide_earlier_history(qs, request, author_field, path="child"):
    """Narrow a queryset of clinical records to the carry-history control.

    `Child.assignee_sees_history = False` spares a newly assigned psychologist
    a colleague's prior opinions: of that child's records they see only what
    they wrote themselves. Everyone else, and every child whose history is
    carried, is untouched - so, like scope_to_visible, this is safe to apply
    unconditionally.

    `author_field` is who wrote the row ("author", "entered_by", ...). A row
    whose author was deleted has none, and is hidden with the rest: nobody can
    say it was theirs.

    It lives here because it is the same kind of rule as scope_to_visible and
    had the same history: written out once, on the child's page, while the
    record endpoints and Monitoring served the rows that page had just hidden.
    Apply it after scope_to_visible, never instead of it.
    """
    from django.db.models import Q

    if role_of(request) != Role.PSYCHOLOGIST:
        return qs
    return qs.filter(Q(**{f"{path}__assignee_sees_history": True})
                     | Q(**{author_field: request.user}))


def visible_pre_assessments(request):
    """A prefetch of each child's pre-assessments under the carry-history
    control, instruments included.

    A child's pre-assessment status and "instruments used" are worked out from
    `child.pre_assessments.all()`, which reads whatever was prefetched. Hiding
    the pre-assessment rows was not enough on its own: prefetched whole, the
    same response said "Answered" and listed the previous psychologist's test
    titles beside an empty pre-assessment list, and Monitoring counted and
    dated them. Every screen that shows those fields prefetches through this.

    Business rules that load a child without it still see every
    pre-assessment - hiding a colleague's history from a reader must not
    change what the case is.
    """
    from django.db.models import Prefetch
    from children.models import Child

    # By relation rather than by import: children never imports clinical.
    pre_assessment = Child._meta.get_field("pre_assessments").related_model
    return Prefetch("pre_assessments", queryset=hide_earlier_history(
        pre_assessment.objects.prefetch_related("instruments"), request, "psychologist"))
