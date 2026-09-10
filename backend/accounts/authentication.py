from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.token_claims import CLAIM, STALE, matches

# Paths a user with an outstanding forced password change is still allowed to
# hit — everything else is blocked. This is what makes an admin-issued
# temporary password unusable for real work: enforcement happens here, on the
# server, not just as a UI redirect the client could skip.
_ALLOWED_WHILE_MUST_CHANGE_PASSWORD = {
    "/api/auth/change-password/",
    "/api/auth/me/",
    "/api/auth/refresh/",
}


class ForcePasswordChangeJWTAuthentication(JWTAuthentication):
    """JWTAuthentication with three server-side gates the client cannot skip:
    a token minted under a password that has since changed, an outstanding
    forced password change, and an account with no role."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result

        # Changing a password ends the sessions that used the old one. Without
        # this the promise is empty: SimpleJWT is stateless, so a token issued
        # this morning keeps working all day whatever the password does. The
        # claim is Django's own session auth hash, which changes with the
        # password. A token that carries none predates this and cannot be
        # checked — and a check that waves through what it cannot verify is
        # not a check, so everybody signs in once after this ships.
        if not matches(user, validated_token.get(CLAIM)):
            raise AuthenticationFailed(STALE, code="password_changed")

        # An active account with no role has no defined access, and the
        # codebase reads roles as "if psychologist ... else everything" in a
        # dozen places — written when every active account had one. A roleless
        # user therefore lands in the *else* branch of each, which on the
        # activity stream means the full audit trail, child names included.
        #
        # Rather than audit and invert a dozen call sites, fail closed at the
        # single door they all come through. Two paths could produce this
        # state: approving nothing but reactivating a declined request, and
        # creating a user without choosing a role. Both are now blocked at
        # source as well — this is the backstop for whatever comes next.
        if getattr(user, "role_id", None) is None:
            raise AuthenticationFailed(
                "This account has no role assigned. Ask an administrator to "
                "set one before signing in.",
                code="no_role",
            )

        if getattr(user, "must_change_password", False) and request.path not in _ALLOWED_WHILE_MUST_CHANGE_PASSWORD:
            raise AuthenticationFailed(
                "You must change your password before continuing.",
                code="password_change_required",
            )
        return result
