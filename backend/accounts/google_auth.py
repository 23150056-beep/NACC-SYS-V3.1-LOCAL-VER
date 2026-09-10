"""Google Sign-In and sign-up for staff and psychologists.

The flow: the browser gets an ID token from Google, POSTs it here, and this
module decides what that address is entitled to. One button does both jobs —
first use registers, later uses sign in.

Registering is not the same as being let in. This system holds child case
records, and sign-up is open to any Gmail address, so a new address creates an
account in PENDING with no role and no access — a request sitting in a queue,
nothing more. An administrator approves it and assigns Staff or Psychologist
before it can reach anything. The person may state which role they want; that
is a claim recorded on the request, and this module never acts on it.

Two refusals are absolute and predate the sign-up flow:

* Administrators authenticate with a password, never through Google. The admin
  account is the agency's recovery path, and tying it to a third-party identity
  provider means an outage there locks RACCO I out of its own records.
* An archived address cannot re-register itself. Deactivation would otherwise
  be undone by the deactivated person.
"""

import logging

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework.exceptions import AuthenticationFailed

from accounts import signup_limit
from accounts.models import Role, User

logger = logging.getLogger(__name__)

# Google signs its ID tokens under one of these two issuer strings.
_VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

# Deliberately vague, and identical for "no such account", "wrong role" and
# "archived". A precise message here would let anyone with a Google account
# probe which addresses belong to agency staff.
_GENERIC_DENIAL = (
    "This Google account is not authorised for the system. Ask an "
    "Administrator to create your account first, or sign in with your "
    "email and password."
)


# Roles a person may ask for. Administrator is absent deliberately — that
# account is created by an existing administrator and never through this door.
_REQUESTABLE_ROLES = (Role.PSYCHOLOGIST, Role.STAFF)


class GoogleAuthUnavailable(AuthenticationFailed):
    """Raised when Google Sign-In is not configured on this server."""


class SignupThrottled(AuthenticationFailed):
    """Too many access requests, by address or in total.

    One message for both limits on purpose: which ceiling was hit is
    operational detail, and telling someone whether the queue is full or their
    own address is capped only helps them work around it.
    """


class AccessRequestPending(AuthenticationFailed):
    """The address maps to an account waiting on an administrator.

    Deliberately not a denial. The person has done everything they can, and the
    login page needs to tell them so rather than showing a failure they will
    retry forever. `role_required` is True while the request has no claimed
    role yet, which is the login page's cue to ask.

    Saying this much is safe here in a way it would not be on the password
    form: probing an address through Google requires already controlling that
    Google account, so an attacker learns nothing about anyone but themselves.
    """

    def __init__(self, detail, role_required=False):
        super().__init__(detail)
        self.role_required = role_required


def verify_google_credential(credential):
    """Validate a Google ID token and return its claims.

    google-auth checks the signature against Google's rotating public keys and
    verifies audience and expiry. Anything it rejects, we reject.
    """
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    if not client_id:
        raise GoogleAuthUnavailable(
            "Google Sign-In is not configured on this server.")
    if not credential:
        raise AuthenticationFailed("Missing Google credential.")

    try:
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            client_id,
            # Small tolerance for clock drift between Google and this
            # container; without it a correct token can fail near its edges.
            clock_skew_in_seconds=10,
        )
    except ValueError as exc:
        # Covers a bad signature, the wrong audience, an expired token and
        # malformed input alike — none of which the caller should be able to
        # tell apart.
        logger.warning("Rejected Google credential: %s", exc)
        raise AuthenticationFailed("Google sign-in failed. Please try again.") from exc

    if claims.get("iss") not in _VALID_ISSUERS:
        raise AuthenticationFailed("Google sign-in failed. Please try again.")

    # An unverified address proves only that someone typed it into a Google
    # account, so it must never be matched against an agency account.
    if not claims.get("email_verified"):
        raise AuthenticationFailed(
            "Your Google account's email address is not verified.")

    if not claims.get("email"):
        raise AuthenticationFailed("Google did not return an email address.")

    allowed_domains = settings.GOOGLE_ALLOWED_DOMAINS
    if allowed_domains:
        domain = claims["email"].rsplit("@", 1)[-1].lower()
        if domain not in allowed_domains:
            raise AuthenticationFailed(
                "Only agency Google accounts may sign in this way.")

    return claims


def _record_requested_role(user, requested_role_name):
    """Store what the person says they are. A claim, never a grant — the only
    code that reads it is the approval endpoint, to pre-fill a dropdown."""
    if not requested_role_name or user.requested_role_id:
        return
    if requested_role_name not in _REQUESTABLE_ROLES:
        return
    role = Role.objects.filter(role_name=requested_role_name).first()
    if role:
        user.requested_role = role
        user.save(update_fields=["requested_role", "updated_at"])


def _pending_response(user):
    """The one outcome that is neither success nor refusal."""
    if user.requested_role_id is None:
        raise AccessRequestPending(
            "Tell us your role to finish your request.", role_required=True)
    raise AccessRequestPending(
        "Your request is with an administrator. You will be able to sign in "
        "once it is approved.")


_THROTTLED = (
    "Too many access requests right now. Please try again later, or ask an "
    "Administrator to create your account."
)


def resolve_google_user(claims, requested_role_name=None, ip=None):
    """Map verified Google claims onto a User, registering a new one if needed.

    Returns an ACTIVE, eligible User, or raises: AccessRequestPending when the
    address belongs to a request still awaiting a decision, AuthenticationFailed
    for anything refused outright.
    """
    sub = claims["sub"]
    email = claims["email"].strip().lower()

    # Prefer the stable subject id; fall back to email only for the first
    # sign-in of an account an administrator created by hand, when there is
    # nothing linked yet.
    user = User.objects.filter(google_sub=sub).first()
    if user is None:
        user = User.objects.filter(email__iexact=email, google_sub__isnull=True).first()

    if user is None:
        # An email already in use but linked to a *different* Google subject is
        # not a new registration — it is someone arriving at an address that is
        # already spoken for. Refuse rather than collide.
        if User.objects.filter(email__iexact=email).exists():
            raise AuthenticationFailed(_GENERIC_DENIAL)
        # Checked only on the path that creates a row. Someone returning to
        # see whether they have been approved must never be throttled out of
        # reading their own status.
        if signup_limit.ip_is_throttled(ip) or signup_limit.queue_is_full():
            logger.warning("Refused Google access request from %s (throttled)", email)
            raise SignupThrottled(_THROTTLED)
        user = _register_access_request(claims, requested_role_name)
        signup_limit.register_attempt(ip)
        _pending_response(user)

    role_name = user.role.role_name if user.role else None

    # Administrators are excluded on purpose: the admin account is the
    # system's recovery path, and tying it to a third-party identity provider
    # means an outage or a lost Google account locks the agency out of its own
    # records. Admins keep password login.
    if role_name == Role.ADMINISTRATOR:
        raise AuthenticationFailed(
            "Administrator accounts sign in with email and password, not Google.")

    # Archived first, and generic: a deactivated person must not be able to tell
    # their account still exists, nor re-register it as a fresh request.
    if user.status == User.ARCHIVED:
        raise AuthenticationFailed(_GENERIC_DENIAL)

    if user.status == User.PENDING:
        _record_requested_role(user, requested_role_name)
        _pending_response(user)

    if role_name not in _REQUESTABLE_ROLES:
        raise AuthenticationFailed(_GENERIC_DENIAL)

    if user.status != User.ACTIVE or not user.is_active:
        raise AuthenticationFailed(_GENERIC_DENIAL)

    return user


def _register_access_request(claims, requested_role_name=None):
    """Create the PENDING account that an administrator will act on.

    The account is created with an unusable password: approval grants a role,
    it does not hand out a credential, and this door must never mint one.
    """
    email = claims["email"].strip().lower()
    role = None
    if requested_role_name in _REQUESTABLE_ROLES:
        role = Role.objects.filter(role_name=requested_role_name).first()

    user = User(
        email=email,
        username=email,
        first_name=(claims.get("given_name") or "")[:150],
        last_name=(claims.get("family_name") or "")[:150],
        status=User.PENDING,
        google_sub=claims["sub"],
        requested_role=role,
        # Google checked the address before it ever reached us, so this door
        # never asks the applicant to prove it a second time.
        email_verified=True,
    )
    user.set_unusable_password()
    try:
        user.save()
    except IntegrityError:
        # Two tabs, one person: whichever insert lost the race, the request
        # already exists and that is the correct outcome either way.
        existing = User.objects.filter(google_sub=claims["sub"]).first()
        if existing is None:
            raise
        return existing
    logger.info("New Google access request from %s", email)
    return user


def link_google_account(user, claims):
    """Record the Google link on first use and return whether it was new."""
    updates = []
    newly_linked = False

    if not user.google_sub:
        user.google_sub = claims["sub"]
        updates.append("google_sub")
        newly_linked = True

    # A Google user has no password to rotate, so leaving this flag set would
    # trap them in the change-password gate with nothing to change.
    if user.must_change_password:
        user.must_change_password = False
        updates.append("must_change_password")

    user.last_login = timezone.now()
    updates.append("last_login")

    if updates:
        updates.append("updated_at")
        user.save(update_fields=updates)

    return newly_linked
