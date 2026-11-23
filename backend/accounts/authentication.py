from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.settings import api_settings

from accounts.token_claims import CLAIM, STALE, matches, refuse_unknown_role

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
    forced password change, and an account whose role is missing or unknown."""

    def get_user(self, validated_token):
        """SimpleJWT's lookup, with the role joined in. Every request reads
        `user.role` (role_of, the no-role gate below, every permission class),
        so a plain get() cost a second query on each authenticated request.

        A copy of SimpleJWT 5.3.1's get_user without its CHECK_REVOKE_TOKEN
        branch, which settings.py does not turn on. Turning that setting on,
        or upgrading SimpleJWT, means bringing this copy up to date."""
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
        except KeyError:
            raise InvalidToken(_("Token contained no recognizable user identification"))
        try:
            user = self.user_model.objects.select_related("role").get(
                **{api_settings.USER_ID_FIELD: user_id})
        except self.user_model.DoesNotExist:
            raise AuthenticationFailed(_("User not found"), code="user_not_found")
        if not user.is_active:
            raise AuthenticationFailed(_("User is inactive"), code="user_inactive")
        return user

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

        # An account whose role the code cannot place has no defined access.
        # Two ways to get there. No role at all: approving nothing but
        # reactivating a declined request, or creating a user without choosing
        # a role (both blocked at source now). Or a role row the code does not
        # know, which can reach a database through Django admin or a migration
        # that runs ahead of the code. Each decision in the code names the
        # roles that take each branch and refuses the rest, but this is the
        # single door they all come through, so it fails closed here too.
        if getattr(user, "role_id", None) is None:
            raise AuthenticationFailed(
                "This account has no role assigned. Ask an administrator to "
                "set one before signing in.",
                code="no_role",
            )
        refuse_unknown_role(user)

        if getattr(user, "must_change_password", False) and request.path not in _ALLOWED_WHILE_MUST_CHANGE_PASSWORD:
            raise AuthenticationFailed(
                "You must change your password before continuing.",
                code="password_change_required",
            )
        return result
