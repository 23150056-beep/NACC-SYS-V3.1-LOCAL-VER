from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from accounts.managers import UserManager


class Role(models.Model):
    ADMINISTRATOR = "Administrator"
    PSYCHOLOGIST = "Psychologist"
    STAFF = "Staff"

    role_name = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_role"

    def __str__(self):
        return self.role_name


class User(AbstractUser):
    ACTIVE = "active"
    # Signed up through Google but not yet approved by an administrator. Holds
    # no role and reaches nothing: the account exists only so a human has
    # something to approve.
    PENDING = "pending"
    ARCHIVED = "archived"
    STATUS_CHOICES = [
        (ACTIVE, "Active"), (PENDING, "Pending approval"), (ARCHIVED, "Archived"),
    ]

    email = models.EmailField(unique=True)
    middle_initial = models.CharField(max_length=5, blank=True)
    contact_details = models.CharField(max_length=50, blank=True)
    # The number a text message goes to, stored the way a gateway wants it
    # (+639XXXXXXXXX) rather than the way somebody typed it. contact_details
    # stays for anything else worth recording — a landline, an extension —
    # because a field that has to be machine-readable and a field that has to
    # be human-readable are not the same field.
    #
    # Never write to this directly. accounts.phone.normalise_ph_mobile is the
    # one thing that decides what a valid number is.
    phone = models.CharField(max_length=16, blank=True, default="")
    # Whether the person has proved they can receive at that number, by
    # entering a code sent to it. An unverified number is a number somebody
    # typed — texting a temporary password to a typo is worse than not
    # texting at all, so the senders check this.
    phone_verified = models.BooleanField(default=False)
    # Whether the address has been proved to exist. False for a typed
    # sign-up until a code is confirmed; true from the start for a Google
    # one, which Google already verified. Approval emails a temporary
    # password, so this is the gate on issuing a credential to a typo.
    email_verified = models.BooleanField(default=False)
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    # Set whenever an admin issues a temporary password. Server-side
    # enforcement (see accounts/authentication.py) blocks all other API
    # access until the user sets their own password.
    must_change_password = models.BooleanField(default=False)
    # Single-admin handover: set when this user is created as the successor
    # Administrator while another admin is still active. Their FIRST login
    # archives every other admin account and clears the flag (see
    # accounts/serializers.py LoginSerializer).
    admin_takeover_pending = models.BooleanField(default=False)
    # Google's stable subject identifier, stored the first time this account
    # signs in with Google. Matching on `sub` rather than email from then on
    # means a Google-side email change cannot hand someone else's session to
    # this account, and cannot lock this user out of their own.
    google_sub = models.CharField(
        max_length=255, unique=True, null=True, blank=True, editable=False)
    # What the person said about themselves when they signed up with Google.
    # A CLAIM, never a grant. Nothing in the permission system may read this
    # field — only the approval endpoint does, and only to pre-fill the
    # administrator's choice. `role` stays null until a human decides.
    requested_role = models.ForeignKey(
        Role, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="access_requests", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    objects = UserManager()

    class Meta:
        db_table = "tbl_user"

    def save(self, *args, **kwargs):
        """`status` is the domain truth; `is_active` is what actually gates
        authentication (Django's ModelBackend and SimpleJWT both check it, and
        neither has heard of `status`). Deriving one from the other here means
        the two cannot drift, and — the reason it exists — a status added
        later cannot accidentally authenticate because someone forgot to set
        `is_active` alongside it. Callers set `status` and nothing else.

        Caveat for future work: a queryset-level `.update(status=...)` skips
        this and would leave the two out of step. Change status through an
        instance save."""
        self.is_active = self.status == self.ACTIVE
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if "status" in update_fields:
                kwargs["update_fields"] = update_fields | {"is_active"}
        super().save(*args, **kwargs)

    def is_last_active_administrator(self):
        """Is this the only administrator left who can still sign in?

        Nothing in the product can mint a replacement: approving an access
        request refuses the role, reactivating refuses administrators outright,
        and creating one needs an administrator already signed in. The seeded
        administrator is also the Django superuser, so /admin/ goes down with
        it (is_active follows status). Deactivating this account therefore
        leaves direct database access as the only way back into the system —
        which is why both doors that could do it, archive/ and a plain status
        edit, ask here first.
        """
        if not (self.role and self.role.role_name == Role.ADMINISTRATOR):
            return False
        return not (type(self).objects
                    .filter(role__role_name=Role.ADMINISTRATOR, status=self.ACTIVE)
                    .exclude(pk=self.pk).exists())

    @property
    def fullname(self):
        parts = [self.first_name, self.middle_initial, self.last_name]
        return " ".join(p for p in parts if p)

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    """The handful of things a person may say about themselves.

    Deliberately its own table rather than three more columns on User, and the
    reason is concrete: UserSerializer backs /api/users/, the directory every
    administrator opens. Anything added to User shows up there. Keeping this
    separate means profile data stays out of that screen by construction
    rather than by somebody remembering not to add it to a field list.

    Optional by definition — a row is created on first save, and an account
    without one is normal, not incomplete.

    No home address. The earlier prototype asked for one; nothing in the
    system reads a staff member's home address, and collecting personal data
    with no purpose is the thing RA 10173 asks agencies not to do. If a
    process ever needs it, add it then, with a retention rule.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    # Stored as the bare "host/path" the serializer normalises to, never as
    # raw input: people type @handles, full URLs and everything between.
    facebook = models.CharField(max_length=200, blank=True, default="")
    twitter = models.CharField(max_length=200, blank=True, default="")
    instagram = models.CharField(max_length=200, blank=True, default="")
    # The PRC license a social worker or psychologist practises under. The
    # Social Case Study Report's signature block prints both. Only the person
    # themselves writes them, and only a Staff or Psychologist account has one -
    # an administrator is IT support. A date in the past is allowed on purpose:
    # a lapsed license is a fact about the person, not an input error.
    license_number = models.CharField(max_length=50, blank=True, default="")
    license_valid_until = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_user_profile"

    def __str__(self):
        return f"Profile for {self.user.email}"


class AgencyProfile(models.Model):
    """Who the agency is, as printed on its reports: one row, pk=1.

    Agency-wide and set by an administrator in Settings. It exists because the
    Social Case Study Report's signature block needs the Head of Office's name
    and the printed headings need the office's name and address, and until now
    those were typed into each report by hand or not at all. The ISA is the
    agency's IT support and not the head of office, so the Head of Office is a
    name and a title held here, never an account.

    Blank is a valid state - an agency that has not filled it in prints lines
    to complete by hand, as the other printed forms do.
    """

    agency_name = models.CharField(max_length=200, blank=True, default="")
    office_address = models.TextField(max_length=500, blank=True, default="")
    contact_details = models.TextField(max_length=300, blank=True, default="")
    head_of_office_name = models.CharField(max_length=150, blank=True, default="")
    head_of_office_title = models.CharField(max_length=150, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_agency_profile"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.agency_name or "Agency profile"


class PhoneVerification(models.Model):
    """The code texted to a number and waiting to be typed back, and how
    often this account has asked for one. One row per account.

    In the database for the reason SessionReminder is: the default cache
    lives in one process's memory, and gunicorn with more than one worker
    gave each worker its own. A code stored by the worker that sent it was
    "expired" to the worker that took the reply, and each worker kept its
    own count against the resend limits - which multiplied them.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="phone_verification")
    number = models.CharField(max_length=16, blank=True, default="")
    # Blank means no code is outstanding.
    code = models.CharField(max_length=12, blank=True, default="")
    tries = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    window_started_at = models.DateTimeField(null=True, blank=True)
    sent_in_window = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "tbl_phone_verification"

    def __str__(self):
        return f"Phone verification for {self.user.email}"


class EmailVerification(models.Model):
    """The code mailed to a typed sign-up address and waiting to be typed
    back, and how often this request has asked for one. One row per access
    request (accounts/email_verification.py).

    In the database for the reason PhoneVerification is: the default cache
    lives in one process's memory, so under gunicorn a code stored by the
    worker that sent it was "expired" to the worker that took the reply, and
    each worker kept its own count of guesses.

    Tied to the request's User row rather than keyed by address alone, so it
    goes when the account does and no address outlives the request it was
    collected for. `email` is the address the code was mailed to: a code
    counts only while that is still the request's address, so a code mailed
    to a typo that an administrator has since corrected cannot vouch for the
    corrected one.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="email_verification")
    # Lower-cased, as accounts.email_verification.normalise writes it.
    email = models.EmailField(db_index=True)
    # Blank means no code is outstanding.
    code = models.CharField(max_length=12, blank=True, default="")
    tries = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    # The resend limits, counted here for the reason PhoneVerification counts
    # its own: a cache counter is per worker and multiplies the allowance.
    last_sent_at = models.DateTimeField(null=True, blank=True)
    window_started_at = models.DateTimeField(null=True, blank=True)
    sent_in_window = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "tbl_email_verification"

    def __str__(self):
        return f"Email verification for {self.user.email}"
