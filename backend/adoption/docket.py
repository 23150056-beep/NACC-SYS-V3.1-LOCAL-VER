"""The requirement checklist: submit, verify, waive.

The module is a checklist the office keeps for itself, so every casework role
works it end to end - staff tick, sign off and waive exactly as administrators
do. Gating verification on the administrator role only ever produced a stage a
staff member could fill in and then not finish.

One rule survives that, and it lives here rather than in a DRF permission
class because it is not a function of the caller's role at all:

* **Nobody verifies their own upload.** That depends on the ROW. Two people
  are still two people whether they are administrators or staff, and without
  this rule "verified" only means somebody clicked twice.

A waiver still has to say why. That is not a permission either - it is the
audit trail for proceeding without a statutory document, and it is worth the
same whoever grants it.

Views call these. They do not re-check the rules, and they must not, or the
rules will drift apart the first time a second screen wants to verify
something.
"""
from django.utils import timezone

from accounts.models import Role
from accounts.scoping import role_of_user
from adoption.models import Requirement


class NotPermitted(Exception):
    """The action is refused, and the message says why to the person's face."""


class RoleNotPermitted(NotPermitted):
    """Refused because of who is asking, rather than what state the row is in.

    The distinction is the HTTP status - 403 for this, 400 for everything else -
    and it used to be recovered by searching the message for the word
    "administrator". That worked until the word changed. A subclass cannot be
    broken by rewording a sentence.
    """


# The casework roles, and the same three tuples for all of it. Psychologists
# are absent from every one of them on purpose: section 6 gives them the
# timeline of children they assessed and nothing more.
#
# These stay as three separate names rather than one CASEWORK_ROLES constant
# because they are three different decisions. If a Supervisor role is ever
# added - the spec's "verify requirements, reassign owners, approve
# extensions" - it is these tuples that get narrowed again, and they will not
# all narrow the same way.
SUBMIT_ROLES = (Role.ADMINISTRATOR, Role.STAFF)
VERIFY_ROLES = (Role.ADMINISTRATOR, Role.STAFF)
WAIVE_ROLES = (Role.ADMINISTRATOR, Role.STAFF)


def _assert_open(requirement):
    if requirement.case.closed_at:
        raise NotPermitted("This case is closed.")
    if requirement.case.on_hold:
        raise NotPermitted(
            "The assessment has been reopened, so this case is on hold.")


def submit(requirement, actor, document=None, original_filename="", due_date=None):
    """Put a document (or an assertion) on the docket, pending verification."""
    _assert_open(requirement)
    if role_of_user(actor) not in SUBMIT_ROLES:
        raise RoleNotPermitted("Your role cannot add documents to an adoption docket.")

    requirement.state = Requirement.SUBMITTED
    requirement.submitted_by = actor
    requirement.submitted_at = timezone.now()
    if document is not None:
        requirement.document = document
        requirement.original_filename = original_filename
    if due_date is not None:
        requirement.due_date = due_date
    requirement.save()
    return requirement


def verify(requirement, actor):
    """Sign a submitted requirement off. Never your own."""
    _assert_open(requirement)
    if role_of_user(actor) not in VERIFY_ROLES:
        raise RoleNotPermitted("Your role cannot verify a docket requirement.")
    if requirement.state != Requirement.SUBMITTED:
        raise NotPermitted("There is nothing submitted here to verify yet.")
    if requirement.submitted_by_id == getattr(actor, "id", None):
        raise NotPermitted(
            "This is your own upload. Verification has to come from somebody else.")

    requirement.state = Requirement.VERIFIED
    requirement.verified_by = actor
    requirement.verified_at = timezone.now()
    requirement.save(update_fields=["state", "verified_by", "verified_at"])
    return requirement


def waive(requirement, actor, reason):
    """Proceed without a statutory document, on the record.

    No prior submission is needed - that is what a waiver is for: the document
    is never going to arrive.
    """
    _assert_open(requirement)
    if role_of_user(actor) not in WAIVE_ROLES:
        raise RoleNotPermitted("Your role cannot waive a requirement.")
    if not (reason or "").strip():
        raise NotPermitted("A waiver has to say why.")

    requirement.state = Requirement.WAIVED
    requirement.waiver_reason = reason.strip()
    requirement.verified_by = actor
    requirement.verified_at = timezone.now()
    requirement.save(update_fields=["state", "waiver_reason", "verified_by", "verified_at"])
    return requirement


def blockers_for_case(case):
    """The current stage's unmet exit conditions, for display.

    Re-exported from `pipeline` so a serializer showing blockers and the guard
    refusing to advance can never be reading two different lists.
    """
    from adoption import pipeline
    return pipeline.blockers_for(case)


def next_action(case):
    """The one thing to do next: the earliest-due outstanding requirement.

    Dated rows first, then docket order. This is what a board card shows under
    the child's name - a status chip says how it is going, this says what to do
    about it.
    """
    outstanding = [r for r in case.requirements.all()
                   if r.stage_id == case.current_stage_id
                   and r.state not in Requirement.SATISFIED]
    if not outstanding:
        return None
    outstanding.sort(key=lambda r: (r.due_date is None, r.due_date, r.position, r.id))
    return outstanding[0].label
