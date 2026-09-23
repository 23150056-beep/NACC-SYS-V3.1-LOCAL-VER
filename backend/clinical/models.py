import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from children.models import Child


def report_upload_path(instance, filename):
    """UUID filenames under media/reports/ — originals keep their extension."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "bin").lower()
    return f"reports/{uuid.uuid4().hex}.{ext}"


class InstrumentCatalog(models.Model):
    """Title-and-metadata-only record of a psychological assessment instrument.

    Copyright policy (V2 §2): instrument items, scales, scoring keys, and
    scans are NEVER stored. Titles and bibliographic facts only.
    """
    CATEGORY_CHOICES = [
        ("cognitive", "Cognitive"),
        ("behavioral", "Behavioral"),
        ("projective", "Projective"),
        ("personality", "Personality"),
        ("developmental", "Developmental"),
        ("achievement", "Achievement"),
        ("other", "Other"),
    ]
    AUDIENCE_CHOICES = [
        ("child", "For children"),
        ("adoptive_parent", "For prospective adoptive parents"),
        ("both", "Both"),
    ]

    title = models.CharField(max_length=200)
    publisher = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="other")
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default="child")
    age_range = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="catalog_instruments")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_instrument_catalog"
        ordering = ["title"]

    def __str__(self):
        return self.title


class AgencyFormTemplate(models.Model):
    """Agency-authored form definitions (v1's questionnaire builder repurposed).

    Restricted to agency/government forms; creating one requires an explicit
    attestation that it is not a published assessment instrument.
    """
    CONSENT = "consent"
    CLINICAL_INTERVIEW = "clinical_interview"
    PROBLEM_CHECKLIST = "problem_checklist"
    SELF_REPORT_GOV = "self_report_gov"
    TYPE_CHOICES = [
        (CONSENT, "Consent Form"),
        (CLINICAL_INTERVIEW, "Clinical Interview Form"),
        (PROBLEM_CHECKLIST, "Problem Checklist"),
        (SELF_REPORT_GOV, "Self-Report (Government Form)"),
    ]

    form_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    # Document prose shown before the fields (on screen and in print).
    # Lines starting with "## " render as section headings.
    body = models.TextField(blank=True, default="")
    # Versioned field definitions: [{"label": ..., "field_type": ..., "options": [...]}, ...]
    fields = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=1)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="form_templates")
    attestation = models.BooleanField(
        default=False,
        help_text="This form is agency-authored or an official government form, "
                  "not a published assessment instrument.")
    attested_at = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_agency_form_template"
        ordering = ["form_type", "title"]

    def __str__(self):
        return f"{self.get_form_type_display()}: {self.title}"


class ConsentRecord(models.Model):
    """A consent collected (on paper) before pre-assessment; optionally a scan
    of the signed agency-authored form is attached."""
    PENDING = "pending"
    SIGNED = "signed"
    DECLINED = "declined"
    STATUS_CHOICES = [(PENDING, "Pending"), (SIGNED, "Signed"), (DECLINED, "Declined")]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="consents")
    template = models.ForeignKey(
        AgencyFormTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consent_records")
    signer_name = models.CharField(max_length=150, blank=True)
    signer_relationship = models.CharField(max_length=100, blank=True)
    date = models.DateField(default=timezone.localdate)
    scan = models.FileField(upload_to="consents/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consents_recorded")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_consent_record"
        ordering = ["-date", "-id"]


class ClinicalInterviewRecord(models.Model):
    """Answers to the psychologist's own Clinical Interview form."""
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="clinical_interviews")
    template = models.ForeignKey(
        AgencyFormTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="interview_records")
    # Who answered this interview (e.g. "Custodian/PAP", "Child", "Guardian").
    respondent = models.CharField(max_length=100, blank=True, default="")
    answers = models.JSONField(default=dict, blank=True)
    interviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="interviews_conducted")
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_clinical_interview_record"
        ordering = ["-date", "-id"]


class PreAssessment(models.Model):
    """The guided pre-assessment flow: consent → clinical interview →
    instrument titles (administered on paper, offline) → problems → complete."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    STATUS_CHOICES = [(PENDING, "Pending"), (IN_PROGRESS, "In progress"), (COMPLETED, "Completed")]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="pre_assessments")
    psychologist = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pre_assessments")
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    instruments = models.ManyToManyField(
        InstrumentCatalog, blank=True, related_name="pre_assessments")
    consent = models.ForeignKey(
        ConsentRecord, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pre_assessments")
    interview = models.ForeignKey(
        ClinicalInterviewRecord, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="pre_assessments")
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_pre_assessment"
        ordering = ["-date", "-id"]


class PsychologicalReport(models.Model):
    """The psychologist's own uploaded report file (each keeps her own format).
    Text is extracted (PDF) for search and, later, AI summarization drafts."""
    TYPE_CHOICES = [
        ("initial", "Initial Evaluation"),
        ("progress", "Progress Report"),
        ("final", "Final Report"),
        ("other", "Other"),
    ]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="psych_reports")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="psych_reports")
    file = models.FileField(upload_to=report_upload_path)
    original_filename = models.CharField(max_length=255, blank=True)
    report_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="other")
    coverage = models.CharField(max_length=150, blank=True,
                                help_text="Session/date coverage, e.g. 'Sessions 1-3, Jan-Mar 2026'")
    extracted_text = models.TextField(blank=True)
    ai_summary = models.TextField(null=True, blank=True)
    ai_summary_confirmed = models.BooleanField(default=False)
    # clinical.report_check's findings when the file was filed - another
    # child's name, a case number or age that is not this child's. Kept on
    # the row so the flag is still there for whoever reads the report next,
    # until somebody who may edit the report marks it looked at.
    check_findings = models.JSONField(default=list, blank=True)
    check_reviewed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_psychological_report"
        ordering = ["-created_at"]


def case_referral_upload_path(instance, filename):
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "bin").lower()
    return f"case-referrals/{uuid.uuid4().hex}.{ext}"


class CaseReferral(models.Model):
    """The social worker's official case referral document for a child.
    Uploaded by staff/admin, viewable by the assigned psychologist —
    the other half of the split-view document area."""
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="case_referrals")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="case_referrals_uploaded")
    file = models.FileField(upload_to=case_referral_upload_path)
    original_filename = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255, blank=True)
    extracted_text = models.TextField(blank=True)
    ai_summary = models.TextField(null=True, blank=True)
    ai_summary_confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_case_referral"
        ordering = ["-created_at"]


class RemarkNote(models.Model):
    """Psychological remark notes, manually added (replaces v1 session notes)."""
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="remarks")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="remarks_written")
    date = models.DateField(default=timezone.localdate)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_remark_note"
        ordering = ["-date", "-id"]


class TreatmentPlan(models.Model):
    """The psychologist's free-input treatment plan (replaces v1 goal tracker)."""
    ACTIVE, COMPLETED, REVISED = "active", "completed", "revised"
    STATUS_CHOICES = [(ACTIVE, "Active"), (COMPLETED, "Completed"), (REVISED, "Revised")]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="treatment_plans")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="treatment_plans")
    objectives = models.TextField()
    interventions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    review_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_treatment_plan"
        ordering = ["-created_at"]


class ResultEntry(models.Model):
    """Manual result entry — the psychologist's own findings for an instrument
    administered on paper. No computed scores, ever."""
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="result_entries")
    pre_assessment = models.ForeignKey(
        PreAssessment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="result_entries")
    instrument = models.ForeignKey(
        InstrumentCatalog, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="result_entries")
    summary = models.TextField(help_text="Findings in the psychologist's own words")
    classification = models.CharField(max_length=150, blank=True,
                                      help_text="Free-text classification, the psychologist's own words")
    # Post-session baseline category (blueprint): a simple two-option verdict.
    NEEDS_COUNSELING = "Needs Counseling"
    GOOD_ASSESSMENT = "Good Assessment"
    BASELINE_CHOICES = [(NEEDS_COUNSELING, NEEDS_COUNSELING),
                        (GOOD_ASSESSMENT, GOOD_ASSESSMENT)]
    baseline_category = models.CharField(
        max_length=30, choices=BASELINE_CHOICES, blank=True)
    date = models.DateField(default=timezone.localdate)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="result_entries")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_result_entry"
        ordering = ["-date", "-id"]


def _invite_token():
    return uuid.uuid4().hex


class OpinionnaireInvite(models.Model):
    """A single-use, tokenized link (rendered as a QR code) that lets a child
    answer the agency's self-report opinionnaire on a secondary device.
    Templates are restricted to agency/government self-report forms —
    never published instruments (copyright policy §2)."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    EXPIRED = "expired"
    STATUS_CHOICES = [(PENDING, "Pending"), (SUBMITTED, "Submitted"), (EXPIRED, "Expired")]

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="opinionnaire_invites")
    template = models.ForeignKey(
        AgencyFormTemplate, on_delete=models.CASCADE, related_name="opinionnaire_invites")
    token = models.CharField(max_length=64, unique=True, default=_invite_token, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    answers = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="opinionnaire_invites_created")
    expires_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_opinionnaire_invite"
        ordering = ["-created_at"]

    @property
    def is_open(self):
        return self.status == self.PENDING and timezone.now() < self.expires_at


class ProblemEntry(models.Model):
    """A problem encountered in the child, logged by the psychologist."""
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="problems")
    description = models.TextField()
    category = models.CharField(max_length=100, blank=True)
    identified_on = models.DateField(default=timezone.localdate)
    resolved = models.BooleanField(default=False)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="problems_logged")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_problem_entry"
        ordering = ["resolved", "-identified_on", "-id"]


class SelfReportFlag(models.Model):
    """A child's own words, flagged as worth reading.

    It asserts exactly one thing: this child said something worth reading. It
    is never a claim that anyone missed anything, that a case is mishandled, or
    that the child is at risk — those are conclusions for a human who has read
    the record, and a system that matched a phrase has not read the record.

    The question and answer are snapshotted rather than followed by reference,
    so editing a form template later cannot rewrite what a child was asked.
    """
    LEXICON = "lexicon"
    MODEL = "model"
    SOURCE_CHOICES = [(LEXICON, "Phrase list"), (MODEL, "Local model")]

    invite = models.ForeignKey(
        OpinionnaireInvite, on_delete=models.CASCADE, related_name="flags")
    # Denormalised and indexed: every query here is "flags for these children".
    child = models.ForeignKey(
        Child, on_delete=models.CASCADE, related_name="self_report_flags")
    question = models.TextField()
    answer = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    matched = models.CharField(
        max_length=255, blank=True,
        help_text="The phrase that fired, or the model's one-line reason")
    created_at = models.DateTimeField(auto_now_add=True)

    # Acknowledgement. Without it flags accumulate, the list stops being read,
    # and the feature becomes decoration.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="self_report_flags_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        db_table = "tbl_self_report_flag"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["child", "reviewed_at"])]
        # One row per (invite, question, source), so re-scanning is idempotent
        # while still letting both detectors flag the same answer.
        constraints = [
            models.UniqueConstraint(
                fields=["invite", "question", "source"],
                name="uniq_self_report_flag_per_question_source"),
        ]

    @property
    def is_reviewed(self):
        return self.reviewed_at is not None
