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


def stamp(token, user):
    """Put the current password hash on a freshly minted token."""
    token[CLAIM] = user.get_session_auth_hash()
    return token


def matches(user, claimed):
    return bool(claimed) and claimed == user.get_session_auth_hash()
