"""Aggregation helpers for V2 reports. Sessions are completed pre-assessments;
clinical judgment lives in the psychologist's own result entries — the system
never computes scores."""
from accounts.display import display_name


# NACC-SAMD-GF-000's "Service Users" age groups, in the form's own order.
# Shared by the Agency Summary and the assistant's statistics: two copies of
# this rule would eventually put the same child in two different bands.
AGE_BANDS = [
    ("Infants and Young Children (0-6)", 0, 6),
    ("Middle Childhood (7-11)", 7, 11),
    ("Adolescents (12-17)", 12, 17),
    ("Young Adults (18+)", 18, None),
]
UNSPECIFIED_AGE = "Unspecified age"


def age_on(birth_date, today):
    """Whole years on `today`, or None when there is no birth date."""
    if not birth_date:
        return None
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day))


def age_band(birth_date, today):
    """The AGE_BANDS label a birth date falls in, or UNSPECIFIED_AGE.

    A missing birth date and one that gives no band (a date in the future)
    both land in UNSPECIFIED_AGE, which is where the census always put them.
    """
    age = age_on(birth_date, today)
    if age is not None:
        for label, lo, hi in AGE_BANDS:
            if age >= lo and (hi is None or age <= hi):
                return label
    return UNSPECIFIED_AGE


def bucket(d, rng):
    if rng == "yearly":
        return str(d.year)
    if rng == "quarterly":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if rng == "weekly":
        return d.strftime("%Y-W%U")
    return d.strftime("%Y-%m")  # monthly (default)


def summary(pre_assessments, rng="monthly"):
    """Aggregates over completed PreAssessment rows (with child + psychologist
    select_related)."""
    total = len(pre_assessments)
    by_case_type, per_psy, trend = {}, {}, {}

    for p in pre_assessments:
        ct = p.child.case_type or "—"
        by_case_type[ct] = by_case_type.get(ct, 0) + 1
        name = display_name(p.psychologist, "—")
        slot = per_psy.setdefault(name, {"name": name, "count": 0})
        slot["count"] += 1
        b = bucket(p.date, rng)
        trend[b] = trend.get(b, 0) + 1

    return {
        "total": total,
        "children": len({p.child_id for p in pre_assessments}),
        "by_case_type": by_case_type,
        "per_psychologist": sorted(per_psy.values(), key=lambda x: -x["count"]),
        "trend": [{"bucket": k, "count": trend[k]} for k in sorted(trend)],
    }
