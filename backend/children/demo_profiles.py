"""Health condition and current whereabouts for demo children (7 Oct 2026).

The record form asks both of every child (children/intake.ALWAYS_REQUIRED), so
a seeded or imported demo record without them is one the form would refuse to
create. Seeded data must satisfy the rules the endpoint enforces (CLAUDE.md,
Demo data).

Called by `seed_demo_data` and `import_demo_data` only, and never by a
migration: a migration also runs against real records, and writing a health
condition or a whereabouts into a real child's file would be inventing it.
Only blanks are filled; anything already recorded is left alone.
"""
from children.models import Child

# Where a child in each track is, in the words a case worker would use.
_WHEREABOUTS = {
    "Foster Care": "With a foster family",
    "Kinship Care": "With a relative",
    "Adoption": "With the prospective adoptive family",
    "Family Tracing & Reunification": "With family, under reunification",
    "Residential Care": "In the agency's residential facility",
    "Independent Living": "Living independently, with follow-up visits",
}
_SPECIAL_NEEDS = ["Asthma, needs an inhaler", "Mild hearing loss", "Poor eyesight, wears glasses",
                  "Speech delay, in therapy"]


def fill_profiles(children):
    """Give each demo child with no health condition, or no whereabouts, one.
    One child in six has special needs. Returns how many were changed."""
    changed = []
    for child in children:
        touched = False
        if not (child.health_condition or "").strip():
            n = child.pk or 0
            if n % 6 == 5:
                child.health_condition = "With special needs"
                child.special_needs = _SPECIAL_NEEDS[(n // 6) % len(_SPECIAL_NEEDS)]
            else:
                child.health_condition = "Healthy"
                child.special_needs = ""
            touched = True
        if not (child.current_placement or "").strip():
            child.current_placement = _WHEREABOUTS.get(child.case_type, "With the agency")
            touched = True
        if touched:
            changed.append(child)
    Child.objects.bulk_update(changed, ["health_condition", "special_needs", "current_placement"])
    return len(changed)
