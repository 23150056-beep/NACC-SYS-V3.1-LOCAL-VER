"""Names with letters outside ASCII - Ñ, ñ, é - and the one way they go wrong.

Our own screens send every name as UTF-8 JSON, and that path keeps an Ñ
intact end to end (accounts/tests/test_accented_names.py pins each door). The
way an Ñ is lost is a client that is not ours: a script, curl, PowerShell 5.1,
or any tool posting a FORM-encoded body in Latin-1, where the letter is the
single byte 0xD1. Django decodes form data leniently and swaps a byte it cannot
read for U+FFFD, so "PEÑAMORA" arrived as "PE" + U+FFFD + "AMORA" and was saved
without a word. That reproduces exactly what the owner's account showed in the
left rail on 9 Oct 2026, "JOHN REYNOLD PE" + U+FFFD + "…"; whether that is how
it got there cannot be told from the code, only from the stored value. A JSON
body in Latin-1 is refused by the parser; a form body was not.

The replacement character is never part of a real name, so a name carrying one
is refused where it is written, and the sender is told to type it again.
"""
from rest_framework import serializers

REPLACEMENT = "\ufffd"

UNREADABLE = ("A character in this name could not be read. Type the name again, "
              "with its accented letters (Ñ, é) as you would write them.")


def refuse_unreadable(value, current=None):
    """Return the name, or raise if it carries U+FFFD.

    `current` is what the record already holds. A value that is unchanged
    passes, so a name already damaged does not block every other edit of the
    record: the edit form resends it, and the fix is to retype it, not to be
    locked out of changing a role."""
    if value and REPLACEMENT in value and value != current:
        raise serializers.ValidationError(UNREADABLE)
    return value
