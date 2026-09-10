import logging
import secrets
import string

from rest_framework import generics, permissions, viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from accounts.google_auth import (
    AccessRequestPending, SignupThrottled, link_google_account,
    resolve_google_user, verify_google_credential,
)
from accounts import email_verification
from accounts.lockout import client_ip, clear_failures, is_locked, register_failure
from accounts.models import Role, UserProfile
from accounts import signup_limit
from accounts.permissions import IsAdministrator, IsAdminOrStaff
from children.models import Child
from accounts.serializers import (
    LoginSerializer, UserSerializer, UserWriteSerializer, RoleSerializer,
    ChangePasswordSerializer, UserProfileSerializer,
    SignupSerializer,
)
from activity.models import ActivityLog
from activity.serializers import ActivityLogSerializer
from activity.services import log_activity
from children.notifications import send_temporary_password_notification
from accounts.sms_notifications import (
    notify_temporary_password, start_phone_verification,
    confirm_phone_verification)
from accounts.sms import check_gateway, send_sms
from accounts.phone import (normalise_ph_mobile, InvalidPhilippineMobile,
                            as_typed as phone_as_typed)

logger = logging.getLogger(__name__)

User = get_user_model()

# Unambiguous alphabet for admin-issued temporary passwords — excludes
# characters that are easy to mis-key/mis-read: 0/O, 1/l/I.
_AMBIGUOUS_CHARS = set("0O1lI")
_TEMP_PASSWORD_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits if c not in _AMBIGUOUS_CHARS)


def _generate_temp_password(length=12):
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer

    def post(self, request, *args, **kwargs):
        email = request.data.get("email", "") or ""
        ip = client_ip(request)

        # Locked means locked — don't even attempt authentication, correct
        # credentials or not, and never reveal whether the account exists.
        locked, retry_after = is_locked(email, ip)
        if locked:
            minutes = (retry_after + 59) // 60  # round up to whole minutes
            return Response(
                {"detail": f"Too many failed login attempts. Try again in {minutes} minute(s)."},
                status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            self._register_failure(email, ip)
            raise

        clear_failures(email, ip)
        return response

    def _register_failure(self, email, ip):
        _locked, _retry_after, new_lockout = register_failure(email, ip)
        if new_lockout:
            # No authenticated actor caused this — the system locked the
            # account/IP out. log_activity accepts actor=None (logged as
            # "System"), so there's no need to look up the user.
            log_activity(
                None, ActivityLog.UPDATED, ActivityLog.SECURITY,
                entity_type="User",
                entity_label=f"Login locked for {email} after repeated failures")


class GoogleLoginView(generics.GenericAPIView):
    """Exchange a Google ID token for this system's own JWT pair.

    Returns the same {refresh, access, user} shape as LoginView, so the
    frontend stores the session identically however the user signed in.
    """

    # No authenticators on purpose: a stale or expired access token sitting in
    # the browser must not stop someone from signing in again. The cost is
    # that DRF would render AuthenticationFailed as 403 (it downgrades 401
    # when a view exposes no WWW-Authenticate scheme), so the 401 is returned
    # explicitly below — this is an authentication endpoint and callers, the
    # frontend included, branch on that status.
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = None

    def post(self, request):
        try:
            claims = verify_google_credential(request.data.get("credential"))
            user = resolve_google_user(
                claims, request.data.get("requested_role"), ip=client_ip(request))
        except SignupThrottled as exc:
            return Response({"detail": exc.detail},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        except AccessRequestPending as exc:
            # 403, not 401: Google authenticated them fine — this system has
            # simply not authorised them yet. The distinct status and `state`
            # let the login page show a waiting screen instead of an error the
            # person would retry forever.
            return Response(
                {"detail": exc.detail, "state": "pending_approval",
                 "role_required": exc.role_required},
                status=status.HTTP_403_FORBIDDEN)
        except AuthenticationFailed as exc:
            return Response({"detail": exc.detail},
                            status=status.HTTP_401_UNAUTHORIZED)

        newly_linked = link_google_account(user, claims)

        # Reuse LoginSerializer.get_token so the access token carries the same
        # role claim as a password login — anything reading it downstream
        # cannot tell the two paths apart.
        refresh = LoginSerializer.get_token(user)

        if newly_linked:
            log_activity(
                user, ActivityLog.UPDATED, ActivityLog.SECURITY,
                entity_type="User", entity_id=user.id,
                entity_label="Linked Google account for sign-in")
        log_activity(user, ActivityLog.LOGIN, ActivityLog.SECURITY)

        return Response({
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": UserSerializer(user).data,
        }, status=status.HTTP_200_OK)


class VerifySignupEmailView(generics.GenericAPIView):
    """Confirm a typed address by the code mailed to it.

    Open, and it has to be: the applicant cannot sign in — a PENDING account is
    refused by design — so there is no session to authenticate this against.
    What protects it is that the code is six digits, short-lived, guess-limited
    and burned on use.

    Every failure reads the same from outside, for the reason the sign-up form
    gives one refusal for every address already spoken for: a different answer
    would turn this into a way to ask whether somebody works at the agency.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = None

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        ok, message = email_verification.confirm(email, request.data.get("code"))
        if not ok:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            # A confirmed code with no row behind it: nothing to mark, and
            # saying so would leak which addresses exist.
            return Response({"detail": message}, status=status.HTTP_200_OK)
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])
        return Response({"detail": message}, status=status.HTTP_200_OK)


class SignupView(generics.GenericAPIView):
    """Open sign-up: creates a request, never an account with access.

    The counterpart to the Google path in accounts.google_auth, for people
    without a Google address. Same outcome, same protections: a PENDING row
    with no role, the stated role recorded as a claim, the same per-IP and
    queue limits, and the same refusal to say whether an address already
    exists.

    Deliberately returns 202 and no tokens. Registering is not being let in —
    an administrator decides, and until then the account cannot authenticate
    because is_active follows status.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = SignupSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Checked only once the payload is known good, and only on the path
        # that creates a row — the same reasoning as the Google flow, where a
        # returning applicant must never be throttled out of their own status.
        ip = client_ip(request)
        if signup_limit.ip_is_throttled(ip) or signup_limit.queue_is_full():
            logger.warning("Refused access request from %s (throttled)",
                           serializer.validated_data.get("email"))
            return Response(
                {"detail": "Too many access requests right now. Try again "
                           "later, or ask an administrator to create the "
                           "account for you."},
                status=status.HTTP_429_TOO_MANY_REQUESTS)

        user = serializer.save()
        signup_limit.register_attempt(ip)
        # A typed address is only what somebody typed. Approval emails a
        # temporary password, so the address has to be proved before an
        # administrator can hand a credential to a typo.
        email_verification.start(user.email)
        log_activity(
            None, ActivityLog.CREATED, ActivityLog.SECURITY,
            entity_type="User",
            entity_label=(f"{user.fullname or user.email} — access request "
                          f"(asked for: "
                          f"{user.requested_role.role_name if user.requested_role else 'none stated'})"))
        return Response(
            {"detail": "Your request has been sent. An administrator will "
                       "review it and you will be able to sign in once it is "
                       "approved.",
             "state": "pending_approval"},
            status=status.HTTP_202_ACCEPTED)


class EmailConfigTestView(generics.GenericAPIView):
    """Send a test email and report exactly what Brevo said.

    Every other send here is fire-and-forget, so a rejected message is
    invisible unless someone reads the server log — and on Render's free plan
    an administrator cannot. Diagnosing "no email arrived" therefore meant
    guessing at settings one at a time. This turns that into one button and a
    sentence.

    Administrator only, and it sends to the caller's own address: it must not
    become a way to send mail to anyone from inside the app.
    """

    permission_classes = [IsAdministrator]
    serializer_class = None

    def post(self, request):
        from children.notifications import send_test_email

        ok, detail = send_test_email(getattr(request.user, "email", ""))
        return Response({
            "ok": ok,
            "detail": detail,
            "sender": settings.BREVO_SENDER_EMAIL,
            "recipient": getattr(request.user, "email", ""),
            "key_configured": bool(settings.BREVO_API_KEY),
        })


class GoogleAuthConfigView(generics.GenericAPIView):
    """Tells the login page whether to render the Google button.

    The client ID is public by design (it ships in the page anyway), but
    serving it from here means the frontend does not need a rebuild to turn
    Google Sign-In on or off — only the API's environment changes.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = None

    def get(self, request):
        from django.conf import settings as django_settings
        client_id = django_settings.GOOGLE_OAUTH_CLIENT_ID
        return Response({
            "enabled": bool(client_id),
            "client_id": client_id,
            "allowed_domains": django_settings.GOOGLE_ALLOWED_DOMAINS,
        })


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(generics.GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_activity(
            request.user, ActivityLog.UPDATED, ActivityLog.SECURITY,
            entity_type="User", entity_label="Changed own password", entity_id=request.user.id)
        # Every token minted under the old password stopped working the moment
        # it changed - see accounts/token_auth.py. Saying so here means the two
        # screens that change a password do not each decide for themselves
        # whether to sign the person out.
        return Response({"detail": "Password changed.", "reauthenticate": True},
                        status=status.HTTP_200_OK)


class MyProfileView(generics.RetrieveUpdateAPIView):
    """A person's own optional details. Theirs, and only theirs.

    There is deliberately no id in the URL and no way to name another
    account: get_object returns request.user's row and nothing else, which is
    the same rule ChangePasswordView follows. An endpoint that took an id
    would need a permission check, and a permission check is a thing that can
    be got wrong.

    The row is created on first write rather than with the account, so an
    account that never opens this page never gets one.
    """

    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile


class MyPhoneView(generics.GenericAPIView):
    """Your own mobile number: set it, and prove it is yours.

    Bound to request.user and taking no id, like the profile endpoint beside
    it. POST starts verification by texting a code; PUT confirms one.

    Kept off the ordinary user-edit path on purpose. An administrator can type
    a number into somebody's record, but only the person holding the handset
    can mark it verified, and only a verified number ever gets a message.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = None

    def get(self, request):
        user = request.user
        return Response({
            "phone": user.phone,
            "phone_display": phone_as_typed(user.phone),
            "phone_verified": user.phone_verified,
        })

    def post(self, request):
        """Send a code to the number supplied."""
        try:
            number = normalise_ph_mobile(request.data.get("phone"))
        except InvalidPhilippineMobile as exc:
            return Response({"phone": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not number:
            return Response({"phone": "Enter your mobile number."},
                            status=status.HTTP_400_BAD_REQUEST)

        result = start_phone_verification(request.user, number)
        if not result.ok:
            # The gateway's own words. A code that never arrives with no
            # explanation is how the mail integration cost a day.
            return Response({"detail": result.detail},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({"detail": f"We sent a code to {phone_as_typed(number)}. "
                                   f"It expires in 10 minutes."},
                        status=status.HTTP_200_OK)

    def put(self, request):
        """Confirm the code, which is what marks the number usable."""
        ok, message = confirm_phone_verification(
            request.user, request.data.get("code"))
        if not ok:
            return Response({"code": message}, status=status.HTTP_400_BAD_REQUEST)
        log_activity(request.user, ActivityLog.UPDATED, ActivityLog.SECURITY,
                     entity_type="User", entity_label="Verified own mobile number",
                     entity_id=request.user.id)
        return Response({"detail": message,
                         "phone": request.user.phone,
                         "phone_display": phone_as_typed(request.user.phone),
                         "phone_verified": True}, status=status.HTTP_200_OK)

    def delete(self, request):
        """Stop texts without waiting for an administrator."""
        user = request.user
        user.phone = ""
        user.phone_verified = False
        user.save(update_fields=["phone", "phone_verified", "updated_at"])
        return Response({"detail": "Number removed. You will not get text "
                                   "notifications.", "phone": "",
                         "phone_verified": False}, status=status.HTTP_200_OK)


class SmsConfigTestView(generics.GenericAPIView):
    """Ask the gateway, and print exactly what it says.

    The mirror of the email test button, and it exists for the same reason:
    every SMS send is fire-and-forget on a background thread, so a refused
    message looks precisely like a delivered one from the outside. This is
    synchronous, and it reports the gateway's own words.

    Sends only to the administrator's own verified number — a diagnostic that
    can text arbitrary numbers is a diagnostic somebody will point at a
    stranger.
    """

    permission_classes = [IsAdministrator]
    serializer_class = None

    def get(self, request):
        """Confirm the key and the balance without spending a message.

        GET asks, POST sends - which is the ordinary meaning of both verbs and
        also the order somebody should do them in. PhilSMS ships five free
        credits and has no sandbox, so the key has to be diagnosable without
        burning one, and this needs no verified handset either: requiring one
        first is what made a bad key hard to tell from a bad number.
        """
        result = check_gateway()
        return Response({
            "ok": result.ok,
            "detail": result.detail,
            "provider": settings.SMS_PROVIDER or "console",
            "sender": settings.SMS_SENDER_NAME or "(the gateway default)",
        }, status=status.HTTP_200_OK)

    def post(self, request):
        user = request.user
        if not user.phone:
            return Response(
                {"ok": False,
                 "detail": "Add your own mobile number first — this sends the "
                           "test to you, not to anyone else."},
                status=status.HTTP_400_BAD_REQUEST)
        # A different reference each time. The gateway's own guidance is that
        # repeatedly sending nearly identical text to one number is classified
        # as spam by the telcos, and this is the single message an
        # administrator sends over and over while getting the key right.
        reference = f"{secrets.randbelow(1_000_000):06d}"
        result = send_sms(
            user.phone,
            f"NACC SYS: test message {reference}. If you can read this, text "
            f"notifications are working.",
            "configuration test")
        return Response({
            "ok": result.ok,
            "detail": result.detail,
            "provider": settings.SMS_PROVIDER,
            "sender": settings.SMS_SENDER_NAME or "(the gateway default)",
            "recipient": phone_as_typed(user.phone),
        }, status=status.HTTP_200_OK)


class UserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdministrator]

    # Unpaginated on purpose, and this is the reasoning rather than an
    # oversight. RACCO I is one regional office: a handful of psychologists,
    # perhaps a dozen staff, one administrator. That set does not grow with
    # caseload the way children and activity entries do, and the directory
    # filters and sorts client-side so the whole list is what the screen wants
    # anyway.
    #
    # What DOES grow without bound is declined Google sign-ups. They are
    # archived rather than deleted — that is what stops a refused address
    # asking again — and the directory asks for include_archived=true, so they
    # accumulate in every page load. At a few a month that is invisible; at a
    # few thousand it is not.
    #
    # The threshold to act on: when declined requests pass roughly 500, add
    # server-side pagination and move search into the queryset. Until then this
    # is one query returning a few dozen rows, and pagination would be
    # machinery around a list that fits on a screen.
    pagination_class = None

    def get_queryset(self):
        qs = User.objects.all().order_by("last_name", "first_name")
        # Hiding archived users is a *list* concern only. Detail routes have to
        # reach a deactivated account by id — otherwise reactivate/ is
        # unreachable for exactly the users it exists to serve.
        if self.action == "list" and self.request.query_params.get("include_archived") != "true":
            qs = qs.exclude(status=User.ARCHIVED)
        return qs

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserWriteSerializer
        return UserSerializer

    def _log(self, user, action_name):
        log_activity(
            self.request.user, action_name, ActivityLog.USER,
            entity_type="User",
            entity_label=(user.fullname or user.email),
            entity_id=user.id)

    def create(self, request, *args, **kwargs):
        """Create a user with a server-generated temporary password, returned
        exactly once (same contract as reset_password). Any client-supplied
        password is ignored by the serializer."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        temp_password = _generate_temp_password()
        user.set_password(temp_password)
        user.must_change_password = True
        update_fields = ["password", "must_change_password", "updated_at"]
        # Single-admin handover: a new Administrator created while another
        # admin is active takes over at first login (accounts/serializers.py).
        if (user.role and user.role.role_name == Role.ADMINISTRATOR
                and User.objects.filter(role__role_name=Role.ADMINISTRATOR,
                                        status=User.ACTIVE).exclude(pk=user.pk).exists()):
            user.admin_takeover_pending = True
            update_fields.append("admin_takeover_pending")
        user.save(update_fields=update_fields)
        email_queued = send_temporary_password_notification(user, temp_password)
        # The password travels by email only. This says one is waiting.
        sms_queued = notify_temporary_password(user)
        self._log(user, ActivityLog.CREATED)
        data = UserSerializer(user).data
        data["temp_password"] = temp_password
        data["email_queued"] = email_queued
        data["sms_queued"] = sms_queued
        return Response(data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        user = serializer.save()
        self._log(user, ActivityLog.UPDATED)

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        user = self.get_object()
        if user.is_last_active_administrator():
            return Response(
                {"detail": "This is the only administrator account. Create the "
                           "replacement administrator first — that hands over "
                           "properly — then deactivate this one."},
                status=status.HTTP_400_BAD_REQUEST)
        user.status = User.ARCHIVED
        # is_active follows status automatically (User.save).
        user.save(update_fields=["status", "updated_at"])
        self._log(user, ActivityLog.ARCHIVED)
        return Response({"status": "archived"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        """Undo a deactivation. Administrators are deliberately excluded: a
        deactivated administrator can only come back through a brand-new
        account (product decision 2026-07-18), and a reactivate route would
        quietly reopen the handover path that decision closed.

        The old password is left working — an account is usually deactivated
        when someone leaves, so it is flagged for a forced change instead:
        whoever comes back signs in once with the old credentials and has to
        set a new password before they reach any case data.

        That flag is set only where there is a password to change. An account
        created through the Google door has `set_unusable_password()`, and the
        gate the flag raises asks for the CURRENT password and checks it with
        `check_password()` — which no value can satisfy on an unusable one. So
        flagging a Google colleague on the way back in forced nothing and shut
        them out for good; the only route back was a new account. They
        re-authenticate with Google, which is the credential they actually
        have."""
        user = self.get_object()
        if user.status != User.ARCHIVED:
            return Response({"detail": "This account is already active."},
                            status=status.HTTP_400_BAD_REQUEST)
        if user.role and user.role.role_name == Role.ADMINISTRATOR:
            return Response(
                {"detail": "A deactivated administrator cannot be reactivated. "
                           "Create a new administrator account instead."},
                status=status.HTTP_400_BAD_REQUEST)
        # A declined Google request is also an archived account, and it has no
        # role because it was never approved. Reactivating one would turn a
        # refused stranger into an active account — this route is the undo for
        # a deactivated colleague, not a second chance at approval.
        if user.role_id is None:
            return Response(
                {"detail": "This account was never approved, so there is "
                           "nothing to restore. Approve a new request from "
                           "Access Requests instead."},
                status=status.HTTP_400_BAD_REQUEST)
        user.status = User.ACTIVE
        user.must_change_password = user.has_usable_password()
        user.save(update_fields=["status", "must_change_password", "updated_at"])
        self._log(user, ActivityLog.UPDATED)
        return Response(
            {"status": user.status,
             "must_change_password": user.must_change_password},
            status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        """This account's recent history, both directions.

        What they DID answers the question an agency accountable under RA
        10173 actually has to answer — who opened this child's record. What was
        done TO the account is the other half of the same story: who issued
        them a password, who approved them, who deactivated them. Either alone
        leaves a gap someone could hide in, so the two streams are merged and
        each entry says which it is.
        """
        user = self.get_object()
        entries = list(
            ActivityLog.objects
            .filter(Q(actor=user) | Q(entity_type="User", entity_id=user.id))
            .order_by("-created_at")[:25])
        data = ActivityLogSerializer(entries, many=True).data
        for row, entry in zip(data, entries):
            # The drawer has to distinguish "she archived a case" from "her
            # account was archived" — same verb, opposite meaning.
            row["by_them"] = entry.actor_id == user.id
        return Response(data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Grant a pending sign-up its role and let it in.

        Both doors land here: the Google flow in accounts.google_auth and the
        password form at auth/signup/. Nothing below reads how they arrived,
        because the decision is the same either way.

        The role must be supplied explicitly. There is deliberately NO fallback
        to `requested_role`: the applicant's claim is a hint for the dropdown,
        and defaulting to it would quietly turn every request that omitted the
        field into self-assignment — which is the exact thing this whole flow
        exists to prevent.
        """
        user = self.get_object()
        if user.status != User.PENDING:
            return Response({"detail": "This account is not awaiting approval."},
                            status=status.HTTP_400_BAD_REQUEST)
        # Approving emails a temporary password. An address nobody has proved
        # exists is an address that credential may be handed to by mistake, so
        # the check bites here rather than merely showing beside the row.
        # Google requests arrive verified — Google checked the address.
        if not user.email_verified:
            return Response(
                {"detail": "This applicant has not confirmed their email "
                           "address yet, and approving would send a temporary "
                           "password to an address nobody has verified. Ask "
                           "them to enter the code sent when they registered."},
                status=status.HTTP_400_BAD_REQUEST)

        role_id = request.data.get("role")
        if not role_id:
            return Response({"role": "Choose the role this account should have."},
                            status=status.HTTP_400_BAD_REQUEST)
        role = Role.objects.filter(pk=role_id).first()
        if role is None:
            return Response({"role": "Unknown role."},
                            status=status.HTTP_400_BAD_REQUEST)
        # Approving cannot mint an administrator. That account is the agency's
        # recovery path and carries the single-admin handover rule with it;
        # reaching it through this door would route around both.
        if role.role_name == Role.ADMINISTRATOR:
            return Response(
                {"role": "An administrator cannot be created by approving a "
                         "request. Add the account from User Management instead."},
                status=status.HTTP_400_BAD_REQUEST)

        asked = user.requested_role.role_name if user.requested_role else "none stated"
        user.role = role
        user.status = User.ACTIVE
        user.save(update_fields=["role", "status", "updated_at"])
        # Both roles in the audit line: when someone asked for more than they
        # were given, that should be legible a year later.
        log_activity(
            request.user, ActivityLog.UPDATED, ActivityLog.USER,
            entity_type="User",
            entity_label=(f"{user.fullname or user.email} — access approved as "
                          f"{role.role_name} (asked for: {asked})"),
            entity_id=user.id)
        return Response(UserSerializer(user).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        """Refuse a pending sign-up.

        Archives rather than deletes: an archived address cannot re-register
        itself (accounts/google_auth.py), so a declined applicant cannot simply
        sign in again for a fresh request and wait for a distracted approval.
        Deleting the row would hand them exactly that loop.
        """
        user = self.get_object()
        if user.status != User.PENDING:
            return Response({"detail": "This account is not awaiting approval."},
                            status=status.HTTP_400_BAD_REQUEST)
        asked = user.requested_role.role_name if user.requested_role else "none stated"
        user.status = User.ARCHIVED
        user.save(update_fields=["status", "updated_at"])
        log_activity(
            request.user, ActivityLog.ARCHIVED, ActivityLog.USER,
            entity_type="User",
            entity_label=(f"{user.fullname or user.email} — access request "
                          f"declined (asked for: {asked})"),
            entity_id=user.id)
        return Response({"status": user.status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """Admin-issued temporary password. Never accepts a password from the
        request — always generated server-side and returned exactly once."""
        user = self.get_object()
        # Deliberately "not ACTIVE" rather than "is ARCHIVED": a pending
        # account has no role yet, and issuing it a password would hand out a
        # working credential before anyone approved the person.
        if user.status != User.ACTIVE:
            return Response(
                {"detail": "Cannot reset the password for an inactive or archived user."},
                status=status.HTTP_400_BAD_REQUEST)
        temp_password = _generate_temp_password()
        user.set_password(temp_password)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password", "updated_at"])
        email_queued = send_temporary_password_notification(user, temp_password)
        # The password travels by email only. This says one is waiting.
        sms_queued = notify_temporary_password(user)
        self._log(user, ActivityLog.UPDATED)
        return Response({"temp_password": temp_password,
                         "email_queued": email_queued,
                         "sms_queued": sms_queued},
                        status=status.HTTP_200_OK)


class RoleListView(generics.ListAPIView):
    permission_classes = [IsAdministrator]
    pagination_class = None
    serializer_class = RoleSerializer

    def get_queryset(self):
        return Role.objects.all().order_by("role_name")


class PsychologistListView(generics.GenericAPIView):
    """Active psychologists + current caseload (active assigned children).
    Admin + Staff so Staff can populate the assign picker and gauge workload."""
    permission_classes = [IsAdminOrStaff]
    pagination_class = None

    def get(self, request):
        qs = (User.objects
              .filter(role__role_name=Role.PSYCHOLOGIST, status=User.ACTIVE)
              .annotate(caseload=Count("assigned_children",
                                       filter=Q(assigned_children__status=Child.ACTIVE)))
              .order_by("last_name", "first_name"))
        return Response([
            {"id": p.id, "name": p.fullname or p.email, "caseload": p.caseload} for p in qs
        ])
