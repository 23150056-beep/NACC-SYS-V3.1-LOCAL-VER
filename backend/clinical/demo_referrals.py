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

Written as a PDF because that is what the upload form accepts. As .txt these
satisfied the booking gate while the form would have refused the same file —
one system giving two answers about whether a referral is valid, which only
shows when somebody tries to replace one.
"""
from django.core.files.base import ContentFile

from clinical.demo_pdf import build_pdf
from clinical.models import CaseReferral

# How this seeder recognises its own work. Only a document carrying this
# exact description may be replaced — anything else might be a real referral
# somebody scanned, and that is not this module's to touch.
DEMO_DESCRIPTION = "Case referral (demonstration document)"

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
        existing = list(CaseReferral.objects.filter(child=child))
        if existing:
            # Databases seeded before this wrote .txt, which the upload form
            # refuses — so the file satisfied the booking gate while the form
            # would have rejected the very same document. The seeder may
            # replace what it wrote itself, and nothing else: an upload that
            # did not come from here might be a real scan.
            stale = [r for r in existing
                     if r.description == DEMO_DESCRIPTION
                     and not (r.original_filename or "").lower().endswith(".pdf")]
            if len(stale) != len(existing):
                continue
            for row in stale:
                row.file.delete(save=False)
                row.delete()
        slug = "".join(c if c.isalnum() else "-" for c in child.fullname).strip("-").lower()
        filename = f"case-referral-{slug or child.pk}.pdf"
        body = build_text(child)
        referral = CaseReferral(
            child=child,
            # The child's own social worker files it, as a real one would;
            # `uploaded_by` is for a child that has none.
            uploaded_by=child.social_worker or uploaded_by,
            original_filename=filename,
            description=DEMO_DESCRIPTION,
            # The text is kept alongside the PDF so the document-summary path
            # has something to read without a PDF parser.
            extracted_text=body,
        )
        referral.file.save(filename, ContentFile(build_pdf(body.splitlines())),
                           save=False)
        referral.save()
        made += 1
    return made
