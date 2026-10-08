"""What a saved section may hold, one rule set per kind of box.

`clean_value(entry, value)` returns the cleaned value to store, or raises
Django's ValidationError with ONE plain sentence: the screen shows it as it is
(the calendar's `firstError` rule - a refusal reads as a sentence, never as
`{"end_time": [...]}`). The server checks every save, whatever the browser
did.

None always means "nothing entered" and cleans to the blank of its kind, so a
section can be emptied. Dates are never in the future; the one place a future
date is natural, a licence validity, is not in this report.
"""
import re
from datetime import date

from django.core.exceptions import ValidationError
from django.utils import timezone

from case_study.sections import (
    DVC_NOTARIZED, DVC_SIGNED, PAP_ROWS, PAP_SIDES)

PROSE_MAX = 20000
LIST_MAX = 50
CELL_MAX = 500
ROWS_MAX = 50
AGE_RANGE = (0, 150)
HEIGHT_CM = (30, 250)
WEIGHT_KG = (2, 250)
# A year typed as 0202 is a slip, not a vaccination in the year 202.
EARLIEST_YEAR = 1900

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PARTIAL = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _fail(message):
    raise ValidationError(message)


def _today(today):
    return today or timezone.localdate()


def _text(value, label, limit=CELL_MAX):
    """A stripped string, or '' for nothing. Anything else is a slip."""
    if value is None:
        return ""
    if not isinstance(value, str):
        _fail(f"{label} must be text.")
    # PostgreSQL's JSON type cannot hold a NUL character, so one pasted in
    # from somewhere would be a server error on the hosted database only.
    value = value.replace("\x00", "").strip()
    if len(value) > limit:
        _fail(f"{label} is too long. Keep it under {limit:,} characters.")
    return value


def _iso_date(value, label, today):
    """A date as 'YYYY-MM-DD', not in the future. Returns (text, date)."""
    value = _text(value, label, 10)
    if not value:
        return "", None
    if not _ISO.match(value):
        _fail(f"{label} must be a date written year-month-day, such as 2026-03-14.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail(f"{label} is not a real date.")
    if parsed.year < EARLIEST_YEAR:
        _fail(f"{label} is too long ago to be right.")
    if parsed > _today(today):
        _fail(f"{label} cannot be in the future.")
    return value, parsed


def _partial_date(value, label, today):
    """'2019', '2019-05' or '2019-05-14', not in the future."""
    value = _text(value, label, 10)
    if not value:
        return ""
    if not _PARTIAL.match(value):
        _fail(f"{label} must be a year, a year and month, or a full date, "
              "such as 2019, 2019-05 or 2019-05-14.")
    parts = [int(p) for p in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    if year < EARLIEST_YEAR:
        _fail(f"{label} is too long ago to be right.")
    if not 1 <= month <= 12:
        _fail(f"{label} has a month that does not exist.")
    if len(parts) == 3:
        try:
            date(year, month, parts[2])
        except ValueError:
            _fail(f"{label} is not a real date.")
    now = _today(today)
    # A year alone is in the future only once the whole year is, and a month
    # alone likewise: compare as far as the answer goes.
    if len(parts) == 3:
        future = date(year, month, parts[2]) > now
    elif len(parts) == 2:
        future = (year, month) > (now.year, now.month)
    else:
        future = year > now.year
    if future:
        _fail(f"{label} cannot be in the future.")
    return value


def _whole_number(value, label, low, high):
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        _fail(f"{label} must be a whole number.")
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,4}", text):
        _fail(f"{label} must be a whole number.")
    number = int(text)
    if not low <= number <= high:
        _fail(f"{label} must be between {low} and {high}.")
    return str(number)


def _measure(value, label, low, high):
    """A height or weight: a number within range, kept to one decimal."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        _fail(f"{label} must be a number.")
    try:
        number = float(str(value).strip())
    except ValueError:
        _fail(f"{label} must be a number.")
    if number != number or number in (float("inf"), float("-inf")):
        _fail(f"{label} must be a number.")
    if not low <= number <= high:
        _fail(f"{label} must be between {low} and {high}.")
    number = round(number, 1)
    return int(number) if number == int(number) else number


# --- one cleaner per kind -------------------------------------------------------

def _clean_prose(entry, value, today):
    return _text(value, entry["title"], PROSE_MAX)


def _clean_list(entry, value, today):
    if value is None:
        return []
    if not isinstance(value, list):
        _fail(f"{entry['title']} must be a list of lines.")
    lines = [_text(item, "A line in " + entry["title"]) for item in value]
    lines = [line for line in lines if line]
    if len(lines) > LIST_MAX:
        _fail(f"{entry['title']} can hold at most {LIST_MAX} lines.")
    return lines


def _clean_cell(column, value, today):
    label = column["label"]
    kind = column["type"]
    if kind == "partial_date":
        return _partial_date(value, label, today)
    if kind == "date":
        return _iso_date(value, label, today)[0]
    if kind == "int":
        return _whole_number(value, label, *AGE_RANGE)
    text = _text(value, label)
    if kind == "choice" and text and text not in {o["value"] for o in column["options"]}:
        _fail(f"{label} is not one of the choices offered.")
    return text


def _clean_table(entry, value, today):
    if value is None:
        return []
    if not isinstance(value, list):
        _fail(f"{entry['title']} must be a list of rows.")
    columns = entry["columns"]
    wanted = {c["key"] for c in columns}
    rows = []
    for row in value:
        if not isinstance(row, dict):
            _fail(f"Each row of {entry['title']} must be a set of answers.")
        if set(row) != wanted:
            _fail(f"A row of {entry['title']} must have exactly these answers: "
                  + ", ".join(c["key"] for c in columns) + ".")
        cleaned = {c["key"]: _clean_cell(c, row[c["key"]], today) for c in columns}
        if any(cleaned.values()):
            rows.append(cleaned)
    if len(rows) > ROWS_MAX:
        _fail(f"{entry['title']} can hold at most {ROWS_MAX} rows.")
    return rows


def _clean_pap_table(entry, value, today):
    blank = {side: {} for side in PAP_SIDES}
    if value is None:
        return blank
    if not isinstance(value, dict) or set(value) - set(PAP_SIDES):
        _fail("The adoptive parents' table has only a female and a male column.")
    row_ids = [r["id"] for r in PAP_ROWS]
    cleaned = {}
    for side in PAP_SIDES:
        column = value.get(side)
        if column is None:
            column = {}
        if not isinstance(column, dict):
            _fail("Each column of the adoptive parents' table must be a set of answers.")
        unknown = set(column) - set(row_ids)
        if unknown:
            _fail("The adoptive parents' table has a row that does not exist.")
        mine = {}
        for row in PAP_ROWS:
            raw = column.get(row["id"])
            if row["id"] == "date_of_birth":
                text = _iso_date(raw, f"The {side} adopter's date of birth", today)[0]
            else:
                text = _text(raw, row["label"])
            if text:
                mine[row["id"]] = text
        cleaned[side] = mine
    return cleaned


def _clean_date(entry, value, today):
    return _iso_date(value, entry["title"], today)[0] or None


def _clean_tick(entry, value, today):
    if value is None:
        return False
    if not isinstance(value, bool):
        _fail(f"{entry['title']} must be ticked or not.")
    return value


def _clean_measurements(entry, value, today):
    if value is None:
        return {}
    allowed = {"height_cm", "weight_kg", "measured_on"}
    if not isinstance(value, dict) or set(value) - allowed:
        _fail("Height, weight and the date measured are all that is kept here.")
    cleaned = {}
    height = _measure(value.get("height_cm"), "Height", *HEIGHT_CM)
    if height is not None:
        cleaned["height_cm"] = height
    weight = _measure(value.get("weight_kg"), "Weight", *WEIGHT_KG)
    if weight is not None:
        cleaned["weight_kg"] = weight
    measured, _ = _iso_date(value.get("measured_on"), "The date measured", today)
    if measured:
        cleaned["measured_on"] = measured
    return cleaned


_PLACEMENT_DATES = (
    ("matching_date", "The date of matching"),
    ("accepted_date", "The date accepted by the adopters"),
    ("entrustment_date", "The date of entrustment"),
)


def _clean_placement(entry, value, today):
    if value is None:
        return {}
    allowed = {"matching_date", "racco_cpa", "accepted_date", "entrustment_date"}
    if not isinstance(value, dict) or set(value) - allowed:
        _fail("Placement history keeps the dates of matching, acceptance and "
              "entrustment, and the name of the RACCO or CPA.")
    cleaned = {}
    name = _text(value.get("racco_cpa"), "The name of the RACCO or CPA")
    if name:
        cleaned["racco_cpa"] = name
    given = []
    for key, label in _PLACEMENT_DATES:
        text, parsed = _iso_date(value.get(key), label, today)
        if text:
            cleaned[key] = text
            given.append((label, parsed))
    # Matching comes before acceptance, and acceptance before entrustment, so
    # each date given must not be earlier than the one given before it.
    for (earlier_label, earlier), (later_label, later) in zip(given, given[1:]):
        if later < earlier:
            _fail(f"{later_label} cannot be before {earlier_label[0].lower()}"
                  f"{earlier_label[1:]}.")
    return cleaned


_CLEANERS = {
    "prose": _clean_prose,
    "list": _clean_list,
    "table": _clean_table,
    "pap_table": _clean_pap_table,
    "date": _clean_date,
    "tick": _clean_tick,
    "measurements": _clean_measurements,
    "placement": _clean_placement,
}


def clean_date_prepared(value, birth_date, today=None):
    """The date the report is prepared: not in the future, and not before the
    child was born (every age on the report is worked out as of it). None or
    an empty string clears it."""
    if value is None or value == "":
        return None
    _, parsed = _iso_date(value, "The date prepared", today)
    if birth_date and parsed < birth_date:
        _fail("The date prepared cannot be before the child's date of birth.")
    return parsed


def clean_value(entry, value, today=None):
    """The value to store for this box, or a ValidationError with a sentence."""
    return _CLEANERS[entry["kind"]](entry, value, today)


def check_against_other_sections(entry, cleaned, other_value):
    """Rules that compare one box with another, checked at save against what
    the other box holds now. `other_value` is the stored value of the box this
    one is compared with (see `partner_of`), or None.

    The notarized date is never before the signed date, checked from both
    sides: moving the signed date past a notarized one already saved would
    otherwise leave the pair out of order with nothing noticing.
    """
    if not cleaned or not other_value:
        return
    if entry["key"] == DVC_NOTARIZED:
        signed, notarized = other_value, cleaned
    elif entry["key"] == DVC_SIGNED:
        signed, notarized = cleaned, other_value
    else:
        return
    if notarized < signed:
        _fail("The Deed cannot be notarized before it was signed.")


def partner_of(key):
    """The box a box is compared with, or None."""
    return {DVC_NOTARIZED: DVC_SIGNED, DVC_SIGNED: DVC_NOTARIZED}.get(key)
