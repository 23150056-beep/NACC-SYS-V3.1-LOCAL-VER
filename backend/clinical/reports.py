"""Aggregation helpers for V2 reports. Sessions are completed pre-assessments;
clinical judgment lives in the psychologist's own result entries — the system
never computes scores."""
from accounts.display import display_name


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
