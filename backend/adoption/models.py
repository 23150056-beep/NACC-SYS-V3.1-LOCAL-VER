"""The adoption process module.

A second lifecycle attached to an existing child record. It begins only when
the psychologist has marked the assessment complete, and from there it tracks
eight statutory stages from endorsement to final decree.

Two rules shape everything in this file:

* **Stages and their targets are rows, not constants.** RACCO I's issuances
  change, and a corrected day target must not need a code deploy. The seeder
  installs the current values and then never overwrites them again.
* **Nothing derived is stored.** Status chips, days-in-stage, progress
  percentages and overdue flags are all computed on read (see `status.py`).
  A stored status string is a status that can disagree with the data, and on
  a compliance tracker that disagreement is the whole failure.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from children.models import Child


class AdoptionStage(models.Model):
    """One of the eight steps. Configuration — see the module docstring."""

    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=80)
    # Free text rather than a FK to Role: half of these owners are not accounts
    # in this system at all (the court, the national office), and the column
    # exists to tell a reader who they are waiting on.
    owner_role = models.CharField(max_length=60)
    # Null on the final stage: post-placement follow-through has no statutory
    # window, so it must never be able to read as overdue.
    target_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "tbl_adoption_stage"
        ordering = ["number"]

    def __str__(self):
        return f"{self.number:02d} {self.name}"


class RequirementTemplate(models.Model):
    """The exit conditions of a stage, which become a case's docket rows."""

    stage = models.ForeignKey(
        AdoptionStage, on_delete=models.CASCADE, related_name="requirement_templates")
    code = models.CharField(max_length=50)
    label = models.CharField(max_length=160)
    # Drives the blue "Waiting" status: this one is somebody else's to clear —
    # a court, the national office — so chasing the owner will not help.
    owned_externally = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "tbl_adoption_requirement_template"
        ordering = ["stage__number", "position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["stage", "code"],
                                    name="uniq_requirement_code_per_stage"),
        ]

    def __str__(self):
        return f"{self.stage.number:02d}/{self.code}"


class Handoff(models.Model):
    """The psychologist has finished; the record is back with staff.

    Written automatically the moment an assessment flips to Completed, and it
    stays on the banner until a human admits the child or routes them to
    another case plan. Automatic, so a finished assessment cannot be quietly
    forgotten; a row rather than a flag, so "who released this, and when" is
    still answerable months later.
    """

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="adoption_handoffs")
    pre_assessment = models.ForeignKey(
        "clinical.PreAssessment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="adoption_handoffs")
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="adoption_handoffs_released")
    released_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "tbl_adoption_handoff"
        ordering = ["-released_at", "-id"]


class AdoptionCase(models.Model):
    """One row per child in process. The spine of the module."""

    # Exits, per spec section 8. A pipeline that only models success gets
    # worked around within a month, so every one of these closes the case
    # cleanly and leaves the child record itself active.
    FINALIZED = "Adoption finalized"
    DISRUPTED = "Placement disruption"
    REUNIFIED = "Reunified with biological family"
    AGED_OUT = "Aged out"
    REFERRED_OUT = "Referred out of RACCO I"
    CLOSURE_CHOICES = [
        (FINALIZED, FINALIZED), (DISRUPTED, DISRUPTED), (REUNIFIED, REUNIFIED),
        (AGED_OUT, AGED_OUT), (REFERRED_OUT, REFERRED_OUT),
    ]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="adoption_cases")
    current_stage = models.ForeignKey(
        AdoptionStage, on_delete=models.PROTECT, related_name="cases")
    stage_entered_at = models.DateTimeField(default=timezone.now)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="adoption_cases_owned")
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_reason = models.CharField(max_length=60, blank=True, choices=CLOSURE_CHOICES)
    closure_note = models.TextField(blank=True)
    # Set when the psychologist reopens the assessment. The case freezes: the
    # stage stays, the clocks pause, and a banner says why. Deleting it would
    # throw away a docket somebody has been assembling for months.
    on_hold = models.BooleanField(default=False)
    pap = models.ForeignKey(
        "adoption.PAP", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cases")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_adoption_case"
        ordering = ["-opened_at", "-id"]

    def __str__(self):
        return f"{self.child.fullname} - stage {self.current_stage.number}"


class StageEvent(models.Model):
    """Append-only stage history. Never updated, never deleted.

    Every days-in-step figure and the whole child-view timeline read from here
    rather than from a column, which is what lets the timeline show the real
    date a stage was completed instead of the date somebody last touched the
    row.
    """

    ADVANCE = "advance"
    REVERT = "revert"
    DIRECTION_CHOICES = [(ADVANCE, "Advance"), (REVERT, "Revert")]

    case = models.ForeignKey(AdoptionCase, on_delete=models.CASCADE, related_name="events")
    from_stage = models.ForeignKey(
        AdoptionStage, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    to_stage = models.ForeignKey(AdoptionStage, on_delete=models.PROTECT, related_name="+")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="adoption_stage_events")
    occurred_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default=ADVANCE)

    class Meta:
        db_table = "tbl_adoption_stage_event"
        ordering = ["occurred_at", "id"]


def requirement_upload_path(instance, filename):
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "bin").lower()
    return f"adoption-docket/{uuid.uuid4().hex}.{ext}"


class Requirement(models.Model):
    """One line of the docket checklist, seeded from the stage templates."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    WAIVED = "waived"
    STATE_CHOICES = [
        (PENDING, "Pending"), (SUBMITTED, "Submitted"),
        (VERIFIED, "Verified"), (WAIVED, "Waived"),
    ]
    # What lets a stage advance. Submitted is deliberately not enough:
    # somebody other than the person who uploaded it has to have looked.
    SATISFIED = (VERIFIED, WAIVED)

    case = models.ForeignKey(AdoptionCase, on_delete=models.CASCADE, related_name="requirements")
    stage = models.ForeignKey(AdoptionStage, on_delete=models.PROTECT, related_name="+")
    code = models.CharField(max_length=50)
    label = models.CharField(max_length=160)
    owned_externally = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)
    state = models.CharField(max_length=12, choices=STATE_CHOICES, default=PENDING)
    document = models.FileField(upload_to=requirement_upload_path, null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    due_date = models.DateField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="adoption_requirements_submitted")
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="adoption_requirements_verified")
    verified_at = models.DateTimeField(null=True, blank=True)
    waiver_reason = models.TextField(blank=True)

    class Meta:
        db_table = "tbl_adoption_requirement"
        ordering = ["stage__number", "position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["case", "stage", "code"],
                                    name="uniq_requirement_per_case_stage"),
        ]

    @property
    def satisfied(self):
        return self.state in self.SATISFIED


class PAP(models.Model):
    """Prospective adoptive parents - the roster matching draws from."""

    ELIGIBLE = "eligible"
    MATCHED = "matched"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (ELIGIBLE, "Eligible"), (MATCHED, "Matched"),
        (WITHDRAWN, "Withdrawn"), (EXPIRED, "Expired"),
    ]

    family_name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True)
    region = models.CharField(max_length=60, blank=True)
    cea_number = models.CharField(max_length=60, blank=True)
    cea_expiry = models.DateField(null=True, blank=True)
    home_study_expiry = models.DateField(null=True, blank=True)
    children_in_household = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ELIGIBLE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_adoption_pap"
        ordering = ["family_name"]

    def __str__(self):
        return self.family_name


class ComplianceClock(models.Model):
    """A statutory window.

    Started from the issuance date of the authority it follows, never from the
    date somebody happened to type the record in - the spec is emphatic, and
    the difference is the difference between a real deadline and a fictional
    one. An extension re-scales the bar rather than resetting it, so the
    original overrun stays visible.
    """

    case = models.ForeignKey(AdoptionCase, on_delete=models.CASCADE, related_name="clocks")
    code = models.CharField(max_length=50)
    label = models.CharField(max_length=160)
    started_at = models.DateField()
    duration_days = models.PositiveSmallIntegerField()
    satisfied_at = models.DateField(null=True, blank=True)
    extension_days = models.PositiveSmallIntegerField(default=0)
    extension_document = models.FileField(
        upload_to=requirement_upload_path, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_adoption_clock"
        ordering = ["started_at", "id"]
