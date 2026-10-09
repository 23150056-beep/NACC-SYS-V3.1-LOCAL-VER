"""One child, twice (found 9 Oct 2026).

A social worker pressed Save Record twice. Two records came out - same child,
same birth date - each with its own request to the psychologist, who then had
two "Waiting for your answer" rows for one child. There was no way to delete
either: children are never deleted through the API.

Three guards live here, one for each way it can happen:

1. THE SAME SUBMISSION ARRIVING TWICE. Add Record makes a token when it opens
   and sends it with every attempt to save. A token that already has a record
   is answered "already saved" rather than adding the child again. This is
   the server's half of the button guard in Children.jsx, and it covers what
   a button cannot: a response that never came back, then a retry. Two
   requests that arrive together both find nothing, and the unique column lets
   one in; the other is turned into the same answer.

2. THE SAME CHILD, TYPED AGAIN. A new record is refused when one already
   exists - any social worker's, active or closed - with the same first name,
   last name and birth date. A name alone is not enough: two children can
   share a common one. The sentence follows what the duplicate check already
   discloses to this requester (children/views.py check_duplicate).

3. A DUPLICATE THAT GOT IN ANYWAY. The ISA can remove one, but only a record
   holding nothing but what Add Record makes; see `remove`.
"""
import re
import unicodedata

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from accounts.display import display_name
from accounts.scoping import scope_to_visible
from children.models import Child
from scheduling.visibility import case_ref

# --- 1. The same submission ----------------------------------------------

# crypto.randomUUID() is 36 characters; anything made some other way still has
# to look like a token before it is looked up.
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ALREADY_SAVED = "This record was already saved."


def clean_token(raw):
    """The token as stored, or None for blank. Raises ValueError for text that
    is not shaped like one."""
    text = str(raw).strip() if raw else ""
    if not text:
        return None
    if not TOKEN_SHAPE.match(text):
        raise ValueError("That is not a valid submission token.")
    return text


def token_in(data):
    """The token a request carries, or None. A malformed one is left for the
    serializer to refuse; it is never looked up."""
    try:
        return clean_token(data.get("intake_token")) if hasattr(data, "get") else None
    except ValueError:
        return None


def already_saved(request, token):
    """The 409 for a token that already has a record, or None when it has not.

    The id comes back only when the requester may see that record
    (accounts/scoping.py): the token is something the browser invented, but
    the answer must not hand an id to somebody the record is not for."""
    existing = Child.objects.filter(intake_token=token).values_list("pk", flat=True).first()
    if existing is None:
        return None
    body = {"detail": ALREADY_SAVED}
    if scope_to_visible(Child.objects.filter(pk=existing), request, path=None).exists():
        body["id"] = existing
    return Response(body, status=status.HTTP_409_CONFLICT)


# --- 2. The same child ----------------------------------------------------


def _key(text):
    """A name as compared: trimmed, one space between words, case folded.
    Done here rather than by the database because SQLite folds only ASCII,
    which would treat PEÑA and Peña as two children."""
    return " ".join(unicodedata.normalize("NFC", str(text or "")).casefold().split())


def same_child(first, last, birth, exclude=None):
    """Every record for the child with this first name, last name AND birth
    date. Any of the three blank matches nothing: a last name alone is half a
    barangay."""
    if not (_key(first) and _key(last) and birth):
        return []
    wanted = (_key(first), _key(last))
    qs = Child.objects.filter(birth_date=birth).select_related("social_worker")
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return [c for c in qs.order_by("-updated_at")
            if (_key(c.first_name), _key(c.last_name)) == wanted]


def refusal_for_second_record(request, first, last, birth):
    """The sentence that refuses a new record for a child who already has one,
    or None. It says as much as check_duplicate does and no more:

    - the requester's own active record: its case reference;
    - another social worker's active record: who holds it, no id;
    - a closed record, anybody's: that it exists and can be reopened.
    """
    found = same_child(first, last, birth)
    if not found:
        return None
    mine = set(scope_to_visible(Child.objects.filter(pk__in=[c.pk for c in found]),
                                request, path=None).values_list("pk", flat=True))
    active = [c for c in found if c.status == Child.ACTIVE]
    if not active:
        return ("This child has a closed record. Reopen it from the warning above "
                "instead of adding a new one.")
    own = next((c for c in active if c.pk in mine), None)
    if own is not None:
        return (f"This child already has a record ({case_ref(own.pk)}). "
                "Open it instead of adding a second one.")
    holder = display_name(active[0].social_worker)
    if holder:
        return (f"A record for this child is already held by {holder}. "
                "Ask the ISA (Administrator) to transfer it to you.")
    return ("A record for this child already exists and is not with a social "
            "worker yet. Ask the ISA (Administrator) to assign it to you.")


def save_new(serializer, **extra):
    """serializer.save() in a transaction of its own, so a refusal by the
    database (the token already used) leaves the caller's transaction usable
    and the caller free to look the record up and answer."""
    with transaction.atomic():
        return serializer.save(**extra)
