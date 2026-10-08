"""The case study API (/api/case-studies/).

Every rule about who may do what is in case_study/access.py, every rule about
what a box may hold is in case_study/validation.py, and what is missing is
case_study/completeness.py. This file only wires a request to them.

Refusals are `{"detail": "<one sentence>"}`, the shape the rest of the API
uses, so the screens show them as they are.
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from activity.models import ActivityLog
from activity.services import log_activity
from case_study import serializers as shapes
from case_study.access import access_for, child_or_404, writes_refused
from case_study.completeness import missing_sections
from case_study.models import CaseStudy, CaseStudySection
from case_study.sections import DOMESTIC_RELATIVE, applies, entry_for
from case_study.validation import (
    check_against_other_sections, clean_date_prepared, clean_value, partner_of)

ALLOWED_PATCH = {"date_prepared", "custody_over_two_years"}


def _refuse(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": message}, status=code)


def _case_study_of(child):
    """The child's case study with its sections, or None."""
    case_study = (CaseStudy.objects.filter(child=child)
                  .prefetch_related(Prefetch(
                      "sections", queryset=CaseStudySection.objects.select_related("updated_by")))
                  .first())
    if case_study is not None:
        case_study.child = child
    return case_study


class CaseStudyView(APIView):
    """GET what this caller may read, POST to start one, PATCH its header."""

    permission_classes = [IsAuthenticated]

    def get(self, request, child_id):
        child = child_or_404(request, child_id)
        access = access_for(request, child)
        return Response(shapes.payload_for(request, access, _case_study_of(child)))

    def post(self, request, child_id):
        child = child_or_404(request, child_id)
        access = access_for(request, child)
        if not access.can_write:
            return _refuse(access.not_writable_reason(), status.HTTP_403_FORBIDDEN)
        refused = writes_refused(child)
        if refused:
            return _refuse(refused)
        exists = {"detail": "This child already has a case study."}
        if CaseStudy.objects.filter(child=child).exists():
            return Response(exists, status=status.HTTP_409_CONFLICT)
        try:
            with transaction.atomic():
                CaseStudy.objects.create(child=child, created_by=request.user)
        except IntegrityError:
            # Two tabs pressed Start together; the second one lost.
            return Response(exists, status=status.HTTP_409_CONFLICT)
        # The event names the child and says a case study was started. Never
        # any of its text: the activity feed is read by more people than the
        # case study is.
        log_activity(request.user, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="CaseStudy", entity_label=child.fullname,
                     entity_id=child.pk)
        case_study = _case_study_of(child)
        return Response(shapes.payload_for(request, access, case_study),
                        status=status.HTTP_201_CREATED)

    def patch(self, request, child_id):
        child = child_or_404(request, child_id)
        access = access_for(request, child)
        if not access.can_write:
            return _refuse(access.not_writable_reason(), status.HTTP_403_FORBIDDEN)
        case_study = _case_study_of(child)
        if case_study is None:
            return _refuse("This child has no case study yet. Start one first.",
                           status.HTTP_404_NOT_FOUND)
        refused = writes_refused(child, case_study)
        if refused:
            return _refuse(refused)
        data = request.data
        if not isinstance(data, dict) or set(data) - ALLOWED_PATCH:
            return _refuse("Only the date prepared and the custody answer can be changed here.")
        fields = []
        try:
            if "date_prepared" in data:
                case_study.date_prepared = clean_date_prepared(
                    data["date_prepared"], child.birth_date)
                fields.append("date_prepared")
            if "custody_over_two_years" in data:
                answer = data["custody_over_two_years"]
                if answer is not None and not isinstance(answer, bool):
                    return _refuse("The custody answer must be yes or no.")
                if answer is not None and child.type_of_adoption != DOMESTIC_RELATIVE:
                    return _refuse("The custody question is asked for a Domestic "
                                   "Relative adoption only.")
                case_study.custody_over_two_years = answer
                fields.append("custody_over_two_years")
        except ValidationError as exc:
            return _refuse(exc.messages[0])
        if fields:
            case_study.save(update_fields=fields + ["updated_at"])
        return Response(shapes.payload_for(request, access, _case_study_of(child)))


def _expected_version(raw):
    """The version the writer last saw: absent or 0 for a box never saved."""
    if raw is None or raw == "":
        return 0
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValidationError("The version must be a whole number.")
    text = str(raw).strip()
    # Nine digits is a billion saves. The limit also keeps a pasted wall of
    # digits from reaching int(), which refuses very long ones.
    if not text.isdigit() or len(text) > 9:
        raise ValidationError("The version must be a whole number.")
    return int(text)


def _stored_row(case_study, key):
    # A function of its own so a test can stand in for "nobody had saved this
    # yet when I looked" and exercise the race on a first save.
    return (CaseStudySection.objects.select_related("updated_by")
            .filter(case_study=case_study, key=key).first())


class SectionView(APIView):
    """PUT one box. Each box saves on its own, with the version the writer
    last saw: the report is written over weeks and two tabs are normal, so a
    save that does not know what it replaces would silently overwrite."""

    permission_classes = [IsAuthenticated]

    def put(self, request, child_id, key):
        child = child_or_404(request, child_id)
        access = access_for(request, child)
        if not access.can_write:
            return _refuse(access.not_writable_reason(), status.HTTP_403_FORBIDDEN)
        case_study = _case_study_of(child)
        if case_study is None:
            return _refuse("This child has no case study yet. Start one first.",
                           status.HTTP_404_NOT_FOUND)
        refused = writes_refused(child, case_study)
        if refused:
            return _refuse(refused)
        entry = entry_for(key)
        if entry is None:
            return _refuse("There is no such section in the case study.")
        if not applies(entry, child, case_study):
            return _refuse("That section does not apply to this child.")

        data = request.data if isinstance(request.data, dict) else {}
        try:
            expected = _expected_version(data.get("expected_version"))
            not_applicable = data.get("not_applicable", False)
            if not isinstance(not_applicable, bool):
                raise ValidationError("Not applicable must be ticked or not.")
            if not_applicable and not entry["may_be_na"]:
                raise ValidationError("This section cannot be marked Not applicable.")
            # Not applicable hides what is written; it never deletes it, as
            # changing an answer anywhere in the system hides what it no longer
            # asks. A value sent with the tick is cleaned and stored as usual;
            # with none sent, the box keeps what it already holds, so unticking
            # brings the text back. A request that sends no `value` at all (an
            # untick that only says so) leaves the text alone too; sending
            # `value: null` with the tick off still empties the box.
            keep = "value" not in data or (not_applicable and data["value"] is None)
            cleaned = None if keep else clean_value(entry, data.get("value"))
            partner = partner_of(key)
            if partner and cleaned is not None:
                other = case_study.sections.filter(key=partner).first()
                check_against_other_sections(entry, cleaned, other.value if other else None)
        except ValidationError as exc:
            return _refuse(exc.messages[0])

        row = _stored_row(case_study, key)
        now = timezone.now()
        with transaction.atomic():
            if expected == 0:
                saved = row is None and self._create(
                    case_study, key, cleaned, not_applicable, request.user)
            else:
                # Left out of the update when the tick is the only news, so the
                # stored text is not touched at all.
                stored = {} if keep else {"value": cleaned}
                saved = row is not None and CaseStudySection.objects.filter(
                    pk=row.pk, version=expected).update(
                        not_applicable=not_applicable,
                        version=F("version") + 1, updated_by=request.user,
                        updated_at=now, **stored) == 1
            if saved:
                CaseStudy.objects.filter(pk=case_study.pk).update(updated_at=now)
        if not saved:
            return Response(
                {"detail": "This section was saved from another tab or by someone else "
                           "since you opened it.",
                 "current": shapes.conflict_current(_stored_row(case_study, key))},
                status=status.HTTP_409_CONFLICT)

        case_study = _case_study_of(child)
        row = _stored_row(case_study, key)
        body = shapes.section_payload(entry, row, child, case_study)
        body["missing"] = missing_sections(case_study)
        return Response(body)

    @staticmethod
    def _create(case_study, key, cleaned, not_applicable, user):
        """The first save of a box. If another save got there first, the unique
        constraint refuses this one - the same answer as a stale version."""
        try:
            with transaction.atomic():
                CaseStudySection.objects.create(
                    case_study=case_study, key=key, value=cleaned,
                    not_applicable=not_applicable, version=1, updated_by=user)
        except IntegrityError:
            return False
        return True
