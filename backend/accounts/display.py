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


def display_name(person, fallback=""):
    """The name to show for a user. Never their email address.

    Tolerates None, which genuinely occurs: an unassigned child has no
    psychologist, and a system-generated activity row has no actor.
    """
    return (getattr(person, "fullname", "")
            or getattr(person, "username", "")
            or fallback)


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
    """
    return getattr(person, "fullname", "") or fallback
