"""The current RACCO I adoption process, as data.

This is the seed, not the source of truth — once a row exists the seeder leaves
it alone, so an office that corrects a target keeps the correction. The spec is
explicit about why: "Stage names, owning roles and day targets should be
confirmed against RACCO I's current issuances before build — they are
configuration in this design precisely so they can be corrected without a code
change."

`owned_externally` marks the requirements nobody in this office can clear: a
court publication period, the national office's own signature. They are the
difference between a case that is stuck and a case that is merely waiting, and
staff should not be chased about the second kind.
"""

# (number, name, owner_role, target_days)
STAGES = [
    (1, "Case review & endorsement", "Staff", 14),
    (2, "CDCLAA issuance", "Legal unit", 60),
    (3, "Child study & docketing", "Assigned social worker", 30),
    (4, "Matching conference", "RACCC panel", 45),
    (5, "PAPA issued", "NACC regional director", 30),
    # The spec writes this one as "6 months"; stored in days so that every
    # comparison in the codebase stays a single subtraction.
    (6, "Supervised trial custody", "Social worker", 180),
    (7, "Adoption order", "NACC central", 60),
    (8, "Finalized & post-placement", "Staff", None),
]

# stage number -> [(code, label, owned_externally)]
REQUIREMENTS = {
    1: [
        ("psych_report_attached", "Psychologist's report attached", False),
        ("case_plan_confirmed", "Case plan confirmed as Adoption", False),
        ("consent_on_file", "Guardian / child consent on file", False),
        ("owner_assigned", "Case owner assigned", False),
    ],
    2: [
        ("petition_filed", "Petition for CDCLAA filed", False),
        ("publication_elapsed", "Publication period elapsed", True),
        ("cdclaa_issued", "CDCLAA document uploaded and verified", True),
    ],
    3: [
        ("study_report_signed", "Social case study report signed by supervisor", False),
        ("health_dental_current", "Health & dental record current", False),
        ("profile_packet_complete", "Child profile packet complete", False),
    ],
    4: [
        ("conference_held", "Matching conference held", False),
        ("minutes_uploaded", "Conference minutes uploaded", False),
        ("pap_selected", "Prospective adoptive parents selected", False),
    ],
    5: [
        ("papa_uploaded", "Pre-Adoption Placement Authority uploaded", True),
        ("pap_credentials_valid", "PAP's CEA and home study still unexpired", False),
        ("placement_date_set", "Placement date set", False),
    ],
    6: [
        ("placement_recorded", "Placement date recorded", False),
        ("visit_reports_filed", "Minimum supervisory visit reports filed", False),
        ("trial_period_elapsed", "Trial period elapsed", False),
        ("final_recommendation_signed", "Final recommendation signed", False),
    ],
    7: [
        ("adoption_order_issued", "Order of adoption issued", True),
        ("certificate_of_finality", "Certificate of finality received", True),
    ],
    8: [
        ("amended_birth_certificate", "Amended birth certificate on file", True),
        ("post_placement_reports", "Post-placement reports complete", False),
    ],
}
