"""One child, twice (found 9 Oct 2026).

A social worker double-clicked Save Record. Two records came out - same child,
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

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

from accounts.scoping import scope_to_visible
from children.models import Child

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


def save_new(serializer, **extra):
    """serializer.save() in a transaction of its own, so a refusal by the
    database (the token already used) leaves the caller's transaction usable
    and the caller free to look the record up and answer."""
    with transaction.atomic():
        return serializer.save(**extra)
