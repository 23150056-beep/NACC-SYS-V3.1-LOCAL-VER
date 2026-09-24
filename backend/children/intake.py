"""What a child's record has to say, and which questions apply to which case.

The Add Record form asks different things per case - a Type of Adoption on a
reunification case is a question nobody can answer - and since 24 Sep 2026
it refuses to save with any question that DOES apply left blank. Both halves
live here, so the serializer's refusal and the form's asterisks are computed
from the same map. frontend/src/config/caseData.js holds the browser's copy;
children/tests/test_intake.py pins the two together.
"""

ADMISSION = "date_of_admission"
PLACEMENT = "date_of_placement_to_custodian"

# Categories that describe how a child entered care. Reunification and
# independent living are about leaving it, so "Surrendered" and "Abandoned"
# are not offered there.
ALL_CATEGORIES = ["Surrendered", "Abandoned", "Dependent", "Neglected",
                  "Without Known Parents", "Orphaned"]
_LEAVING_CARE = ["Dependent", "Neglected", "Without Known Parents", "Orphaned"]
CATEGORY_OPTIONS = {
    "Adoption": ALL_CATEGORIES,
    "Foster Care": ALL_CATEGORIES,
    "Kinship Care": ALL_CATEGORIES,
    "Residential Care": ALL_CATEGORIES,
    "Family Tracing & Reunification": _LEAVING_CARE,
    "Independent Living": _LEAVING_CARE,
}

# The case-specific questions besides the date, per track.
CASE_TYPE_FIELDS = {
    "Adoption": ["surrendered_by", "type_of_adoption"],
    "Foster Care": ["surrendered_by"],
    "Kinship Care": ["surrendered_by"],
    "Family Tracing & Reunification": ["surrendered_by"],
    "Residential Care": [],
    "Independent Living": [],
}

# A child placed with a custodian is dated from the placement; a child the
# agency itself took in, from the admission. Within Adoption only a Regular
# adoption is an admission - every other type is a placement with a relative,
# step-parent or foster-adopter.
_PLACED = {"Foster Care", "Kinship Care", "Family Tracing & Reunification"}
_ADMITTED = {"Residential Care", "Independent Living"}


def date_field_for(case_type, type_of_adoption=""):
    """Which of the two dates this case records, or None while it cannot be
    known yet (an Adoption whose type has not been picked)."""
    if case_type == "Adoption":
        if not type_of_adoption:
            return None
        return ADMISSION if type_of_adoption == "Regular" else PLACEMENT
    if case_type in _PLACED:
        return PLACEMENT
    if case_type in _ADMITTED:
        return ADMISSION
    return None


def intake_date_field(child):
    """Which date a case started on: the one the case records, or - on a
    record from before the form asked for just one - whichever it holds."""
    field = date_field_for(child.case_type, child.type_of_adoption)
    if field and getattr(child, field):
        return field
    if child.date_of_admission:
        return ADMISSION
    if child.date_of_placement_to_custodian:
        return PLACEMENT
    return field or ADMISSION


def intake_date(child):
    return getattr(child, intake_date_field(child))


# Always asked. Middle name, legal status, landmark and date found are not
# here on purpose: a foundling or a non-marital child may have no middle name,
# a child newly in care may not have a legal status yet, and most addresses
# have no landmark. Asking for something that does not exist gets "N/A" typed
# into it, which is worse than a blank.
ALWAYS_REQUIRED = [
    "case_category", "case_type",
    "first_name", "last_name", "birth_date", "gender",
    "place_of_birth_or_found", "birth_status",
    "house_number", "street", "barangay", "municipality", "province",
]

# Answered by the case type, so asked again whenever the case type (or the
# type of adoption) changes.
DYNAMIC = {"surrendered_by", "type_of_adoption", ADMISSION, PLACEMENT}


def required_fields(case_type, type_of_adoption=""):
    fields = list(ALWAYS_REQUIRED) + list(CASE_TYPE_FIELDS.get(case_type, []))
    date = date_field_for(case_type, type_of_adoption)
    if date:
        fields.append(date)
    return fields
