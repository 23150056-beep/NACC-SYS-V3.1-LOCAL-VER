"""What is still missing from a case study.

One function answers it for every caller: the SW's list of what is left, the
ISA's count, and (from phase P2) the refusal to finalize. They must never
disagree, so none of them counts for itself.

"Missing" means an applicable box with neither content nor a Not applicable
tick. A few boxes have a stricter meaning of "content", written out below
from the design: the two Surrendered ticks must both be ticked, at least one
adoptive parent must be named, the entrustment date must be set where the
placement history applies, and the date prepared must be set.
"""
from case_study.sections import SCSR_SECTIONS, applies

DATE_PREPARED = "Date prepared"


def _has_content(entry, value):
    kind = entry["kind"]
    if not value:
        return False
    if kind == "prose":
        return bool(value.strip())
    if kind in ("list", "table"):
        return len(value) > 0
    if kind == "date":
        return bool(value)
    if kind == "tick":
        # Both of the Surrendered statements are required, so a tick that is
        # off is as good as an empty box.
        return value is True
    if kind == "pap_table":
        return any((value.get(side) or {}).get("full_name") for side in ("female", "male"))
    if kind == "measurements":
        return bool(value.get("height_cm")) and bool(value.get("weight_kg"))
    if kind == "placement":
        return bool(value.get("entrustment_date"))
    return False


def missing_sections(case_study):
    """The titles of what is missing, in the catalogue's order, with the date
    prepared first: it heads the report."""
    child = case_study.child
    stored = {s.key: s for s in case_study.sections.all()}
    missing = []
    if not case_study.date_prepared:
        missing.append(DATE_PREPARED)
    for entry in SCSR_SECTIONS:
        if not applies(entry, child, case_study):
            continue
        row = stored.get(entry["key"])
        if row is not None and row.not_applicable and entry["may_be_na"]:
            continue
        if not _has_content(entry, row.value if row is not None else None):
            missing.append(entry["title"])
    return missing
