"""Custodians for demo children (29 Sep 2026).

The record form asks who the child lives with now - the Custodian - for the
case types in children/intake.CASE_TYPE_FIELDS, so a seeded or imported demo
record without one is a record the form would refuse to create. Seeded data
must satisfy the rules the endpoint enforces (CLAUDE.md, Demo data).

Called by `seed_demo_data` and `import_demo_data` only - both demo commands,
the first of which refuses a hosted database. Never by a migration: a
migration also runs against real records, and inventing somebody's custodian
there would be writing fiction into a case file.

No contact numbers. An invented mobile number is somebody's real handset, and
a demo someone clicks through could text it. Demo custodians therefore never
receive texts: no number, so no consent and no confirmed number either.
"""
from children import intake
from children.models import Child

# A grandmother is not called Ernesto: the first name follows the role.
_WOMEN = ["Rosa", "Lorna", "Mercedes", "Josefina", "Leticia", "Carmelita", "Nenita"]
_MEN = ["Arturo", "Rogelio", "Ernesto", "Virgilio", "Romeo"]
_SURNAMES = ["Dela Cruz", "Bautista", "Pascual", "Aquino", "Valdez", "Soriano",
             "Galang", "Ramirez", "Tolentino", "Villanueva"]
# Who a child in each track lives with, and which names fit. Kin share the
# child's surname.
_ROLE = {
    "Foster Care": [("foster parent", _WOMEN + _MEN)],
    "Kinship Care": [("maternal aunt", _WOMEN), ("grandmother", _WOMEN),
                     ("paternal uncle", _MEN), ("older sister", _WOMEN)],
    "Adoption": [("prospective adoptive parent", _WOMEN + _MEN)],
    "Family Tracing & Reunification": [("mother", _WOMEN), ("grandmother", _WOMEN),
                                       ("father", _MEN)],
}
_KIN = {"Kinship Care", "Family Tracing & Reunification"}


def custodian_for(child):
    """A plausible custodian for this demo child, the same every time."""
    roles = _ROLE.get(child.case_type)
    if not roles:
        return ""
    n = child.pk or 0
    role, names = roles[n % len(roles)]
    first = names[(n // len(roles)) % len(names)]
    surname = (child.last_name if child.case_type in _KIN and child.last_name
               else _SURNAMES[(n // 7) % len(_SURNAMES)])
    return f"{first} {surname} ({role})"


def fill_custodians(children):
    """Give each demo child whose case asks for a custodian, and has none,
    one. Returns how many were set."""
    todo = [c for c in children
            if "custodian_name" in intake.CASE_TYPE_FIELDS.get(c.case_type, [])
            and not (c.custodian_name or "").strip()]
    for child in todo:
        child.custodian_name = custodian_for(child)
    Child.objects.bulk_update(todo, ["custodian_name"])
    return len(todo)
