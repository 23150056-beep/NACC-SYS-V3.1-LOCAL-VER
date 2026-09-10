"""Invented case referrals, so demo children can actually be booked.

Booking requires a referral on file - see scheduling/booking.py - and
`seed_demo_data` had never created one, so the rule shipped and closed the
calendar for every seeded child at once. Each refusal was correct and every one
of them was about the fixture rather than the feature.

The same standard the availability seeding is held to: what this writes, the
booking endpoint would accept. Mock data the real rules reject is not a sample
of the system, it is a second system sharing a database, and every feature
built against it inherits the difference.

Each document says in its own first line that it is invented. A file that
reads like a genuine referral is a file somebody eventually mistakes for one,
and this one is about a child who does not exist.
"""
from django.core.files.base import ContentFile

from clinical.models import CaseReferral

TEMPLATE = """CASE REFERRAL — DEMONSTRATION DOCUMENT

This document is invented. It was generated for a fictional child so that the
demonstration system has something to hold, and it records nothing about any
real person. Do not treat it as a case record.

Child:            {name}
Case reference:   {reference}
Referred by:      Municipal Social Welfare and Development Office
Reason for referral:
    Referred for psychosocial assessment and counselling support following
    an intake interview. Placement and guardianship details are recorded in
    the child's own record.

Attachments named in the original: none.
"""


def build_text(child):
    return TEMPLATE.format(
        name=child.fullname,
        reference=f"RACCO1-{child.pk:05d}",
    )


def install_referrals(children, uploaded_by=None):
    """Give each child a referral, if they have none. Returns how many were made.

    Never overwrites: a child who already has one keeps it, because the one
    they have might be real and this one certainly is not.
    """
    made = 0
    for child in children:
        if CaseReferral.objects.filter(child=child).exists():
            continue
        slug = "".join(c if c.isalnum() else "-" for c in child.fullname).strip("-").lower()
        filename = f"case-referral-{slug or child.pk}.txt"
        referral = CaseReferral(
            child=child,
            uploaded_by=uploaded_by,
            original_filename=filename,
            description="Case referral (demonstration document)",
            extracted_text=build_text(child),
        )
        referral.file.save(filename, ContentFile(build_text(child).encode("utf-8")),
                           save=False)
        referral.save()
        made += 1
    return made
