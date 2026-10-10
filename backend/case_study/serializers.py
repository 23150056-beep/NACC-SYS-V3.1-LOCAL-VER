"""The shapes the case study API answers with.

Hand-built dicts rather than model serializers, because three callers get three
different answers from one set of rows and the difference IS the access rule:
the social worker gets everything, the psychologist block A, and the ISA a
status with no text anywhere in it. Each shape is built by one function here so
a field cannot slip into the wrong one.

The catalogue (titles, kinds, columns, hints) is not repeated in the answers:
the screen has its own copy (frontend/src/config/scsr.js), pinned to
case_study/sections.py by a test.
"""
from django.utils import timezone
from rest_framework import serializers

from accounts.display import display_name
from accounts.scoping import hide_earlier_history, scope_to_visible
from case_study.access import FULL, BLOCK_A, writes_refused
from case_study.completeness import missing_sections
from case_study.sections import (
    ABANDONED, ADOPTION, DOMESTIC_RELATIVE, SCSR_SECTIONS, applies)
from clinical.models import CaseReferral, PsychologicalReport
from clinical.reports import age_on

CDCLAA = "With Issued CDCLAA"

_datetime_field = serializers.DateTimeField()


def iso_datetime(value):
    """A timestamp the way the rest of the API writes one."""
    return _datetime_field.to_representation(value) if value else None


def iso_date(value):
    return value.isoformat() if value else None


def as_of(case_study):
    """The date every age on the report is worked out at: the date prepared,
    or today while that is not set yet."""
    if case_study is not None and case_study.date_prepared:
        return case_study.date_prepared
    return timezone.localdate()


def _age(born, on):
    age = age_on(born, on)
    return age if age is not None and age >= 0 else None


# --- Part I, as the record holds it ----------------------------------------------

def record_facts(child, on):
    """Part I (Identifying Information) read from the child's record, which is
    where it is kept; the case study never stores it. Field names are the
    child record's own, plus what the report derives from them. Nothing here is
    beyond what the child's page shows a psychologist."""
    abandoned = child.case_category in ABANDONED
    found = child.date_found
    return {
        "fullname": child.fullname,
        # Part I names the case type where an adoption names its type of
        # adoption, and leaves out rows only an adoption uses.
        "case_type": child.case_type,
        "alias": child.alias,
        "gender": child.gender,
        "birth_date": iso_date(child.birth_date),
        "age": _age(child.birth_date, on),
        "age_as_of": iso_date(on),
        "place_of_birth_or_found": child.place_of_birth_or_found,
        "birth_status": child.birth_status,
        "case_category": child.case_category,
        "legal_status": child.legal_status,
        "legal_status_date": iso_date(child.legal_status_date),
        "health_condition": child.health_condition,
        "special_needs": child.special_needs,
        "date_of_admission": iso_date(child.date_of_admission),
        "date_of_placement_to_custodian": iso_date(child.date_of_placement_to_custodian),
        "type_of_adoption": child.type_of_adoption,
        "current_placement": child.current_placement,
        # Shown beside the abandonment box and the CDCLAA summary.
        "date_found": iso_date(found),
        "place_found": child.place_of_birth_or_found if (found or abandoned) else None,
        "age_when_found": _age(child.birth_date, found) if found else None,
        "cdclaa_date": (iso_date(child.legal_status_date)
                        if child.legal_status == CDCLAA else None),
        "education_level": child.education_level,
    }


# --- Text offered to the social worker, never saved on its own ---------------------

def _long_date(value):
    return f"{value.day} {value:%B %Y}"


def seeds_for(request, child):
    """Starting text for three boxes, from what the record already holds. Each
    is a string or None. Offered, never saved: the screen shows a button and
    the social worker decides.

    The psychological summary is the latest report's, and only when somebody
    has confirmed it - the design's rule that nothing in this feature reads a
    draft summary. It is looked up through the same scoping as the report
    endpoints, so it is a report this reader may see.
    """
    parts = []
    if child.referral_source:
        parts.append(f"Referred by {child.referral_source}.")
    if (child.referral_reason or "").strip():
        parts.append(child.referral_reason.strip())
    referral = (scope_to_visible(CaseReferral.objects.filter(child=child), request)
                .order_by("-created_at", "-id").first())
    if referral is not None:
        parts.append("A case referral was filed on "
                     f"{_long_date(timezone.localdate(referral.created_at))}.")
    circumstances = "\n\n".join(parts) or None

    medical = []
    if (child.medical_notes or "").strip():
        medical.append(child.medical_notes.strip())
    if (child.special_needs or "").strip():
        medical.append(f"Special needs: {child.special_needs.strip()}.")

    reports = hide_earlier_history(
        scope_to_visible(PsychologicalReport.objects.filter(child=child), request),
        request, "author")
    latest = reports.order_by("-created_at", "-id").first()
    highlights = None
    if latest is not None and latest.ai_summary_confirmed and (latest.ai_summary or "").strip():
        highlights = latest.ai_summary.strip()

    return {
        "a2_circumstances": circumstances,
        "a3_medical": "\n\n".join(medical) or None,
        "a3_psych_highlights": highlights,
    }


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def custody_pre_answer(child, on):
    """For a Domestic Relative adoption: has the child been in the adopter's
    custody for two years or more by the date prepared? That is the answer the
    question starts at; the social worker can change it. None for every other
    type, which is not asked (nor of a record that is not an Adoption one,
    whatever adoption type it still carries), and None while the placement date
    is not on the record: unknown is not "no"."""
    if child.case_type != ADOPTION or child.type_of_adoption != DOMESTIC_RELATIVE:
        return None
    placed = child.date_of_placement_to_custodian
    if placed is None:
        return None
    return placed <= _years_before(on, 2)


# --- Sections ----------------------------------------------------------------------

def section_payload(entry, row, child, case_study, hide_kept_text=False):
    """One box as saved, or as it stands before anything is saved (version 0).

    A box ticked Not applicable keeps its text, hidden (the social worker can
    untick it to bring it back), so the social worker's own screen gets it.
    `hide_kept_text` is for readers who only see the report as it prints: the
    text is not part of it, and is not theirs to read."""
    value = row.value if row is not None else None
    if hide_kept_text and row is not None and row.not_applicable:
        value = None
    return {
        "key": entry["key"],
        "value": value,
        "not_applicable": bool(row and row.not_applicable),
        "version": row.version if row is not None else 0,
        "updated_by_name": (display_name(row.updated_by) or None) if row is not None else None,
        "updated_at": iso_datetime(row.updated_at) if row is not None else None,
        "applies": applies(entry, child, case_study),
    }


def conflict_current(row):
    """What is saved now, sent back with a 409 so the screen can offer it."""
    if row is None:
        return {"value": None, "not_applicable": False, "version": 0,
                "updated_by_name": None, "updated_at": None}
    return {
        "value": row.value, "not_applicable": row.not_applicable, "version": row.version,
        "updated_by_name": display_name(row.updated_by) or None,
        "updated_at": iso_datetime(row.updated_at),
    }


def _sections(case_study, child, keys, hide_kept_text=False):
    stored = {s.key: s for s in case_study.sections.all()}
    wanted = set(keys)
    return [section_payload(e, stored.get(e["key"]), child, case_study, hide_kept_text)
            for e in SCSR_SECTIONS if e["key"] in wanted]


# --- The three answers -------------------------------------------------------------

def finalized_by_name(final):
    """Who made a final, or None when the account has since been removed."""
    return (display_name(final.finalized_by) or None) if final.finalized_by_id else None


def finals_of(case_study):
    """The social worker's list of finals on file, newest first: when, and by
    whom. Never the snapshot, which is read one at a time to print."""
    if case_study is None:
        return []
    return [
        {"id": f.pk, "finalized_at": iso_datetime(f.finalized_at),
         "finalized_by_name": finalized_by_name(f)}
        for f in case_study.finals.select_related("finalized_by").defer("snapshot")
    ]


def last_finalized_at(case_study):
    """When the newest final was made, or None when there never was one."""
    if case_study is None:
        return None
    return iso_datetime(case_study.finals.values_list(
        "finalized_at", flat=True).order_by("-finalized_at", "-id").first())


def social_worker_payload(request, access, case_study):
    child = access.child
    on = as_of(case_study)
    refused = writes_refused(child, case_study)
    missing = missing_sections(case_study) if case_study else []
    body = {
        "exists": case_study is not None,
        "read_only": refused is not None,
        "read_only_reason": refused,
        "status": case_study.status if case_study else None,
        "date_prepared": iso_date(case_study.date_prepared) if case_study else None,
        "custody_over_two_years": case_study.custody_over_two_years if case_study else None,
        "custody_pre_answer": custody_pre_answer(child, on),
        "updated_at": iso_datetime(case_study.updated_at) if case_study else None,
        "sections": _sections(case_study, child, access.readable_keys()) if case_study else [],
        "missing": missing,
        # Draft, not refused for any other reason (closed) and nothing left to
        # complete: what the endpoint checks too.
        "can_finalize": bool(case_study and case_study.status == case_study.DRAFT
                             and refused is None and not missing),
        "finals": finals_of(case_study),
        "record_facts": record_facts(child, on),
        "seeds": seeds_for(request, child),
    }
    return body


def psychologist_payload(access, case_study):
    """Block A, read only. No seeds, no `missing` (it would name boxes of
    blocks B and C), and nothing about custody: that question only decides
    block C."""
    child = access.child
    on = as_of(case_study)
    return {
        "exists": case_study is not None,
        "read_only": True,
        "read_only_reason": access.not_writable_reason(),
        "status": case_study.status if case_study else None,
        "date_prepared": iso_date(case_study.date_prepared) if case_study else None,
        "updated_at": iso_datetime(case_study.updated_at) if case_study else None,
        # So the screen can say "Final since ..." for a final one. Just a time:
        # nothing of a final copy's content is the psychologist's to read.
        "last_finalized_at": last_finalized_at(case_study),
        "sections": (_sections(case_study, child, access.readable_keys(), hide_kept_text=True)
                     if case_study else []),
        "record_facts": record_facts(child, on),
    }


def status_payload(access, case_study):
    """The ISA's view. Counts and dates only - no section text, by design."""
    child = access.child
    holder = child.social_worker
    return {
        "exists": case_study is not None,
        "status": case_study.status if case_study else None,
        "holder_name": (display_name(holder) or None) if holder else None,
        "holder_active": bool(holder and holder.is_active),
        "updated_at": iso_datetime(case_study.updated_at) if case_study else None,
        "last_finalized_at": last_finalized_at(case_study),
        "missing_count": len(missing_sections(case_study)) if case_study else None,
    }


def payload_for(request, access, case_study):
    if access.level == FULL:
        return social_worker_payload(request, access, case_study)
    if access.level == BLOCK_A:
        return psychologist_payload(access, case_study)
    return status_payload(access, case_study)

