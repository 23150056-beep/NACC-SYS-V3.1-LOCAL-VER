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
        "health_condition": "Healthy", "current_placement": "With a foster family",
        "custodian_name": "Rosa Dela Cruz (foster parent)",
        "date_of_placement_to_custodian": "2026-03-01",
        "house_number": "12", "street": "Rizal St.",
        "barangay": "Catbangen", "municipality": "San Fernando", "province": "La Union",
    }
    base.update(over)
    return base


# Columns that carry a code, a phone number or a key, or that the code under
# test reads: the names are the tests' own sentinels, and the custodian and the
# referral reason are planted separately with words of their own.
_NOT_PLANTED = {"first_name", "last_name", "fullname", "custodian_name",
                "custodian_contact", "referral_reason", "intake_token",
                "psgc_province", "psgc_municipality", "psgc_barangay"}


def planted_text():
    """{column: token} for every free-text column on a child that describes
    them or says where they live (middle name, alias, street, landmark,
    barangay, place of birth, health and school notes, ...), each with a token
    of its own, cut to the column's width.

    The email privacy tests plant these and search everything that would leave
    the building for them. Listing the columns by hand is how a new one escapes
    the search: it is read off the model, so a column added later is searched
    from the day it exists. Choice columns are left alone - they hold a pick
    from a list, not what somebody typed - and `case_type` and `case_category`
    are searched by name."""
    from django.db import models

    from children.models import Child
    planted = {}
    for index, field in enumerate(Child._meta.concrete_fields):
        if (not isinstance(field, (models.CharField, models.TextField))
                or isinstance(field, models.ImageField)
                or field.choices or field.name in _NOT_PLANTED):
            continue
        token = f"zq{index:02d}{field.name.replace('_', '')}"
        planted[field.name] = token[:field.max_length] if field.max_length else token
    return planted
