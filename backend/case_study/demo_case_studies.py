"""Invented case study drafts for demo adoption children (8 Oct 2026).

A third of the adoption children get a draft with block A partly filled, so the
tab, the psychologist's read-only view and the ISA's status card have something
to show. Everything is fictional and plain: no phone numbers, no e-mail
addresses, no surnames of any person. Block B (the adoptive parents) is left
empty on purpose, for the reason demo custodians have no numbers either - an
invented mobile is somebody's real handset (children/demo_custodians.py).

Seeded data must satisfy the rules the endpoint enforces (CLAUDE.md, Demo
data), so every value goes through the real `clean_value` before it is saved,
the Deed's dates go through the real cross-box check, and only boxes that
apply to the child are written. A draft the form would refuse to save is a
demo of something that cannot happen.

Called by `seed_demo_data` only, which refuses a hosted database. The hosted
demo gets the same rows through `export_demo_data` and `import_demo_data`.
Never by a migration: a migration also runs against real records.
"""
from datetime import timedelta

from django.utils import timezone

from case_study.models import CaseStudy, CaseStudySection
from case_study.sections import (
    ABANDONED, DVC_NOTARIZED, DVC_SIGNED, SCSR_SECTIONS, applies)
from case_study.validation import check_against_other_sections, clean_value, partner_of
from children import intake

# One draft for every THIRD adoption child, in record order.
EVERY = 3
# How many boxes each draft has filled, by turns: a start, a good way along and
# nearly there. Never all of block A, so each stays visibly a draft.
FILLED = (4, 7, 11)

_SOURCES = [
    ["The child", "House parent at the center", "Birth certificate and medical records"],
    ["The child", "Maternal grandmother", "Social worker's case notes", "Barangay certification"],
    ["The child", "Guardian of the child", "Existing documents: medical and school records"],
]
_CIRCUMSTANCES = [
    "The child was brought to the agency by a relative after the birth mother said she could "
    "no longer care for the child. The referral was made through the local social welfare "
    "office and the child was admitted the same week.",
    "The child was referred by the municipal social welfare office. The birth mother, who "
    "has other children and no steady income, asked that the child be placed for adoption. "
    "The child was admitted to the center on the day of the referral.",
    "A neighbor reported the child to the barangay, and the barangay referred the child to "
    "the agency. The child was received by the social worker and admitted that afternoon.",
]
_DESCRIPTION = [
    "On admission the child was quiet and watchful, with no physical deformities and a small "
    "birthmark on the left forearm. The child was clean, properly dressed and appeared well "
    "cared for. The child could walk, run and speak in short sentences.",
    "The child was about average in height and weight for age, active and curious. There "
    "were no deformities or birthmarks. The clothes were worn but clean, and the child's "
    "hair and nails were neat.",
    "The child was shy and held on to the escort at first, then warmed up within the hour. "
    "No deformities were seen. The child spoke clearly, could feed himself or herself and "
    "knew most colors.",
]
_MEDICAL = [
    "Born at term in a rural health unit, normal delivery. Newborn screening was done. "
    "Treated for cough and colds a few times and for one episode of diarrhea, with no "
    "hospital stay. No known allergies.",
    "Born at term in a district hospital. Birth weight was normal and the newborn screening "
    "result was normal. Had chickenpox at age four and recovered without complications.",
    "Born at home with a licensed midwife. Breastfed for the first year. Treated for "
    "pneumonia once at age two and recovered fully.",
]
_DEVELOPMENT = [
    "The child walked at about one year and spoke the first words soon after. At admission "
    "the child could stack blocks, scribble with a crayon and follow simple instructions. "
    "Toilet training had begun and the child could say when he or she needed to go.",
    "The child sat up, crawled and walked within the usual ages. At admission the child "
    "could dress with help and name familiar objects. The child is toilet trained during the "
    "day and enjoys singing and drawing.",
    "Milestones were reached at the usual ages. The child learned the center's routine "
    "quickly, plays well with other children and likes to help the house parent.",
]
_FAMILY_DESCRIPTION = [
    "The birth mother is of medium build with dark hair and eyes. She finished elementary "
    "school and worked as a farmhand with irregular pay. She is described as soft-spoken. "
    "There is no known history of substance abuse or illness in the family.",
    "The birth parents lived together for several years and were not married. The birth "
    "mother is described as hardworking but overwhelmed. No history of abuse, substance use "
    "or criminal record is known.",
]
_SUMMARY_SURRENDERED = (
    "The birth mother voluntarily committed the child to the agency after counseling. She "
    "understood that the Deed of Voluntary Commitment would become irrevocable and chose "
    "to proceed. The petition for the CDCLAA was filed afterwards.")
_SUMMARY_ABANDONED = (
    "The child was found alone and brought to the barangay, and then to the agency. The "
    "search for the birth family was carried out as described below, with no result. The "
    "petition for a declaration of abandonment was prepared.")
_SUMMARY_OTHER = (
    "The child remains in the agency's care while the petition for the CDCLAA is "
    "prepared. The circumstances of the child's admission are as described above.")
_ASSISTANCE = (
    "The social worker offered the birth mother counseling and referred her to the "
    "municipal office for livelihood support. The relatives were visited and none could "
    "take the child in. The birth mother decided to proceed with the surrender.")
_ABANDONMENT = (
    "A passer-by found the child near the market early in the morning and brought the "
    "child to the barangay. The child was awake, calm and in clean clothes. The barangay "
    "turned the child over to the agency the same day.")
_FAMILY_NAMES = ["Lourdes", "Efren", "Teodora", "Alfredo", "Marites", "Danilo"]

_VACCINES = ("BCG", "Hepatitis B", "Pentavalent, first dose", "Oral polio vaccine, first dose")


def _month_after(born, months, today):
    """A year-month, `months` after the birth, never later than this month."""
    index = born.year * 12 + born.month - 1 + months
    year, month = divmod(index, 12)
    return min((year, month + 1), (today.year, today.month))


def _immunizations(child, today):
    rows = []
    for step, vaccine in enumerate(_VACCINES):
        year, month = _month_after(child.birth_date, step * 2, today)
        rows.append({"vaccine": vaccine, "date": f"{year:04d}-{month:02d}",
                     "place": "Rural health unit"})
    return rows


def _family_composition(child, turn):
    mother, other = _FAMILY_NAMES[turn % 6], _FAMILY_NAMES[(turn + 3) % 6]
    return [
        {"name": mother, "relationship": "Birth mother", "age": "29", "sex": "Female",
         "civil_status": "Single", "education": "Elementary graduate",
         "employment_income": "Farmhand, irregular income"},
        {"name": other, "relationship": "Maternal grandmother", "age": "58", "sex": "Female",
         "civil_status": "Widowed", "education": "Elementary graduate",
         "employment_income": "Sells vegetables at the market"},
    ]


def _started_on(child, today):
    """The day the case began, to hang the Deed's and the counseling's dates on
    without ever putting one in the future or before the birth."""
    base = intake.intake_date(child) or timezone.localtime(child.created_at).date()
    return min(base, today)


def _summary(child):
    if child.case_category == "Surrendered":
        return _SUMMARY_SURRENDERED
    if child.case_category in ABANDONED:
        return _SUMMARY_ABANDONED
    return _SUMMARY_OTHER


def _boxes(child, today, turn):
    """The demo text for each box worth showing, in the catalogue's order. Only
    block A: the psychologist reads it, and block B stays empty."""
    start = _started_on(child, today)
    signed = start - timedelta(days=12)
    boxes = {
        "a2_sources": _SOURCES[turn % 3],
        "a2_circumstances": _CIRCUMSTANCES[turn % 3],
        "a3_description": _DESCRIPTION[turn % 3],
        "a3_medical": _MEDICAL[turn % 3],
        "a3_immunizations": _immunizations(child, today),
        "a3_development": _DEVELOPMENT[turn % 3],
        "a4_family_composition": _family_composition(child, turn),
        "a4_family_description": _FAMILY_DESCRIPTION[turn % 2],
        "a5_summary": _summary(child),
        DVC_SIGNED: signed.isoformat(),
        DVC_NOTARIZED: (signed + timedelta(days=2)).isoformat(),
        "a5_counselling": [
            {"date": (signed - timedelta(days=18)).isoformat(), "stage": "before",
             "goals": "Understand the decision and what it means for the child"},
            {"date": signed.isoformat(), "stage": "during",
             "goals": "Make sure the decision is the birth mother's own"},
            {"date": (signed + timedelta(days=30)).isoformat(), "stage": "after",
             "goals": "Process loss and grief; support at home"},
        ],
        "a5_assistance": _ASSISTANCE,
        "a5_aware_irrevocable": True,
        "a5_explained_vernacular": True,
        "a5_abandonment": _ABANDONMENT,
        "a5_search_efforts": [
            {"kind": "newspaper_publication", "date": (start - timedelta(days=20)).isoformat(),
             "note": "Published once a week for three weeks"},
            {"kind": "home_visit", "date": (start - timedelta(days=14)).isoformat(),
             "note": "No one at the last known address"},
        ],
    }
    # A child without known parents has no family to describe.
    if child.case_category == "Without Known Parents":
        boxes["a4_family_composition"] = None
        boxes["a4_family_description"] = None
    return boxes


def _future_dates_clamped(boxes, today):
    """Keep a Deed's dates out of the future for a case that began today."""
    for key in (DVC_SIGNED, DVC_NOTARIZED):
        if boxes[key] > today.isoformat():
            boxes[key] = today.isoformat()
    for row in boxes["a5_counselling"]:
        row["date"] = min(row["date"], today.isoformat())
    return boxes


def draft_for(child, turn, today):
    """{key: (value, not_applicable)} for one child's draft, every value
    already cleaned by the real rules."""
    boxes = _future_dates_clamped(_boxes(child, today, turn), today)
    wanted = [e for e in SCSR_SECTIONS
              if e["block"] == "A" and e["key"] in boxes and applies(e, child, None)]
    count = min(FILLED[turn % len(FILLED)], max(len(wanted) - 1, 1))
    out = {}
    for entry in wanted[:count]:
        raw = boxes[entry["key"]]
        if raw is None and entry["may_be_na"]:
            out[entry["key"]] = (None, True)
            continue
        cleaned = clean_value(entry, raw, today=today)
        partner = partner_of(entry["key"])
        if partner and partner in out:
            check_against_other_sections(entry, cleaned, out[partner][0])
        out[entry["key"]] = (cleaned, False)
    return out


def install_case_studies(children, today=None):
    """Give every third adoption child that has a social worker, and no case
    study yet, a draft. Returns how many were written.

    The same children get one every time (record order), and a child that has
    one is left alone, so running this twice adds nothing.
    """
    today = today or timezone.localdate()
    chosen = [c for c in children
              if c.case_type == "Adoption" and c.social_worker_id and c.birth_date]
    chosen = chosen[::EVERY]
    made = 0
    for turn, child in enumerate(chosen):
        if CaseStudy.objects.filter(child=child).exists():
            continue
        # One draft in three already has its date prepared; the others are
        # still waiting for it.
        prepared = today - timedelta(days=14) if turn % 3 == 2 else None
        if prepared is not None and prepared < child.birth_date:
            prepared = None
        study = CaseStudy.objects.create(
            child=child, created_by=child.social_worker, date_prepared=prepared)
        for key, (value, not_applicable) in draft_for(child, turn, today).items():
            CaseStudySection.objects.create(
                case_study=study, key=key, value=value, not_applicable=not_applicable,
                version=1, updated_by=child.social_worker)
        made += 1
    return made

