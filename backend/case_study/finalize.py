"""Making a case study final - the one place that does it.

The endpoint (POST .../final/) and the demo seeder both come through
`finalize()`, so a final can only exist if the case study passed
`missing_sections()` at that moment. A seeder that wrote CaseStudyFinal rows
by hand could produce a "final" the screen would have refused, which is the
kind of drift CLAUDE.md warns about under "Demo data".

What a final keeps (`build_snapshot`) is what the printed copy shows, whole:
Part I as the record held it, every section that applied, the preparer's name
and license as they were, and the agency's name, address and Head of Office.
Nothing in it is read again from the live record or the profiles when a final
is reprinted, so a license renewal, a changed Head of Office or an edit to the
record afterwards leaves a signed copy as it was signed.
"""
from django.db import transaction

from accounts.models import AgencyProfile, UserProfile
from case_study.access import writes_refused
from case_study.completeness import missing_sections
from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.sections import SCSR_SECTIONS, applies
from case_study.serializers import as_of, iso_date, record_facts

ALREADY_FINAL = "This case study is already final."
STALE = ("The case study was changed in another tab or by someone else since you "
         "opened it. Reload it before making it final.")
MISSING = "Complete these before making it final."


class CannotFinalize(Exception):
    """A refusal, as the sentence and the HTTP status the API answers it with.
    `missing` is the list of titles when something is left to complete."""

    def __init__(self, message, status, missing=None):
        # Spelled out in the exception's own text too, for a caller with no
        # screen (the seeder): "Complete these ..." alone would not say what.
        super().__init__(f"{message} {'; '.join(missing)}" if missing else message)
        self.message = message
        self.status = status
        self.missing = missing


def build_snapshot(case_study, user):
    """Everything a reprint needs, as plain JSON. Read at the moment of
    finalizing, and never again."""
    child = case_study.child
    stored = {s.key: s for s in CaseStudySection.objects.filter(case_study=case_study)}
    sections = {}
    for entry in SCSR_SECTIONS:
        # Only what applied then. A box that did not apply may still hold text
        # (the server keeps it), and it was not part of the report.
        if not applies(entry, child, case_study):
            continue
        row = stored.get(entry["key"])
        ticked = bool(row is not None and row.not_applicable and entry["may_be_na"])
        sections[entry["key"]] = {
            # A box ticked Not applicable prints "Not applicable." and nothing
            # else. Its kept text stays on the live case study, where
            # unticking brings it back; a final is never changed, so text
            # hidden from the printed copy is not copied into it.
            "value": None if (ticked or row is None) else row.value,
            "not_applicable": ticked,
        }

    profile = UserProfile.objects.filter(user=user).first()
    agency = AgencyProfile.load()
    return {
        "date_prepared": iso_date(case_study.date_prepared),
        "custody_over_two_years": case_study.custody_over_two_years,
        "child": {
            "id": child.pk, "fullname": child.fullname, "case_type": child.case_type,
            "case_category": child.case_category,
            "type_of_adoption": child.type_of_adoption,
        },
        "part_one": record_facts(child, as_of(case_study)),
        "sections": sections,
        "preparer": {
            "name": user.fullname,
            "license_number": (profile.license_number if profile else "") or "",
            "license_valid_until": iso_date(profile.license_valid_until) if profile else None,
        },
        "agency": {
            "agency_name": agency.agency_name,
            "office_address": agency.office_address,
            "contact_details": agency.contact_details,
            "head_of_office_name": agency.head_of_office_name,
            "head_of_office_title": agency.head_of_office_title,
        },
    }


def finalize(case_study, user, expected_updated_at=None):
    """Make the case study final and return the new CaseStudyFinal.

    `expected_updated_at` is the `updated_at` the writer last saw. Left out,
    the case study's own is used - for a caller that has just read it and has
    no screen to be out of date (the demo seeder).

    Raises CannotFinalize, and changes nothing, when the case cannot take it:
    not an Adoption record or closed, already final, changed since the writer
    looked, or something is still to complete.
    """
    child = case_study.child
    refused = writes_refused(child)
    if refused:
        raise CannotFinalize(refused, 400)
    if case_study.status == CaseStudy.FINAL:
        raise CannotFinalize(ALREADY_FINAL, 409)
    expected = expected_updated_at or case_study.updated_at
    if expected != case_study.updated_at:
        raise CannotFinalize(STALE, 409)
    missing = missing_sections(case_study)
    if missing:
        raise CannotFinalize(MISSING, 400, missing)

    with transaction.atomic():
        # Conditional on the state and the version the writer saw, so two
        # clicks, two tabs, or a save that slipped in since all lose to
        # whoever got here first - and what is copied below is exactly what
        # was checked above, because every save moves `updated_at`.
        moved = CaseStudy.objects.filter(
            pk=case_study.pk, status=CaseStudy.DRAFT, updated_at=expected,
        ).update(status=CaseStudy.FINAL)
        if not moved:
            raise CannotFinalize(STALE, 409)
        final = CaseStudyFinal.objects.create(
            case_study=case_study, snapshot=build_snapshot(case_study, user),
            finalized_by=user)
    case_study.status = CaseStudy.FINAL
    return final
