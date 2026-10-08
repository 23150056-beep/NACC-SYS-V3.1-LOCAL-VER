"""Invented case studies for demo adoption children (8 Oct 2026).

A third of the adoption children get a draft with block A partly filled, so the
tab, the psychologist's read-only view and the ISA's status card have something
to show. A few others (up to three) get a complete case study that is FINAL, so
the print of a final, "Finals on file" and Reopen have something to work on
too. Everything is fictional and plain: no phone numbers,
no e-mail addresses. A draft leaves block B (the adoptive parents) empty; a
final fills it, but its adoptive parents' table has no contact rows, for the
reason demo custodians have no numbers either - an invented mobile is
somebody's real handset (children/demo_custodians.py).

A final is made by `case_study.finalize.finalize()`, the code the Final
endpoint runs, so a seeded final exists only if it passed `missing_sections()`.

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
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from case_study.finalize import finalize
from case_study.models import CaseStudy, CaseStudySection
from case_study.sections import (
    ABANDONED, DVC_NOTARIZED, DVC_SIGNED, SCSR_SECTIONS, applies)
from case_study.serializers import custody_pre_answer
from case_study.validation import check_against_other_sections, clean_value, partner_of
from children import intake

# One draft for every THIRD adoption child, in record order.
EVERY = 3
# And a final one for up to this many of the children in between (every third
# from the second), so a demo has both: drafts to write in, and finals to
# print, reopen and finalize again.
FINALS = 3
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


# --- A case study that is complete (for a final) ------------------------------------

_PSYCH_HIGHLIGHTS = (
    "The psychological evaluation found the child to be of average ability, friendly and "
    "able to follow instructions. The psychologist recommended regular play sessions and a "
    "steady routine, which the center carried out. The child has since become more "
    "settled and more willing to talk about feelings.")

# Invented, like the children's names (the seeder). The adoptive parents are
# named because the table cannot be complete without one; their contact rows
# are left out for good (PAP_CONTACT_ROWS).
_PAP_FEMALE = ["Corazon Tamayo", "Remedios Valdoz", "Estela Batoon"]
_PAP_MALE = ["Ernesto Tamayo", "Rodolfo Valdoz", "Artemio Batoon"]

_PROSE_B = {
    "b3_family_background": (
        "Both adopters grew up in two-parent families in the province, the eldest of "
        "several children. Discipline in their homes was firm but fair. There was no history "
        "of abuse, substance use or arrest in either family. They cope with stress by "
        "talking it over and by turning to their church."),
    "b4_motivation": (
        "The couple decided together to adopt after years of wanting a child to raise. They "
        "have talked through their feelings about not having a child of their own and "
        "understand that adoption is a lifelong commitment. The decision was reached over "
        "about a year."),
    "b5_child_care_plans": (
        "The adoptive mother will take leave from work after the child is placed and will "
        "care for the child at home. The child will go to the nearby public school. The "
        "extended family is ready to help, and a trusted aunt was named to care for the "
        "child if the adopters ever could not."),
    "b6_marital_history": (
        "The couple has been together for many years and describes the marriage as steady "
        "and respectful. They settle disagreements by talking calmly. Neither has been "
        "married before and there has been no annulment."),
    "b7_children_in_family": (
        "The couple has no other children in the home. They are close to their nieces and "
        "nephews, who visit often and are looking forward to meeting the child."),
    "b9_family_attitude": (
        "The plan to adopt was discussed with both families. The grandparents and the "
        "adopters' siblings welcomed it and said they will treat the child as one of their "
        "own."),
    "b10_parenting_experience": (
        "The adopters have helped to raise their nieces and nephews and have often cared "
        "for them for weeks at a time. They are patient, affectionate and use gentle "
        "discipline such as talking and short quiet time."),
    "b11_employment_finances": (
        "Both adopters have held steady work for many years and changed jobs only for "
        "better pay or to be nearer home. Their income covers their needs and they keep "
        "modest savings and health insurance. They have no large debts."),
    "b12_home_community": (
        "The family lives in a concrete house with its own room ready for the child. The "
        "neighborhood is quiet and safe. A school, a health center and a church are within "
        "walking distance, and the family takes part in the parish and the barangay."),
    "b13_identity": (
        "The family is Ilocano and Roman Catholic and speaks Ilocano, Filipino and English "
        "at home. They attend Mass weekly and plan to raise the child in the same faith "
        "while respecting the child's own background."),
    "b14_health_history": (
        "Both adopters are in good health. The medical reports on file show no serious "
        "illness or disability and no history of mental illness. The psychological "
        "evaluation found both emotionally mature and ready for parenting, and the "
        "psychologist recommended the placement."),
    "b15_references_clearances": (
        "Three character references, from a parish priest, a co-worker and a neighbor, "
        "spoke well of the couple. The barangay, police and court clearances show no "
        "record."),
    "b16_trainings": (
        "The adopters completed the agency's pre-adoption training and a forum on "
        "adoption and attachment. They learned about the child's grief, building trust and "
        "talking to a child about adoption."),
    "b17_adoption_telling": (
        "The adopters plan to tell the child early and in simple words, and to keep "
        "answering questions as the child grows. They are open to the child searching for "
        "the birth family when old enough and would help."),
}

_PROSE_C = {
    "c2_on_placement": (
        "When placed with the adopters the child was healthy, neat and a little shy. The "
        "child ate and slept well, played with the other children and was able to follow "
        "the daily routine. The child was learning at school at the usual pace."),
    "c3_stc_report": (
        "During the supervised trial custody the child and the family adjusted to each "
        "other well. The child took to the adoptive mother first and then to the adoptive "
        "father. Early nights were unsettled, which eased within a few weeks. No concerns "
        "were raised by the family or the school."),
    "c4_functioning": (
        "The child now relates warmly to the adoptive parents and has made friends in the "
        "neighborhood. The child can name feelings, calms down when talked to and responds "
        "to gentle discipline. The child enjoys drawing and singing, is doing well at "
        "school and shows a secure attachment to the family."),
    "c5_assessment": (
        "The placement is going well. The adopters provide a safe, loving home and meet the "
        "child's needs, and the child has grown in confidence and attachment since the "
        "placement. The family has become a closer, more settled household."),
    "c6_recommendation": (
        "It is recommended that the petition for adoption proceed and that the Order of "
        "Adoption be issued. The social worker sees no reason to deny it."),
}


def _pap_column(name, born, turn, female):
    """One adoptive parent's side of the table: names, dates and plain facts,
    and none of the contact rows."""
    return {
        "full_name": name,
        "date_of_birth": born,
        "place_of_birth": "Laoag City, Ilocos Norte",
        "religion": "Roman Catholic",
        "civil_status": "Married",
        "ethnicity_citizenship": "Ilocano / Filipino",
        "languages": "Ilocano, Filipino, English",
        "education": "College graduate" if turn % 2 == 0 else "Vocational course graduate",
        "occupation": "Public school teacher" if female else "Municipal engineering aide",
        "employer": "Local government unit" if not female else "Department of Education",
        "monthly_income": "Regular monthly salary",
        "other_income": "Small family farm" if not female else "None",
    }


def _household(turn):
    return [{"name": "Teodora", "age": "71", "relationship": "Mother of the adoptive mother",
             "education": "Elementary graduate", "occupation": "Retired",
             "disability": "High blood pressure, controlled"}]


def _placement_dates(child, today):
    """Matching, acceptance and entrustment in order, each between the child's
    birth and today, hung on the day the case began."""
    start = _started_on(child, today)
    entrusted = min(start + timedelta(days=45), today - timedelta(days=7))
    accepted = entrusted - timedelta(days=10)
    matching = accepted - timedelta(days=20)
    dates = sorted(min(max(day, child.birth_date), today)
                   for day in (matching, accepted, entrusted))
    return dict(zip(("matching_date", "accepted_date", "entrustment_date"),
                    (d.isoformat() for d in dates)))


def _measurements(child, today):
    age = max((today - child.birth_date).days // 365, 1)
    return {"height_cm": min(95 + round(age * 6.2), 175), "weight_kg": min(13 + age * 3, 70),
            "measured_on": (today - timedelta(days=5)).isoformat()}


def _complete_boxes(child, today, turn):
    """The demo text for every box, A, B and C. The caller keeps the ones that
    apply; a box with no text here (Other individuals living at the home) is
    ticked Not applicable, as the template allows."""
    boxes = _future_dates_clamped(_boxes(child, today, turn), today)
    boxes["a3_psych_highlights"] = _PSYCH_HIGHLIGHTS
    boxes["b1_paps"] = {
        "female": _pap_column(_PAP_FEMALE[turn % 3], "1984-05-17", turn, True),
        # One in three has a single adopter, which the table allows.
        "male": ({} if turn % 3 == 2 else
                 _pap_column(_PAP_MALE[turn % 3], "1982-11-03", turn, False)),
    }
    boxes["b2_household"] = _household(turn)
    boxes.update(_PROSE_B)
    boxes["c1_placement"] = {"racco_cpa": "RACCO 1", **_placement_dates(child, today)}
    boxes["c4_measurements"] = _measurements(child, today)
    boxes.update(_PROSE_C)
    return boxes


def complete_for(child, turn, today, prepared):
    """({key: (value, not_applicable)}, custody answer) for one child's COMPLETE
    case study: every box that applies, every value cleaned by the real rules.

    `prepared` is the date prepared, which the Domestic Relative custody
    question is answered against, as the screen's own pre-answer is."""
    custody = custody_pre_answer(child, prepared)
    if custody is None and child.type_of_adoption == "Domestic Relative":
        custody = False  # no placement date on the record: not more than two years
    answers = SimpleNamespace(custody_over_two_years=custody)
    boxes = _complete_boxes(child, today, turn)
    out = {}
    for entry in SCSR_SECTIONS:
        if not applies(entry, child, answers):
            continue
        raw = boxes.get(entry["key"])
        if raw is None and entry["may_be_na"]:
            out[entry["key"]] = (None, True)
            continue
        cleaned = clean_value(entry, raw, today=today)
        partner = partner_of(entry["key"])
        if partner and partner in out:
            check_against_other_sections(entry, cleaned, out[partner][0])
        out[entry["key"]] = (cleaned, False)
    return out, custody


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
    """Give some adoption children that have a social worker, and no case study
    yet, a case study. Returns how many were written.

    Every third one (from the first) gets a draft. Every third from the
    second - never one that has a draft, and at most FINALS of them - gets a
    complete case study that is made final. The same children get the same
    every time (record order), and a child that has one is left alone, so
    running this twice adds nothing.
    """
    today = today or timezone.localdate()
    eligible = [c for c in children
                if c.case_type == "Adoption" and c.social_worker_id and c.birth_date]
    made = 0
    for turn, child in enumerate(eligible[::EVERY]):
        if not CaseStudy.objects.filter(child=child).exists():
            _write_draft(child, turn, today)
            made += 1
    for turn, child in enumerate(eligible[1::EVERY][:FINALS]):
        if not CaseStudy.objects.filter(child=child).exists():
            _write_final(child, turn, today)
            made += 1
    return made


def _write_draft(child, turn, today):
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


def _write_final(child, turn, today):
    """A complete case study, made final the way the endpoint makes one. If any
    box were missing, `finalize` refuses and the seeder stops with the list."""
    prepared = max(today - timedelta(days=3), child.birth_date)
    boxes, custody = complete_for(child, turn, today, prepared)
    with transaction.atomic():
        study = CaseStudy.objects.create(
            child=child, created_by=child.social_worker, date_prepared=prepared,
            custody_over_two_years=custody)
        for key, (value, not_applicable) in boxes.items():
            CaseStudySection.objects.create(
                case_study=study, key=key, value=value, not_applicable=not_applicable,
                version=1, updated_by=child.social_worker)
        finalize(study, child.social_worker)
