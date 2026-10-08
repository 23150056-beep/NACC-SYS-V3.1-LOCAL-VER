"""Who may read or write a child's case study - the only place that says.

These rules do NOT live in the clinical records' base class, where
"Administrators see everything" would hand the agency's IT support every
social worker's case study, the adoptive parents' incomes included. The
owner's decisions of 8 Oct 2026 (docs/superpowers/specs/2026-10-07-scsr-parts-2-5-design.md):

* The record's social worker reads and writes all of it.
* The assigned psychologist reads block A only, drafts included, and never
  writes. A pending assignment request is not an assignment, and the
  previous psychologist after an accepted transfer is nobody.
* The ISA (Administrator) sees status only: that it exists, its state, who
  holds it and how much is left. Never text.
* Anyone else gets a 404, so the case study is not even known to exist.

The 404 comes first and from the one scoping rule every child endpoint uses
(accounts.scoping.visible_children): a child the caller cannot see is not
found, whatever they ask of it.
"""
from django.http import Http404

from accounts.models import Role
from accounts.scoping import role_of, visible_children
from case_study.sections import (
    BLOCK_A_KEYS, PSYCH_HIGHLIGHTS, SCSR_SECTIONS)
from children.models import Child

FULL, BLOCK_A, STATUS = "full", "block_a", "status"

ADOPTION = "Adoption"


class Access:
    """What one caller may do with one child's case study."""

    def __init__(self, child, level):
        self.child = child
        self.level = level

    @property
    def can_write(self):
        return self.level == FULL

    def readable_keys(self):
        """The catalogue keys this caller may read, in catalogue order."""
        if self.level == FULL:
            return [e["key"] for e in SCSR_SECTIONS]
        if self.level == BLOCK_A:
            keys = list(BLOCK_A_KEYS)
            # Their predecessor's findings reach this box through the social
            # worker's text, so it is withheld wherever the history is not
            # carried (Child.assignee_sees_history) - otherwise the case study
            # would be a way round that control.
            if not self.child.assignee_sees_history:
                keys.remove(PSYCH_HIGHLIGHTS)
            return keys
        return []

    def not_writable_reason(self):
        """Why this caller cannot write, as a sentence, or None. Not about the
        case itself being closed - see `writes_refused`."""
        if self.level == FULL:
            return None
        if self.level == BLOCK_A:
            return "Only the social worker who holds this record can change the case study."
        return "The ISA can see the status of a case study but not change it."


    def not_printable_reason(self):
        """Why this caller cannot open a final copy of the case study, as a
        sentence, or None. A final copy is the whole report, adoptive parents
        and placement included, so it is the holder's alone."""
        if self.level == FULL:
            return None
        if self.level == BLOCK_A:
            return "Only the social worker who holds this record can open a final copy of the case study."
        return "The ISA can see the status of a case study but not its text or its print."


def child_or_404(request, child_id):
    """The child, if this caller may see it at all; otherwise a 404."""
    child = (visible_children(request).select_related("social_worker", "assigned_psychologist")
             .filter(pk=child_id).first())
    if child is None:
        raise Http404
    return child


def access_for(request, child):
    """This caller's access to this child's case study, or a 404."""
    user = request.user
    role = role_of(request)
    if role == Role.STAFF and child.social_worker_id == user.pk:
        return Access(child, FULL)
    if role == Role.PSYCHOLOGIST and child.assigned_psychologist_id == user.pk:
        return Access(child, BLOCK_A)
    if role == Role.ADMINISTRATOR:
        return Access(child, STATUS)
    raise Http404


def writes_refused(child, case_study=None):
    """Why nothing can be written to this child's case study now, as a
    sentence, or None. The same sentences the API answers a write with and the
    screens show for a read-only case study."""
    if child.case_type != ADOPTION:
        return "A case study is kept for adoption records only."
    if child.status == Child.INACTIVE or child.case_status == Child.STAGE_TERMINATED:
        return "This case is closed, so its case study can no longer be changed."
    if case_study is not None and case_study.status == case_study.FINAL:
        return "This case study is final. Reopen it to change it."
    return None
