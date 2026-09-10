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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_user_profile"

    def __str__(self):
        return f"Profile for {self.user.email}"
