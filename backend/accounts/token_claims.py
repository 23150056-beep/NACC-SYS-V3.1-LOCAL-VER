"""Tie a token to the password it was issued under.

Changing a password is supposed to end the sessions that used the old one.
SimpleJWT is stateless, so by default it cannot: a refresh token issued this
morning keeps minting access tokens until tomorrow, whatever the password does
in between. "Please sign in again" would have been theatre - the old session
could renew itself straight through it.

Every token now carries `pwd`, Django's own `get_session_auth_hash()` - an
HMAC of the password hash, which is exactly the value Django's session
framework uses for the same purpose. Setting a new password changes it, so
every token minted under the old one stops matching and is refused. No
blacklist table, no periodic flush, nothing to keep in sync.

**A token with no `pwd` claim is refused.** Those were issued before this
existed and cannot be checked, and a check that waves through what it cannot
verify is not a check. The cost is that everybody signs in once after this
ships - which is the behaviour being added anyway.

Helpers only, and deliberately free of DRF view imports: the authentication
class reads this at settings-load time, and importing a view from here made
that a circular import.
"""

CLAIM = "pwd"

STALE = "This session ended when the password was changed. Please sign in again."


UNKNOWN_ROLE = ("This account's role is not set up in this version of NACC SYS. "
                "Ask an administrator.")


def refuse_unknown_role(user):
    """Refuse an account whose role row the code does not know.

    One rule for every door: the authentication class, the token minted at
    sign-in (password and Google both go through LoginSerializer.get_token)
    and the refresh. An account with NO role is a different refusal, made in
    the authentication class and left as it was. Imported inside the function
    because this module is read at settings-load time, before the apps are.
    """
    from rest_framework.exceptions import AuthenticationFailed

    from accounts import capabilities

    role = getattr(user, "role", None)
    if role is not None and role.role_name not in capabilities.EVERY_ROLE:
        # A dict detail puts the code in the 401 body beside the sentence, so
        # the sign-in page can tell this refusal from a wrong password (it is
        # raised only after the password was accepted). response.data["detail"]
        # is still the sentence, and still carries .code.
        raise AuthenticationFailed({"detail": UNKNOWN_ROLE, "code": "unknown_role"},
                                   code="unknown_role")


def stamp(token, user):
    """Put the current password hash on a freshly minted token."""
    token[CLAIM] = user.get_session_auth_hash()
    return token


def matches(user, claimed):
    return bool(claimed) and claimed == user.get_session_auth_hash()
