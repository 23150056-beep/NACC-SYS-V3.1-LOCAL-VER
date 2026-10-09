"""One child, twice (found 9 Oct 2026).

A social worker pressed Save Record twice. Two records came out - same child,
same birth date - each with its own request to the psychologist, who then had
two "Waiting for your answer" rows for one child. There was no way to delete
either: children are never deleted through the API.

Three guards live here, one for each way it can happen:

1. THE SAME SUBMISSION ARRIVING TWICE. Add Record makes a token when it opens
   and sends it with every attempt to save. A token that already has a record
   is answered "already saved" rather than adding the child again. This is
   the server's half of the button guard in Children.jsx, and it covers what
   a button cannot: a response that never came back, then a retry. Two
   requests that arrive together both find nothing, and the unique column lets
   one in; the other is turned into the same answer.

2. THE SAME CHILD, TYPED AGAIN. A new record is refused when one already
   exists - any social worker's, active or closed - with the same first name,
   last name and birth date. A name alone is not enough: two children can
   share a common one. The sentence follows what the duplicate check already
   discloses to this requester (children/views.py check_duplicate).

3. A DUPLICATE THAT GOT IN ANYWAY. The ISA can remove one, but only a record
   holding nothing but what Add Record makes; see `remove`.
"""
import logging
import re
import unicodedata

from django.db import transaction
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response

from accounts.display import display_name
from accounts.scoping import scope_to_visible
from activity.models import ActivityLog
from activity.services import log_activity
from children.models import AssignmentRequest, Child
from scheduling.visibility import case_ref

logger = logging.getLogger(__name__)

# --- 1. The same submission ----------------------------------------------

# crypto.randomUUID() is 36 characters; anything made some other way still has
# to look like a token before it is looked up.
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ALREADY_SAVED = "This record was already saved."


def clean_token(raw):
    """The token as stored, or None for blank. Raises ValueError for text that
    is not shaped like one."""
    text = str(raw).strip() if raw else ""
    if not text:
        return None
    if not TOKEN_SHAPE.match(text):
        raise ValueError("That is not a valid submission token.")
    return text


def token_in(data):
    """The token a request carries, or None. A malformed one is left for the
    serializer to refuse; it is never looked up."""
    try:
        return clean_token(data.get("intake_token")) if hasattr(data, "get") else None
    except ValueError:
        return None


def already_saved(request, token):
    """The 409 for a token that already has a record, or None when it has not.

    The id comes back only when the requester may see that record
    (accounts/scoping.py): the token is something the browser invented, but
    the answer must not hand an id to somebody the record is not for."""
    existing = Child.objects.filter(intake_token=token).values_list("pk", flat=True).first()
    if existing is None:
        return None
    body = {"detail": ALREADY_SAVED}
    if scope_to_visible(Child.objects.filter(pk=existing), request, path=None).exists():
        body["id"] = existing
    return Response(body, status=status.HTTP_409_CONFLICT)


# --- 2. The same child ----------------------------------------------------


def _key(text):
    """A name as compared: trimmed, one space between words, case folded.
    Done here rather than by the database because SQLite folds only ASCII,
    which would treat PEÑA and Peña as two children."""
    return " ".join(unicodedata.normalize("NFC", str(text or "")).casefold().split())


def same_child(first, last, birth, exclude=None):
    """Every record for the child with this first name, last name AND birth
    date. Any of the three blank matches nothing: a last name alone is half a
    barangay."""
    if not (_key(first) and _key(last) and birth):
        return []
    wanted = (_key(first), _key(last))
    qs = Child.objects.filter(birth_date=birth).select_related("social_worker")
    if exclude is not None:
        qs = qs.exclude(pk=exclude)
    return [c for c in qs.order_by("-updated_at")
            if (_key(c.first_name), _key(c.last_name)) == wanted]


def refusal_for_second_record(request, first, last, birth):
    """The sentence that refuses a new record for a child who already has one,
    or None. It says as much as check_duplicate does and no more:

    - the requester's own active record: its case reference;
    - another social worker's active record: who holds it, no id;
    - a closed record, anybody's: that it exists and can be reopened.
    """
    found = same_child(first, last, birth)
    if not found:
        return None
    mine = set(scope_to_visible(Child.objects.filter(pk__in=[c.pk for c in found]),
                                request, path=None).values_list("pk", flat=True))
    active = [c for c in found if c.status == Child.ACTIVE]
    if not active:
        return ("This child has a closed record. Reopen it from the warning above "
                "instead of adding a new one.")
    own = next((c for c in active if c.pk in mine), None)
    if own is not None:
        return (f"This child already has a record ({case_ref(own.pk)}). "
                "Open it instead of adding a second one.")
    holder = display_name(active[0].social_worker)
    if holder:
        return (f"A record for this child is already held by {holder}. "
                "Ask the ISA (Administrator) to transfer it to you.")
    return ("A record for this child already exists and is not with a social "
            "worker yet. Ask the ISA (Administrator) to assign it to you.")


def save_new(serializer, **extra):
    """serializer.save() in a transaction of its own, so a refusal by the
    database (the token already used) leaves the caller's transaction usable
    and the caller free to look the record up and answer."""
    with transaction.atomic():
        return serializer.save(**extra)


# --- 3. Removing a duplicate ----------------------------------------------

# Children are never deleted through the API - every foreign key to one is
# CASCADE, so one DELETE would take the sessions, notes, consents and closing
# records with it. A duplicate made by mistake is the one exception, and it is
# allowed only while it holds nothing but what Add Record itself makes: the
# row, its case referral, and the question put to a psychologist.
#
# Every model that points at a child is named in one of the two tables below,
# and a test fails when a new one is not (test_remove_duplicate.py). An
# unnamed one blocks removal rather than going with the record, so a model
# added later is safe until somebody decides otherwise.

# Removed with the record. A request still waiting, or withdrawn, is only the
# question; one that was ANSWERED means a psychologist acted on the record,
# and it stays (see `_answered`).
TAKEN_ALONG = {
    "children.AssignmentRequest",
    "clinical.CaseReferral",
}

# Keeps the record, with the words the refusal uses for it.
KEEPS_IT = {
    "scheduling.Appointment": ("appointment", "appointments"),
    "children.TerminationRecord": ("closing record", "closing records"),
    "clinical.ConsentRecord": ("consent record", "consent records"),
    "clinical.ClinicalInterviewRecord": ("clinical interview", "clinical interviews"),
    "clinical.PreAssessment": ("pre-assessment", "pre-assessments"),
    "clinical.PsychologicalReport": ("report", "reports"),
    "clinical.RemarkNote": ("remark", "remarks"),
    "clinical.TreatmentPlan": ("treatment plan", "treatment plans"),
    "clinical.ResultEntry": ("result entry", "result entries"),
    "clinical.OpinionnaireInvite": ("survey invite", "survey invites"),
    "clinical.ProblemEntry": ("problem entry", "problem entries"),
    "clinical.SelfReportFlag": ("self-report flag", "self-report flags"),
    "case_study.CaseStudy": ("case study", "case studies"),
    # SET_NULL, not CASCADE: removing the child would not delete these, it
    # would detach them, and the access log would stop saying whose record
    # the assistant had read.
    "assistant.AssistantJob": ("assistant log entry", "assistant log entries"),
    "children.AssignmentRequest": ("answered assignment request",
                                   "answered assignment requests"),
}


class Refused(Exception):
    """A removal the server will not do; the message is the sentence shown."""


def _answered(rows):
    """The requests a psychologist has acted on. Pending ones are the question
    still open, withdrawn ones are already nothing."""
    return rows.exclude(status__in=[AssignmentRequest.PENDING, AssignmentRequest.WITHDRAWN])


def what_keeps_it(child):
    """What this record holds beyond what Add Record makes, as phrases for a
    sentence ("2 appointments"). Empty means it can be removed."""
    found = []
    order = list(KEEPS_IT)
    for rel in sorted(Child._meta.related_objects,
                      key=lambda r: order.index(r.related_model._meta.label)
                      if r.related_model._meta.label in order else len(order)):
        model = rel.related_model
        label = model._meta.label
        rows = model._default_manager.filter(**{rel.field.name: child})
        if label == "children.AssignmentRequest":
            rows = _answered(rows)
        elif label in TAKEN_ALONG:
            continue
        n = rows.count()
        if not n:
            continue
        one, many = KEEPS_IT.get(label, (model._meta.verbose_name,
                                         model._meta.verbose_name_plural))
        found.append(f"{n} {one if n == 1 else many}")
    if child.assigned_psychologist_id:
        found.append("a psychologist assigned")
    return found


def _and(parts):
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _typed_case_number(text, pk):
    """Whether what was typed is this record's case number: C-0050, c-0050 or
    C-50. Anything else is not, including a different number."""
    match = re.fullmatch(r"C-?0*(\d+)", str(text or "").strip(), flags=re.IGNORECASE)
    return bool(match) and int(match.group(1)) == pk


def remove(duplicate_pk, original_pk, case_reference, *, by):
    """Delete the record `duplicate_pk`, made by mistake for the child
    `original_pk` already has. Raises Refused with the reason, and Http404 when
    the record to remove does not exist. Nothing is changed unless all of it
    can be.

    Only the ISA calls this (ChildViewSet.remove_duplicate). The two records
    must match on first name, last name and birth date; the one removed must
    hold nothing but what Add Record makes; and its own case number has to be
    typed, as the confirmation. Its case referral files go with it, and so
    does its open request, with a notice to the psychologist asked and a line
    in the audit trail."""
    if not str(duplicate_pk).isdigit():
        raise Http404
    try:
        original_pk = int(original_pk)
    except (TypeError, ValueError):
        raise Refused("Choose which record this one duplicates.")
    if original_pk == int(duplicate_pk):
        raise Refused("A record cannot be a duplicate of itself.")
    files = []
    with transaction.atomic():
        # Locked, so a session booked or a note written while this checks is
        # not deleted unseen. No select_related here: PostgreSQL will not lock
        # the nullable side of a join, and the social worker is one query away.
        duplicate = Child.objects.select_for_update().filter(pk=duplicate_pk).first()
        if duplicate is None:
            raise Http404
        original = Child.objects.filter(pk=original_pk).first()
        if original is None:
            raise Refused("The record it is said to duplicate was not found.")
        dup_ref, orig_ref = case_ref(duplicate.pk), case_ref(original.pk)
        if duplicate.pk not in {c.pk for c in same_child(
                original.first_name, original.last_name, original.birth_date)}:
            raise Refused(f"{dup_ref} and {orig_ref} are not for the same child: the first "
                          "name, last name and date of birth must all match.")
        kept = what_keeps_it(duplicate)
        if kept:
            raise Refused(f"{dup_ref} cannot be removed: it already has {_and(kept)}, and only "
                          "a record holding nothing but what Add Record makes can be removed.")
        if not _typed_case_number(case_reference, duplicate.pk):
            raise Refused(f"Type this record's case number, {dup_ref}, to confirm.")

        asked = [r.psychologist for r in AssignmentRequest.objects
                 .filter(child=duplicate, status=AssignmentRequest.PENDING)
                 .select_related("psychologist")]
        files = [f for f in [r.file for r in duplicate.case_referrals.all()] + [duplicate.photo]
                 if f]
        files = [(f.storage, f.name) for f in files]
        holder = duplicate.social_worker
        duplicate.delete()

        # The audit trail: the ISA removed it, and the social worker who held
        # it is told where it went. The record is gone, so the event names it
        # by its case reference alone and points at nothing.
        log_activity(by, ActivityLog.REMOVED, ActivityLog.RECORD,
                     entity_type="Child", entity_label=f"{dup_ref} (duplicate of {orig_ref})",
                     recipient=holder if holder is not None and holder.pk != by.pk else None)
        # The psychologist whose question disappeared. No child's name, like
        # every other notice about a request (children/assignment.py).
        for psychologist in asked:
            log_activity(by, ActivityLog.WITHDRAWN, ActivityLog.RECORD,
                         entity_type="Assignment",
                         entity_label=f"record {dup_ref}, which was a duplicate",
                         recipient=psychologist)
        transaction.on_commit(lambda: _delete_files(files))
    return {"removed": dup_ref, "duplicate_of": orig_ref}


def _delete_files(files):
    """The stored files of a record that is gone. After the commit, so a
    removal that rolled back never loses a file; a file that will not delete
    is logged and left, since the record is already gone and nothing points
    at it."""
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:  # noqa: BLE001 - storage backends raise their own
            logger.exception("Could not delete %s after removing a duplicate record", name)
