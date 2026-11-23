"""The refresh route, bound to the password its token was issued under.

The half that matters most: a refresh token outlives its access token by a
day, so leaving it valid would let an old session quietly renew itself long
after the password was replaced. See accounts/token_claims.py for the claim.
"""
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.token_claims import CLAIM, STALE, matches, refuse_unknown_role, stamp


class PasswordBoundTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        # Signature and expiry first - an unreadable token has no claims worth
        # comparing, and UntypedToken raising here is the correct answer.
        token = UntypedToken(attrs["refresh"])
        user = get_user_model().objects.select_related("role").filter(pk=token.get("user_id")).first()
        if user is None or not matches(user, token.get(CLAIM)):
            raise InvalidToken(STALE)
        # An unknown role cannot renew a session any more than start one.
        refuse_unknown_role(user)

        data = super().validate(attrs)
        # The reissued access token carries the claim forward, otherwise the
        # very next request would be refused for not having one.
        access = RefreshToken(attrs["refresh"]).access_token
        stamp(access, user)
        data["access"] = str(access)
        return data


class PasswordBoundTokenRefreshView(TokenRefreshView):
    serializer_class = PasswordBoundTokenRefreshSerializer
