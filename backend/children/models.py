from django.db import models
from django.utils import timezone


class Child(models.Model):
    ACTIVE = "active"
    INACTIVE = "inactive"
    # Back-compat alias: v1 called the non-active state "archived"; V2 renames it
    # to inactive (a terminated/archived case) without touching shared code paths.
    ARCHIVED = INACTIVE
    STATUS_CHOICES = [(ACTIVE, "Active"), (INACTIVE, "Inactive")]

    # Linear case tracker (blueprint milestones): the psychologist advances
    # pre_assessment -> counseling; terminate sets terminated (and inactive).
    STAGE_PRE_ASSESSMENT = "pre_assessment"
    STAGE_COUNSELING = "counseling"
    STAGE_TERMINATED = "terminated"
    CASE_STATUS_CHOICES = [
        (STAGE_PRE_ASSESSMENT, "Pre-Assessment"),
        (STAGE_COUNSELING, "Counseling"),
        (STAGE_TERMINATED, "Terminated"),
    ]

    # V2 case types per the psychologist interview ("active/adoption,
    # active/foster care"). This placement-track list is now corroborated by
    # NACC-SAMD-GF-000 KRA III (transition strategies: adoption, kinship/foster
    # care, family reunification, independent living); final wording still
    # pending RACCO I confirmation.
    CASE_TYPE_CHOICES = [
        ("Adoption", "Adoption"),
        ("Foster Care", "Foster Care"),
        ("Kinship Care", "Kinship Care"),
        ("Residential Care", "Residential Care"),
        ("Family Tracing & Reunification", "Family Tracing & Reunification"),
        ("Independent Living", "Independent Living"),
    ]

    # "Category" per the agency's official Identifying Information intake
    # form (2026-07 revision). Replaces the earlier, broader NACC-SAMD-GF-000
    # 18-item Service-Users list at the product owner's direction — existing
    # records holding one of the removed values (e.g. "Trafficked") keep that
    # stored value, it just won't appear as a pickable option anymore.
    CASE_CATEGORY_CHOICES = [
        ("Surrendered", "Surrendered"),
        ("Abandoned", "Abandoned"),
        ("Dependent", "Dependent"),
        ("Neglected", "Neglected"),
        ("Without Known Parents", "Without Known Parents"),
        ("Orphan", "Orphan"),
    ]

    # Who surrendered the child to NACC / RACCO I.
    SURRENDERED_BY_CHOICES = [
        ("Social Worker", "Social Worker"),
        ("Police", "Police"),
        ("Relatives", "Relatives"),
    ]

    # New fields below match the agency's official "I. Identifying
    # Information" intake form (2026-07).
    BIRTH_STATUS_CHOICES = [
        ("Marital", "Marital"),
        ("Non-Marital", "Non-Marital"),
        ("Child", "Child"),
        ("N/A", "N/A"),
    ]
    LEGAL_STATUS_CHOICES = [
        ("With Issued CDCLAA", "With Issued CDCLAA"),
        ("With IVC", "With IVC"),
        ("Judicially Declared Abandoned", "Judicially Declared Abandoned"),
    ]
    TYPE_OF_ADOPTION_CHOICES = [
        ("Regular", "Regular"),
        ("Domestic Relative", "Domestic Relative"),
        ("Step-parent", "Step-parent"),
        ("Adult", "Adult"),
        ("SIBRA", "SIBRA"),
        ("ICA Relative", "ICA Relative"),
        ("IP", "IP"),
        ("Foster-Adopt", "Foster-Adopt"),
    ]

    # The one link between a child and the psychologist responsible for
    # them. Every scoping rule, permission class and report reads it -
    # see accounts/scoping.py.
    assigned_psychologist = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assigned_children",
    )
    # Set at (re)assignment: does the current assignee see the child's prior assessments.
    assignee_sees_history = models.BooleanField(default=True)
    # Name parts (adviser): fullname stays as the composed display column so
    # every existing consumer keeps working.
    first_name = models.CharField(max_length=100, blank=True)
    middle_initial = models.CharField(max_length=5, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    fullname = models.CharField(max_length=150)
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    # Structured location pickers (Province / Municipality-City / Barangay).
    province = models.CharField(max_length=100, blank=True)
    municipality = models.CharField(max_length=100, blank=True)
    barangay = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=150, blank=True)

    # The PSGC codes behind the three names above. Kept alongside the text
    # rather than replacing it: a place can be renamed or merged upstream, and
    # what a case worker actually wrote down at intake is part of the record.
    # The names are what a person reads; the codes are what survives a rename
    # and what national-office reporting is keyed on. Blank where an address
    # predates the picker, or was only ever partially filled in.
    psgc_province = models.CharField(max_length=12, blank=True, db_index=True)
    psgc_municipality = models.CharField(max_length=12, blank=True, db_index=True)
    psgc_barangay = models.CharField(max_length=12, blank=True, db_index=True)
    case_type = models.CharField(max_length=150, blank=True, choices=CASE_TYPE_CHOICES)
    # Official agency "Identifying Information" intake form Category list.
    case_category = models.CharField(max_length=50, blank=True, choices=CASE_CATEGORY_CHOICES)
    surrendered_by = models.CharField(max_length=50, blank=True, choices=SURRENDERED_BY_CHOICES)
    # Remaining "I. Identifying Information" fields not already covered above.
    place_of_birth_or_found = models.CharField(max_length=150, blank=True)
    birth_status = models.CharField(max_length=20, blank=True, choices=BIRTH_STATUS_CHOICES)
    legal_status = models.CharField(max_length=50, blank=True, choices=LEGAL_STATUS_CHOICES)
    date_of_admission = models.DateField(null=True, blank=True)
    date_of_placement_to_custodian = models.DateField(null=True, blank=True)
    type_of_adoption = models.CharField(max_length=50, blank=True, choices=TYPE_OF_ADOPTION_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    case_status = models.CharField(
        max_length=20, choices=CASE_STATUS_CHOICES, default=STAGE_PRE_ASSESSMENT)
    # V2 profiling fields (exact list pending confirmation with the psychologist).
    photo = models.ImageField(upload_to="children/photos/", null=True, blank=True)
    referral_source = models.CharField(max_length=150, blank=True)
    referral_reason = models.TextField(blank=True)
    education_level = models.CharField(max_length=100, blank=True)
    current_placement = models.CharField(max_length=150, blank=True)
    medical_notes = models.TextField(blank=True)
    # Free-text recommendations + fields not part of the agency's intake
    # interview live under the "Recommendation" section in the UI.
    recommendation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_child"

    # Derived 5-state pre-assessment pipeline (product decision 2026-07-18).
    # Ordered: No Consent Yet → Not Yet Pre-Assessed → In Progress → Answered
    # → Completed. Never stored — always computed from consent/PA/case data.
    PA_NO_CONSENT = "No Consent Yet"
    PA_NOT_YET = "Not Yet Pre-Assessed"
    PA_IN_PROGRESS = "In Progress"
    PA_ANSWERED = "Answered"
    PA_COMPLETED = "Completed"

    def pre_assessment_status(self):
        """Mutually exclusive pipeline state. A completed pre-assessment
        outranks a newer in-progress re-assessment (the child HAS answered),
        and upgrades to Completed once the case tracker reaches counseling
        (assessment proper underway). Status strings are compared as literals
        so this app never imports clinical. Iterates .all() so callers'
        prefetch_related keeps list views at O(1) queries per child."""
        pas = self.pre_assessments.all()
        if any(p.status == "completed" for p in pas):
            return (self.PA_COMPLETED if self.case_status == self.STAGE_COUNSELING
                    else self.PA_ANSWERED)
        if pas:
            return self.PA_IN_PROGRESS
        if any(c.status == "signed" for c in self.consents.all()):
            return self.PA_NOT_YET
        return self.PA_NO_CONSENT

    def save(self, *args, **kwargs):
        if self.first_name or self.last_name:
            mi = f"{self.middle_initial.rstrip('.')}." if self.middle_initial else ""
            self.fullname = " ".join(p for p in (self.first_name, mi, self.last_name) if p)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.fullname


class TerminationRecord(models.Model):
    """Archive/termination of a case, always with a reason. Creating one sets
    the child to inactive. Reason categories pending RACCO I confirmation."""
    REASON_CHOICES = [
        ("Reunified with family", "Reunified with family"),
        ("Adoption finalized", "Adoption finalized"),
        ("Transferred to another agency", "Transferred to another agency"),
        ("Aged out of program", "Aged out of program"),
        ("Services completed", "Services completed"),
        ("Other", "Other"),
    ]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="terminations")
    terminated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="terminations_made")
    date = models.DateField(default=timezone.localdate)
    reason_category = models.CharField(max_length=50, choices=REASON_CHOICES)
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_termination_record"
        ordering = ["-created_at"]


# V2: v1's ProgressNote and Goal were replaced by clinical.RemarkNote and
# clinical.TreatmentPlan per the psychologist interview.
