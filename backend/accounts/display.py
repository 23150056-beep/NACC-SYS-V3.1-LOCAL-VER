"""One name for a person, wherever that person's name is shown.

This was written out by hand in eighteen places, and not identically. Most fell
back to `username`; three fell back to `get_username()`; two to the literal
"This psychologist"; one to an em dash. So the same psychologist could read as
three different things on three different screens, which is a small bug that is
invisible until somebody has no `fullname`.

`get_username()` is the one that mattered. `USERNAME_FIELD` on this model is
"email", so those three put a member of staff's EMAIL ADDRESS into an API
response - the slot grid, the next-slots strip, the chatbot's availability
answer and the per-child presence list - whenever a `fullname` happened to be
blank. Nobody chose that fallback. It is just what the Django default means on
a model that signs in by email, and it sits badly beside the care this system
otherwise takes not to make agency staff enumerable.

The order below is deliberate and it never reaches the email: the name the
person entered, then the username they were issued, then whatever the caller
wants shown when there is neither.

Not every name in the codebase belongs here. `scheduling/booking.py` says
"This psychologist" in a sentence addressed to a user and deliberately skips
the username step - a refusal reading "p2 is on leave that day" is worse than
the generic one. And `Child.fullname` is not a person's account name at all.
Both are left as they are on purpose.
"""
import unicodedata


def display_name(person, fallback=""):
    """The name to show for a user. Never their email address.

    Tolerates None, which genuinely occurs: an unassigned child has no
    psychologist, and a system-generated activity row has no actor.
    """
    return (getattr(person, "fullname", "")
            or getattr(person, "username", "")
            or fallback)


# The longest a name is printed in an email. A real name of any length people
# use fits; what it stops is a sentence typed into a name field.
NAME_LIMIT = 60


def one_line(text, limit=NAME_LIMIT):
    """`text` as a single plain line of at most `limit` characters.

    Control characters (other than the spaces among them) and invisible
    formatting ones - right-to-left overrides, zero-width spaces - are dropped,
    every run of whitespace, newlines included, becomes one space, and a
    longer text is cut with an ellipsis."""
    kept = "".join(ch for ch in str(text)
                   if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf"))
    line = " ".join(kept.split())
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "\u2026"


def entered_name(person, fallback=""):
    """The name somebody typed into their account, and NOTHING else: not the
    username, which on this model is their email address.

    `display_name` is right for a screen behind the sign-in and wrong for an
    email, whose subject, text part and calendar invite are kept by Brevo and
    Google. A member of staff with no first or last name (the ISA may create
    one with just an address, and Google may send no given name) would read as
    their address in a colleague's subject line, "sw@agency.ph booked a session
    on your calendar". The emails to psychologists and social workers
    (accounts/email_notifications.py, scheduling/session_mail.py,
    scheduling/calendar_invite.py) name people through this and say `fallback`
    when there is no name.

    **The name comes back as one plain line** (`one_line`). Anyone can ask for
    an account under any name, an administrator approves what they see in a
    queue, and the name is then printed inside an email that really does come
    from the agency: in a subject, a lead sentence, a log line. A name with a
    newline in it would start a new line of all three, one with a right-to-left
    override would reverse the rest of the sentence including the case
    reference, and a name of three hundred characters is a paragraph of the
    sender's choosing. A name made only of such characters is no name, and
    reads as `fallback`."""
    name = getattr(person, "fullname", "")
    if isinstance(name, str):
        name = one_line(name)
    return name or fallback
