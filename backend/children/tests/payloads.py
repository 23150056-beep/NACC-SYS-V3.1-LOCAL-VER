"""A new child record the Add Record form would let through (children/intake.py)."""


def complete(**over):
    """A Foster Care case, which asks for a previous custodian and the date of
    placement. Override any field; pass "" or None to leave one blank."""
    base = {
        "case_category": "Neglected", "case_type": "Foster Care",
        "first_name": "Mika", "middle_name": "Dela Cruz", "last_name": "Santos",
        "birth_date": "2016-01-10", "gender": "Female",
        "place_of_birth_or_found": "San Fernando, La Union",
        "birth_status": "Marital", "education_level": "Grade 4",
        "surrendered_by": "Social Worker",
        "date_of_placement_to_custodian": "2026-03-01",
        "house_number": "12", "street": "Rizal St.",
        "barangay": "Catbangen", "municipality": "San Fernando", "province": "La Union",
    }
    base.update(over)
    return base
