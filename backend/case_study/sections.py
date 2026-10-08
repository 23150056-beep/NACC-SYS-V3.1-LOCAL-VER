"""The Social Case Study Report's boxes, in the template's order.

The template is docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx and
the design is docs/superpowers/specs/2026-10-07-scsr-parts-2-5-design.md.
Section ids and the printed numbering follow the template's own: three lettered
blocks - A the child, B the prospective adoptive parents, C the placement -
with the roman numbers starting again in each. Part I of block A
(Identifying Information) is not here: it is read from the child's record.

This catalogue is the server's copy. frontend/src/config/scsr.js is the
browser's, and case_study/tests/test_catalogue.py fails when the two differ,
the same arrangement as children/intake.py and caseData.js.

Guidance, not fields. `hints` condenses the template's bullet points for the
screen to show beside each box. None of them is checked, and when the agency
rewords one no id changes.

Keys are never renamed or reused - see ALL_KEYS_EVER.
"""

BLOCKS = (
    {"block": "A", "title": "The Child/Adoptee"},
    {"block": "B", "title": "The Prospective Adoptive Parents (PAPs)"},
    {"block": "C", "title": "Adoption Placement"},
)

# The kinds of box, and what each stores (case_study/validation.py):
#   prose        text                      list     lines of text
#   table        rows of the given columns pap_table the fixed Female/Male PAP grid
#   date         one date                  tick     yes/no
#   measurements height, weight, date      placement the four placement dates and name
KINDS = ("prose", "list", "table", "pap_table", "date", "tick", "measurements", "placement")

# Column types of a table: text, partial_date ("2019", "2019-05" or a full
# date, for a vaccine given long ago), date, int (0-150) and choice (one of
# `options`).
COLUMN_TYPES = ("text", "partial_date", "date", "int", "choice")

# Who a section applies to. A section that does not apply to a child is not
# asked, not required and cannot be saved.
RULES = ("always", "surrendered", "abandoned", "stc", "placement_history")

# Child.case_category and Child.type_of_adoption, as stored
# (children/models.py).
SURRENDERED = ("Surrendered",)
ABANDONED = ("Abandoned", "Without Known Parents")
# The template's preamble: supervised trial custody is for a regular, an IP
# and a foster adoption.
STC_TYPES = ("Regular", "IP", "Foster-Adopt")
ADULT = "Adult"
DOMESTIC_RELATIVE = "Domestic Relative"


def _col(key, label, type_="text", options=None):
    column = {"key": key, "label": label, "type": type_}
    if options is not None:
        column["options"] = [{"value": v, "label": lab} for v, lab in options]
    return column


def _section(key, block, number, title, kind, *, columns=(), may_be_na=False,
             applies="always", hints=()):
    assert kind in KINDS and applies in RULES
    return {
        "key": key, "block": block, "number": number, "title": title, "kind": kind,
        "columns": list(columns), "may_be_na": may_be_na, "applies": applies,
        "hints": list(hints),
    }


SCSR_SECTIONS = (
    # --- A. The Child/Adoptee ------------------------------------------------
    _section(
        "a2_sources", "A", "II", "Sources of information", "list",
        hints=[
            "List each person and document consulted, one per line.",
            "Examples: the child, the grandmother or petitioner, the guardian, "
            "and existing documents such as medical and birth records.",
        ]),
    _section(
        "a2_circumstances", "A", "II", "Circumstances of referral or admission", "prose",
        hints=[
            "Say why the child was referred or admitted, and where the child was "
            "referred or entrusted.",
            "Say who was responsible for the referral or entrustment, when it was made, "
            "and when the child was admitted or entrusted.",
            "Write in the past tense.",
        ]),
    _section(
        "a3_description", "A", "III", "Description of the child upon admission or entrustment",
        "prose",
        hints=[
            "Be brief, accurate and factual, and write in the past tense.",
            "Age, personality, physical deformities and birthmarks if any, and other "
            "significant observations on admission.",
            "The anthropometric measurements.",
            "Overall appearance: whether the child was properly cared for, the clothing "
            "and the hygiene. Leave out graphic details.",
            "For a child without known parents: the condition and appearance when found "
            "(no graphic details), behavior when found, who named the child and what "
            "the name means.",
            "Developmental achievements, such as fine and gross motor skills and speech "
            "and language.",
        ]),
    _section(
        "a3_medical", "A", "III", "Medical history before placement", "prose",
        hints=[
            "Summarize the significant medical history or conditions, in the past tense.",
            "Circumstances of birth: type of delivery, place of birth, birth weight and "
            "length, head and chest circumference, newborn screening result, "
            "immunizations received.",
            "Age of gestation in weeks, with the Ballard and APGAR scores, if the child "
            "was born in a hospital.",
            "If the child stayed with the birth mother, relatives or an institution "
            "before admission, include what they report. Did the birth mother "
            "breastfeed?",
            "Previous illnesses and the treatment or medication given.",
        ]),
    _section(
        "a3_psych_highlights", "A", "III", "Highlights of the psychological evaluation",
        "prose",
        hints=[
            "For a child aged 5 or older: the result of the psychological evaluation and "
            "the interventions required.",
            "Discuss the child's progress from carrying out the psychologist's "
            "recommendations.",
        ]),
    _section(
        "a3_immunizations", "A", "III", "Immunizations", "table",
        columns=[
            _col("vaccine", "Type of vaccine"),
            _col("date", "Date administered", "partial_date"),
            _col("place", "Place administered"),
        ],
        hints=[
            "One row per vaccine: the type, the date given and the place given.",
            "A date may be a year, a year and month, or a full date.",
        ]),
    _section(
        "a3_development", "A", "III", "Developmental history", "prose",
        hints=[
            "Summarize the overall developmental achievements, in the past tense.",
            "The milestones achieved since admission, such as holding the head up, "
            "rolling over, crawling, standing and walking, with a baseline of what the "
            "child could do at admission.",
            "Whether toilet training has begun and, if so, how it is going.",
            "The child's activities at home, school, the center or the foster home.",
        ]),
    _section(
        "a4_family_composition", "A", "IV", "Family composition", "table", may_be_na=True,
        columns=[
            _col("name", "Name"),
            _col("relationship", "Relationship to the child"),
            _col("age", "Age", "int"),
            _col("sex", "Sex"),
            _col("civil_status", "Civil status"),
            _col("education", "Educational attainment"),
            _col("employment_income", "Employment / income"),
        ],
        hints=[
            "One row per family member.",
            "Tick Not applicable if there is no family to describe.",
        ]),
    _section(
        "a4_family_description", "A", "IV", "Family description", "prose", may_be_na=True,
        hints=[
            "Physical description of the birth parents: build, height, complexion, hair, "
            "eyes, nose and any disability or deformity.",
            "Health history, physical and mental, hereditary or not, with medications; "
            "for the birth mother, the prenatal history.",
            "Education, occupation, income and earnings.",
            "Personality and emotional make-up: traits, hobbies, interests, talents.",
            "Family relationships: the marital relationship, the parents' relationship "
            "with their children, and between siblings.",
            "How the parents met, whether they were married, their own siblings, and any "
            "illness that affects their parenting. If a parent is detained, the status "
            "of the case.",
            "Childhood experiences of the birth parents that may affect their parenting.",
            "Any history of substance or alcohol abuse, sexual abuse, domestic violence "
            "or a criminal record. For substance use during pregnancy: how often, for "
            "how long and how much.",
            "The birth parents' attitude toward the child while the child was in their "
            "care.",
            "For a parent with a history of counseling or psychiatric treatment: the "
            "reason, the institution and the professional. A report belongs in the "
            "child's dossier.",
            "Tick Not applicable if there is no family to describe.",
        ]),
    _section(
        "a5_summary", "A", "V",
        "Termination of parental rights or facts of abandonment: summary", "prose",
        hints=[
            "Summarize the circumstances of the termination of parental rights or of the "
            "abandonment.",
            "Include the application or petition for the CDCLAA, if applicable. The date "
            "the CDCLAA was issued is shown from the record.",
        ]),
    _section(
        "a5_dvc_signed", "A", "V", "Deed of Voluntary Commitment: date signed", "date",
        applies="surrendered",
        hints=[
            "The date the birth parent or parents signed the Deed.",
            "A valid ID of the parent must be presented to the notary public.",
        ]),
    _section(
        "a5_dvc_notarized", "A", "V", "Deed of Voluntary Commitment: date notarized", "date",
        applies="surrendered",
        hints=["The date the Deed was notarized. It cannot be earlier than the date signed."]),
    _section(
        "a5_counselling", "A", "V", "Counseling before, during and after the Deed", "table",
        applies="surrendered",
        columns=[
            _col("date", "Date", "date"),
            _col("stage", "Stage", "choice", [
                ("before", "Before the Deed"), ("during", "During the signing"),
                ("after", "After the Deed")]),
            _col("goals", "Goals"),
        ],
        hints=[
            "The counseling the social worker gave before, during and after the signing "
            "of the Deed, with the dates and goals.",
            "It should not be only an orientation on the meaning of the Deed.",
            "The birth parent should be helped to process loss, grief and trauma from "
            "giving up the child.",
        ]),
    _section(
        "a5_assistance", "A", "V",
        "Assistance to the birth parents and efforts to prevent surrender", "prose",
        applies="surrendered",
        hints=[
            "Poverty cannot be the sole reason for surrender. Say what services were "
            "given in response to the circumstances behind the decision, and why they "
            "failed to help the parents.",
            "For a victim of rape: the counseling goals to help the birth mother "
            "overcome the trauma, the intervention given, and where she is now.",
            "The social worker's efforts to place the child with relatives.",
            "The social worker's efforts to prevent the child from being given up for "
            "adoption.",
        ]),
    _section(
        "a5_aware_irrevocable", "A", "V",
        "Birth parent aware that the Deed becomes irrevocable", "tick",
        applies="surrendered",
        hints=[
            "Tick to confirm: the birth parent is aware that the Deed of Voluntary "
            "Commitment becomes irrevocable three months after signing, so there is time "
            "to reconsider the decision.",
            "The printed report carries this sentence. It is required to finalize.",
        ]),
    _section(
        "a5_explained_vernacular", "A", "V", "Deed explained in the vernacular", "tick",
        applies="surrendered",
        hints=[
            "Tick to confirm: the content of the Deed was explained to the birth parent "
            "in the vernacular he or she understands.",
            "The printed report carries this sentence. It is required to finalize.",
        ]),
    _section(
        "a5_abandonment", "A", "V", "Facts of abandonment", "prose", applies="abandoned",
        hints=[
            "The circumstances of the abandonment: who found the child, where, when, how "
            "old the child was, the child's condition when found, and how the finder "
            "placed the child with an institution or agency.",
            "The date found, the place found and the age when found are shown from the "
            "record.",
        ]),
    _section(
        "a5_search_efforts", "A", "V", "Efforts to locate the birth family", "table",
        applies="abandoned",
        columns=[
            _col("kind", "Kind of effort", "choice", [
                ("media_certification", "Media certification"),
                ("newspaper_publication", "Newspaper publication"),
                ("blotter", "Blotter report"),
                ("registered_mail", "Registered mail"),
                ("home_visit", "Home visit"),
                ("other", "Other")]),
            _col("date", "Date", "date"),
            _col("note", "Note"),
        ],
        hints=[
            "The social worker's efforts to find the birth parents or family: the date of "
            "the media certification, newspaper publication, blotter report, registered "
            "mail and so on.",
            "A home visit to the birth mother's or a family member's last known address "
            "is required, if possible, besides the registered mail.",
        ]),

    # --- B. The Prospective Adoptive Parents ---------------------------------
    _section(
        "b1_paps", "B", "I", "Prospective adoptive parents: identifying information",
        "pap_table",
        hints=[
            "Covers the period from application to matching.",
            "Either column may be left empty, for a single adopter or a step-parent, but "
            "at least one adopter must be named.",
        ]),
    _section(
        "b2_household", "B", "II",
        "Family composition and other individuals living with the PAPs", "table",
        columns=[
            _col("name", "Name"),
            _col("age", "Age", "int"),
            _col("relationship", "Relationship to the client"),
            _col("education", "Educational attainment"),
            _col("occupation", "Occupation"),
            _col("disability", "Disability or sickness, if any"),
        ],
        hints=["Everyone living with the prospective adoptive parents, one row each."]),
    _section(
        "b3_family_background", "B", "III",
        "Family background information and description of the PAPs", "prose",
        hints=[
            "The PAPs' parents and siblings, their childhood experiences and how they "
            "were reared.",
            "The family's discipline pattern, including sensitive areas: a history of "
            "child abuse, alcohol or substance abuse and its penalties (say if arrested, "
            "detained or rehabilitated), vices, and how they cope with stress and "
            "conflict.",
            "Physical description and personal traits, social relationships and "
            "educational history.",
        ]),
    _section(
        "b4_motivation", "B", "IV", "Motivation to adopt", "prose",
        hints=[
            "The reasons for wanting to adopt, who decided, when and how the decision "
            "was reached.",
            "Attitude and resolution of feelings about infertility, if applicable, and "
            "understanding of adoption issues.",
        ]),
    _section(
        "b5_child_care_plans", "B", "V", "Child care and guardianship plans", "prose",
        hints=[
            "The length of parental leave, if applicable, day-to-day care, childcare "
            "after the leave, the child's education, and how work and other commitments "
            "will be managed.",
            "The attitude of the extended family and friends, and any future plans for "
            "the child.",
            "Their thoughts and plans if a pregnancy occurs after the adoption.",
            "Individuals designated in case the couple cannot parent the child.",
        ]),
    _section(
        "b6_marital_history", "B", "VI", "Marital history and relationship", "prose",
        may_be_na=True,
        hints=[
            "The nature and extent of the marital relationship and how conflicts are "
            "resolved.",
            "Annulment history, with the circumstances and the relationship; decision "
            "making.",
            "Children from previous marriages who live with them.",
            "Current family relationships: husband and wife, parent and child, siblings "
            "and extended family.",
            "Tick Not applicable if it does not apply.",
        ]),
    _section(
        "b7_children_in_family", "B", "VII", "Children in the family", "prose",
        may_be_na=True,
        hints=[
            "For each child: biological or adopted, date of birth, age, characteristics "
            "and traits, educational attainment, role in the home, preparation for "
            "another child, and feelings and attitude toward adoption.",
            "Any previous history of adoption disruption.",
            "Tick Not applicable if there are no other children.",
        ]),
    _section(
        "b8_other_individuals", "B", "VIII", "Other individuals living at the home", "prose",
        may_be_na=True,
        hints=[
            "A paragraph on every person living in the home.",
            "Adult members are asked about substance abuse, sexual abuse and child abuse; "
            "record their responses.",
            "Tick Not applicable if no one else lives in the home.",
        ]),
    _section(
        "b9_family_attitude", "B", "IX",
        "Attitude of immediate family members regarding adoption", "prose",
        hints=[
            "Whether the adoption plan was discussed with the extended family, their "
            "thoughts and attitude, and their involvement with the child after "
            "placement.",
        ]),
    _section(
        "b10_parenting_experience", "B", "X", "Parenting and child caring experience",
        "prose",
        hints=[
            "Their experience taking care of a child, on a temporary or long-term basis.",
            "Past parenting experience and knowledge of childcare, their attitude toward "
            "discipline, and their ability to give nurturing care and supervision in an "
            "atmosphere of affection and moral and material security.",
        ]),
    _section(
        "b11_employment_finances", "B", "XI", "Employment history and financial resources",
        "prose",
        hints=[
            "The employment history, with the reasons they moved or changed work.",
            "A short description of income, savings, investments, expenditures, "
            "liabilities and insurance.",
        ]),
    _section(
        "b12_home_community", "B", "XII", "Description of home and community", "prose",
        hints=[
            "The family's home and the child's accommodation; membership and "
            "participation in community organizations, projects and activities.",
            "Community resources and facilities for children, such as hospitals, "
            "clinics and schools.",
            "Peace and order in the community and the degree of racial tolerance, if "
            "applicable, and how these may affect the child's adjustment.",
        ]),
    _section(
        "b13_identity", "B", "XIII",
        "Social, ethno-cultural, linguistic and religious identity of the PAPs", "prose",
        hints=["Spiritual, philosophical, moral or religious beliefs, affiliations and practices."]),
    _section(
        "b14_health_history", "B", "XIV", "Health history", "prose",
        hints=[
            "Any serious illness, physical disability or history of mental illness. A "
            "medical report on the family's health status and history should be "
            "discussed.",
            "The psychological evaluation belongs in this box: the psychometric tests "
            "done, the results of the tests and clinical interviews, the assessment "
            "summary and the psychologist's recommendations.",
        ]),
    _section(
        "b15_references_clearances", "B", "XV", "Character references and clearances",
        "prose",
        hints=["A summary of the character references, including the clearances showing "
               "good moral character."]),
    _section(
        "b16_trainings", "B", "XVI", "Adoption trainings and forums attended or completed",
        "prose",
        hints=["For adopters who have had pre-adoption training: the title, the number of "
               "hours, the topics discussed and what they learned."]),
    _section(
        "b17_adoption_telling", "B", "XVII", "Adoption telling and post-adoption issues",
        "prose",
        hints=[
            "How they plan to tell the child the adoption story.",
            "Their opinion on adoption search and reunion with the birth family.",
        ]),

    # --- C. Adoption Placement -----------------------------------------------
    _section(
        "c1_placement", "C", "I", "Placement history", "placement",
        applies="placement_history",
        hints=[
            "Covers the period from entrustment, through supervised trial custody if "
            "any, up to before the petition is filed.",
            "Not asked for an Adult adoption, or for a Relative adoption of a child who "
            "was in the adopters' custody for more than two years.",
            "The age at entrustment is worked out from the date of birth.",
        ]),
    _section(
        "c2_on_placement", "C", "II", "Child upon placement", "prose",
        hints=[
            "Describe the child upon placement or entrustment, or when the adopters took "
            "custody.",
            "A summary statement of the child's overall development: physical and "
            "health, social, emotional and cognitive.",
        ]),
    _section(
        "c3_stc_report", "C", "III", "Supervised trial custody report", "prose",
        may_be_na=True, applies="stc",
        hints=[
            "A summary statement on the placement, including issues and concerns and how "
            "the child and the family adjusted to each other.",
            "Asked for a Regular, IP or Foster-Adopt adoption. Tick Not applicable if "
            "there was no supervised trial custody.",
        ]),
    _section(
        "c4_measurements", "C", "IV", "Current functioning: height and weight",
        "measurements",
        hints=["The child's current height and weight, and the date they were measured."]),
    _section(
        "c4_functioning", "C", "IV", "Current functioning", "prose",
        hints=[
            "How the child is doing now: emotional, social, psychological and cognitive "
            "functioning after placement.",
            "Emotional and social: how the child relates to the adoptive parents and "
            "siblings, extended family, other children, caregivers and strangers, and "
            "whether the child has formed an attachment to a specific person.",
            "What makes the child happy or sad, what the child does when frustrated or "
            "angry, whether the child can regulate emotions and express feelings freely, "
            "and how the child is pacified.",
            "Which discipline works best, any behavioral concerns, and how the adoptive "
            "parents handle the behavior.",
            "The child's personality, hobbies and interests.",
            "Psychological: strengths and weaknesses, confidence and self-esteem, how the "
            "child deals with frustration, and the ways the child learns.",
            "Educational: grade level, academic performance, the subjects where the "
            "child excels or needs help, and the teacher's view. The education level is "
            "shown from the record.",
            "For a child in play, occupational or speech therapy: the progress made.",
            "How the adoptive parents support the child's overall growth, and the degree "
            "of attachment and bonding.",
        ]),
    _section(
        "c5_assessment", "C", "V", "Assessment", "prose",
        hints=["A summary statement on the progress of the placement and the overall "
               "changes to the adopters' family since the placement."]),
    _section(
        "c6_recommendation", "C", "VI", "Recommendation", "prose",
        hints=["A summary statement of the social worker's recommendation on the "
               "placement, such as the issuance of an Order of Adoption or its denial."]),
)

# The PAP table's rows, in the template's order (B.I). Fixed: the table is
# Female PAP and Male PAP down the same 17 rows. `id` is stored in the saved
# value, so like a section key it is never renamed.
PAP_ROWS = (
    {"id": "full_name", "label": "Complete name (first, middle and last name)"},
    {"id": "date_of_birth", "label": "Date of birth"},
    {"id": "place_of_birth", "label": "Place of birth"},
    {"id": "religion", "label": "Religion"},
    {"id": "civil_status", "label": "Civil status (date and place of marriage, if applicable)"},
    {"id": "ethnicity_citizenship", "label": "Ethnicity / citizenship"},
    {"id": "languages", "label": "Language/s spoken"},
    {"id": "education", "label": "Education"},
    {"id": "occupation", "label": "Occupation"},
    {"id": "mobile_phone", "label": "Mobile phone"},
    {"id": "home_phone", "label": "Home phone number (landline)"},
    {"id": "work_phone", "label": "Work phone (if applicable)"},
    {"id": "email", "label": "E-mail address (if applicable)"},
    {"id": "employer", "label": "Employer"},
    {"id": "employer_address", "label": "Employer address and contact details"},
    {"id": "monthly_income", "label": "Monthly income"},
    {"id": "other_income", "label": "Other sources of income"},
)
PAP_SIDES = ("female", "male")
# The rows that hold a phone number or an e-mail address. The demo import
# blanks these (import_demo_data): an invented number is somebody's real
# handset, the reason demo custodians have none either.
PAP_CONTACT_ROWS = ("mobile_phone", "home_phone", "work_phone", "email", "employer_address")

# Every key ever issued, written out in full and never derived from
# SCSR_SECTIONS - deriving it would make the pinning test below it vacuous.
# KEYS ARE NEVER RENAMED OR REUSED. A saved section is stored under its key, so
# a renamed key orphans every row that holds it (the children 0020 trap, with
# JSON keys instead of a column), and a reused key would hand one box's old text
# to another. Retire a box by removing it from SCSR_SECTIONS and leaving its key
# here; add a new box with a new key and no migration.
ALL_KEYS_EVER = (
    "a2_sources", "a2_circumstances", "a3_description", "a3_medical",
    "a3_psych_highlights", "a3_immunizations", "a3_development",
    "a4_family_composition", "a4_family_description",
    "a5_summary", "a5_dvc_signed", "a5_dvc_notarized", "a5_counselling",
    "a5_assistance", "a5_aware_irrevocable", "a5_explained_vernacular",
    "a5_abandonment", "a5_search_efforts",
    "b1_paps", "b2_household", "b3_family_background", "b4_motivation",
    "b5_child_care_plans", "b6_marital_history", "b7_children_in_family",
    "b8_other_individuals", "b9_family_attitude", "b10_parenting_experience",
    "b11_employment_finances", "b12_home_community", "b13_identity",
    "b14_health_history", "b15_references_clearances", "b16_trainings",
    "b17_adoption_telling",
    "c1_placement", "c2_on_placement", "c3_stc_report", "c4_measurements",
    "c4_functioning", "c5_assessment", "c6_recommendation",
)

SECTION_BY_KEY = {entry["key"]: entry for entry in SCSR_SECTIONS}

# Block A is all a psychologist may read (owner's decision 1, 8 Oct 2026).
BLOCK_A_KEYS = tuple(e["key"] for e in SCSR_SECTIONS if e["block"] == "A")
# Their findings reach the case study through the social worker's text, so a
# psychologist whose history is not carried (Child.assignee_sees_history False)
# must not read it back: that would go around the carry-history control.
PSYCH_HIGHLIGHTS = "a3_psych_highlights"

DVC_SIGNED = "a5_dvc_signed"
DVC_NOTARIZED = "a5_dvc_notarized"


def entry_for(key):
    return SECTION_BY_KEY.get(key)


def _surrendered(child, case_study):
    return child.case_category in SURRENDERED


def _abandoned(child, case_study):
    return child.case_category in ABANDONED


def _stc(child, case_study):
    return child.type_of_adoption in STC_TYPES


def _placement_history(child, case_study):
    """Not for an Adult adoption, nor for a Domestic Relative one when the child
    had been in the adopter's custody for more than two years (the template's
    note under Placement History). An unanswered question does not hide it."""
    if child.type_of_adoption == ADULT:
        return False
    if (child.type_of_adoption == DOMESTIC_RELATIVE
            and getattr(case_study, "custody_over_two_years", None) is True):
        return False
    return True


_RULE_FUNCTIONS = {
    "always": lambda child, case_study: True,
    "surrendered": _surrendered,
    "abandoned": _abandoned,
    "stc": _stc,
    "placement_history": _placement_history,
}


def applies(entry, child, case_study):
    """Does this box apply to this child? `case_study` may be None (nothing
    started yet) and is read only for the Domestic Relative custody answer."""
    return _RULE_FUNCTIONS[entry["applies"]](child, case_study)
