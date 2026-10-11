"""The Social Case Study Report (SCSR) kept on a child's record.

The design is docs/superpowers/specs/2026-10-07-scsr-parts-2-5-design.md. This
is its own app and is called `case_study`, never `adoption`: an `adoption` app
was removed on 17 Sep 2026 and its rows are still in `django_migrations`, so a
new app of that name would be read as already migrated and its tables would
never be created (CLAUDE.md, "Removed: the adoption tracker and SAMD
readiness").

None of these models is registered in the Django admin, on purpose. The
seeded ISA is a superuser, and the ISA is the agency's IT support: the
design's owner decision is that IT support must not be able to read a social
worker's case study (adoptive parents' incomes included). A test holds that.
"""
from django.conf import settings
from django.db import models


class CaseStudy(models.Model):
    """One per child, of any case type. Block A is every child's; blocks B and C
    apply to an Adoption record only (case_study/sections.py `applies`), and
    changing the case type away from Adoption keeps their rows, hidden."""

    DRAFT, FINAL = "draft", "final"
    STATUS_CHOICES = [(DRAFT, "Draft"), (FINAL, "Final")]

    child = models.OneToOneField(
        "children.Child", on_delete=models.CASCADE, related_name="case_study")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    # Every age on the report is worked out as of this date, never as of today.
    date_prepared = models.DateField(null=True, blank=True)
    # Asked for a Domestic Relative adoption only: did the adopter have the
    # child in their custody for more than two years? A yes hides the
    # Placement History box. Null means not answered.
    custody_over_two_years = models.BooleanField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    # Moved on every section save, so "last edited" needs no query over the
    # sections.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_case_study"

    def __str__(self):
        return f"Case study for child {self.child_id} ({self.status})"


class CaseStudySection(models.Model):
    """One box of the report. `key` is a catalogue id (case_study/sections.py)
    and is never renamed: renaming a JSON key would leave every saved row
    under the old name, the same trap as renaming a column during a deploy.

    A section is saved on its own, with the version the writer last saw
    (the conditional update in views.py), because the report is written over
    weeks and two browser tabs are normal.
    """

    case_study = models.ForeignKey(
        CaseStudy, on_delete=models.CASCADE, related_name="sections")
    key = models.CharField(max_length=60)
    # The shape depends on the section's kind (case_study/validation.py).
    # Null before anything is saved. A section ticked Not applicable keeps
    # whatever it held: the tick hides it, and unticking brings it back.
    value = models.JSONField(null=True, blank=True)
    not_applicable = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tbl_case_study_section"
        constraints = [
            models.UniqueConstraint(
                fields=["case_study", "key"], name="one_section_per_key"),
        ]

    def __str__(self):
        return f"{self.key} v{self.version}"


class CaseStudyFinal(models.Model):
    """What was final on a given day, kept whole (used from phase P2).

    The snapshot holds the sections, Part I as the record held it, and the
    preparer and agency details of that day, so a reprint shows the report as
    it was signed even after a licence renewal or a later edit to the record.
    A row is never changed once written: finalizing again writes a new one.
    """

    case_study = models.ForeignKey(
        CaseStudy, on_delete=models.CASCADE, related_name="finals")
    snapshot = models.JSONField()
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    finalized_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_case_study_final"
        ordering = ["-finalized_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("A finalized case study is never changed; finalize again instead.")
        super().save(*args, **kwargs)
