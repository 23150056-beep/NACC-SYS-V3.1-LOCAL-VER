/* The Social Case Study Report's boxes, in the template's order.
 *
 * This is the browser's copy of backend/case_study/sections.py, and the backend
 * is the authority: backend/case_study/tests/test_catalogue.py reads this file
 * and fails when the two differ in keys, order, blocks, numbers, titles, kinds,
 * columns, "may be not applicable", hints or which children a box applies to.
 * Change one and not the other and that test fails, which is the point.
 *
 * Plain data on purpose: the arrays below are JSON, which the test parses.
 * Keep them JSON (double quotes, no trailing commas, no comments inside) when
 * editing. Keys are never renamed or reused: a saved section is stored under
 * its key.
 *
 * Part I (Identifying Information) is not a box: it is read from the child's
 * record (`record_facts` in GET /api/case-studies/child/<id>/).
 *
 * `kind` says what a box stores:
 *   prose         a string                     list        an array of strings
 *   table         an array of rows keyed by the box's `columns`
 *   pap_table     { female: {rowId: text}, male: {rowId: text} } over PAP_ROWS
 *   date          'YYYY-MM-DD' or null         tick        true / false
 *   measurements  { height_cm, weight_kg, measured_on }
 *   placement     { matching_date, racco_cpa, accepted_date, entrustment_date }
 * A column's `type` is text, partial_date ('2019', '2019-05' or a full date),
 * date, int (0-150) or choice (one of its `options`).
 */

export const SCSR_BLOCKS = [
  {
    "block": "A",
    "title": "The Child/Adoptee"
  },
  {
    "block": "B",
    "title": "The Prospective Adoptive Parents (PAPs)"
  },
  {
    "block": "C",
    "title": "Adoption Placement"
  }
];

export const SCSR_SECTIONS = [
  {
    "key": "a2_sources",
    "block": "A",
    "number": "II",
    "title": "Sources of information",
    "kind": "list",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "List each person and document consulted, one per line.",
      "Examples: the child, the grandmother or petitioner, the guardian, and existing documents such as medical and birth records."
    ]
  },
  {
    "key": "a2_circumstances",
    "block": "A",
    "number": "II",
    "title": "Circumstances of referral or admission",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Say why the child was referred or admitted, and where the child was referred or entrusted.",
      "Say who was responsible for the referral or entrustment, when it was made, and when the child was admitted or entrusted.",
      "Write in the past tense."
    ]
  },
  {
    "key": "a3_description",
    "block": "A",
    "number": "III",
    "title": "Description of the child upon admission or entrustment",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Be brief, accurate and factual, and write in the past tense.",
      "Age, personality, physical deformities and birthmarks if any, and other significant observations on admission.",
      "The anthropometric measurements.",
      "Overall appearance: whether the child was properly cared for, the clothing and the hygiene. Leave out graphic details.",
      "For a child without known parents: the condition and appearance when found (no graphic details), behavior when found, who named the child and what the name means.",
      "Developmental achievements, such as fine and gross motor skills and speech and language."
    ]
  },
  {
    "key": "a3_medical",
    "block": "A",
    "number": "III",
    "title": "Medical history before placement",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Summarize the significant medical history or conditions, in the past tense.",
      "Circumstances of birth: type of delivery, place of birth, birth weight and length, head and chest circumference, newborn screening result, immunizations received.",
      "Age of gestation in weeks, with the Ballard and APGAR scores, if the child was born in a hospital.",
      "If the child stayed with the birth mother, relatives or an institution before admission, include what they report. Did the birth mother breastfeed?",
      "Previous illnesses and the treatment or medication given."
    ]
  },
  {
    "key": "a3_psych_highlights",
    "block": "A",
    "number": "III",
    "title": "Highlights of the psychological evaluation",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "For a child aged 5 or older: the result of the psychological evaluation and the interventions required.",
      "Discuss the child's progress from carrying out the psychologist's recommendations."
    ]
  },
  {
    "key": "a3_immunizations",
    "block": "A",
    "number": "III",
    "title": "Immunizations",
    "kind": "table",
    "columns": [
      {
        "key": "vaccine",
        "label": "Type of vaccine",
        "type": "text"
      },
      {
        "key": "date",
        "label": "Date administered",
        "type": "partial_date"
      },
      {
        "key": "place",
        "label": "Place administered",
        "type": "text"
      }
    ],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "One row per vaccine: the type, the date given and the place given.",
      "A date may be a year, a year and month, or a full date."
    ]
  },
  {
    "key": "a3_development",
    "block": "A",
    "number": "III",
    "title": "Developmental history",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Summarize the overall developmental achievements, in the past tense.",
      "The milestones achieved since admission, such as holding the head up, rolling over, crawling, standing and walking, with a baseline of what the child could do at admission.",
      "Whether toilet training has begun and, if so, how it is going.",
      "The child's activities at home, school, the center or the foster home."
    ]
  },
  {
    "key": "a4_family_composition",
    "block": "A",
    "number": "IV",
    "title": "Family composition",
    "kind": "table",
    "columns": [
      {
        "key": "name",
        "label": "Name",
        "type": "text"
      },
      {
        "key": "relationship",
        "label": "Relationship to the child",
        "type": "text"
      },
      {
        "key": "age",
        "label": "Age",
        "type": "int"
      },
      {
        "key": "sex",
        "label": "Sex",
        "type": "text"
      },
      {
        "key": "civil_status",
        "label": "Civil status",
        "type": "text"
      },
      {
        "key": "education",
        "label": "Educational attainment",
        "type": "text"
      },
      {
        "key": "employment_income",
        "label": "Employment / income",
        "type": "text"
      }
    ],
    "may_be_na": true,
    "applies": "always",
    "hints": [
      "One row per family member.",
      "Tick Not applicable if there is no family to describe."
    ]
  },
  {
    "key": "a4_family_description",
    "block": "A",
    "number": "IV",
    "title": "Family description",
    "kind": "prose",
    "columns": [],
    "may_be_na": true,
    "applies": "always",
    "hints": [
      "Physical description of the birth parents: build, height, complexion, hair, eyes, nose and any disability or deformity.",
      "Health history, physical and mental, hereditary or not, with medications; for the birth mother, the prenatal history.",
      "Education, occupation, income and earnings.",
      "Personality and emotional make-up: traits, hobbies, interests, talents.",
      "Family relationships: the marital relationship, the parents' relationship with their children, and between siblings.",
      "How the parents met, whether they were married, their own siblings, and any illness that affects their parenting. If a parent is detained, the status of the case.",
      "Childhood experiences of the birth parents that may affect their parenting.",
      "Any history of substance or alcohol abuse, sexual abuse, domestic violence or a criminal record. For substance use during pregnancy: how often, for how long and how much.",
      "The birth parents' attitude toward the child while the child was in their care.",
      "For a parent with a history of counseling or psychiatric treatment: the reason, the institution and the professional. A report belongs in the child's dossier.",
      "Tick Not applicable if there is no family to describe."
    ]
  },
  {
    "key": "a5_summary",
    "block": "A",
    "number": "V",
    "title": "Termination of parental rights or facts of abandonment: summary",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Summarize the circumstances of the termination of parental rights or of the abandonment.",
      "Include the application or petition for the CDCLAA, if applicable. The date the CDCLAA was issued is shown from the record."
    ]
  },
  {
    "key": "a5_dvc_signed",
    "block": "A",
    "number": "V",
    "title": "Deed of Voluntary Commitment: date signed",
    "kind": "date",
    "columns": [],
    "may_be_na": false,
    "applies": "surrendered",
    "hints": [
      "The date the birth parent or parents signed the Deed.",
      "A valid ID of the parent must be presented to the notary public."
    ]
  },
  {
    "key": "a5_dvc_notarized",
    "block": "A",
    "number": "V",
    "title": "Deed of Voluntary Commitment: date notarized",
    "kind": "date",
    "columns": [],
    "may_be_na": false,
    "applies": "surrendered",
    "hints": [
      "The date the Deed was notarized. It cannot be earlier than the date signed."
    ]
  },
  {
    "key": "a5_counselling",
    "block": "A",
    "number": "V",
    "title": "Counseling before, during and after the Deed",
    "kind": "table",
    "columns": [
      {
        "key": "date",
        "label": "Date",
        "type": "date"
      },
      {
        "key": "stage",
        "label": "Stage",
        "type": "choice",
        "options": [
          {
            "value": "before",
            "label": "Before the Deed"
          },
          {
            "value": "during",
            "label": "During the signing"
          },
          {
            "value": "after",
            "label": "After the Deed"
          }
        ]
      },
      {
        "key": "goals",
        "label": "Goals",
        "type": "text"
      }
    ],
    "may_be_na": false,
    "applies": "surrendered",
    "hints": [
      "The counseling the social worker gave before, during and after the signing of the Deed, with the dates and goals.",
      "It should not be only an orientation on the meaning of the Deed.",
      "The birth parent should be helped to process loss, grief and trauma from giving up the child."
    ]
  },
  {
    "key": "a5_assistance",
    "block": "A",
    "number": "V",
    "title": "Assistance to the birth parents and efforts to prevent surrender",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "surrendered",
    "hints": [
      "Poverty cannot be the sole reason for surrender. Say what services were given in response to the circumstances behind the decision, and why they failed to help the parents.",
      "For a victim of rape: the counseling goals to help the birth mother overcome the trauma, the intervention given, and where she is now.",
      "The social worker's efforts to place the child with relatives.",
      "The social worker's efforts to prevent the child from being given up for adoption."
    ]
  },
  {
    "key": "a5_aware_irrevocable",
    "block": "A",
    "number": "V",
    "title": "Birth parent aware that the Deed becomes irrevocable",
    "kind": "tick",
    "columns": [],
    "may_be_na": false,
    "applies": "surrendered",
    "hints": [
      "Tick to confirm: the birth parent is aware that the Deed of Voluntary Commitment becomes irrevocable three months after signing, so there is time to reconsider the decision.",
      "The printed report carries this sentence. It is required to finalize."
    ]
  },
  {
    "key": "a5_explained_vernacular",
    "block": "A",
    "number": "V",
    "title": "Deed explained in the vernacular",
    "kind": "tick",
    "columns": [],
    "may_be_na": false,
    "applies": "surrendered",
    "hints": [
      "Tick to confirm: the content of the Deed was explained to the birth parent in the vernacular he or she understands.",
      "The printed report carries this sentence. It is required to finalize."
    ]
  },
  {
    "key": "a5_abandonment",
    "block": "A",
    "number": "V",
    "title": "Facts of abandonment",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "abandoned",
    "hints": [
      "The circumstances of the abandonment: who found the child, where, when, how old the child was, the child's condition when found, and how the finder placed the child with an institution or agency.",
      "The date found, the place found and the age when found are shown from the record."
    ]
  },
  {
    "key": "a5_search_efforts",
    "block": "A",
    "number": "V",
    "title": "Efforts to locate the birth family",
    "kind": "table",
    "columns": [
      {
        "key": "kind",
        "label": "Kind of effort",
        "type": "choice",
        "options": [
          {
            "value": "media_certification",
            "label": "Media certification"
          },
          {
            "value": "newspaper_publication",
            "label": "Newspaper publication"
          },
          {
            "value": "blotter",
            "label": "Blotter report"
          },
          {
            "value": "registered_mail",
            "label": "Registered mail"
          },
          {
            "value": "home_visit",
            "label": "Home visit"
          },
          {
            "value": "other",
            "label": "Other"
          }
        ]
      },
      {
        "key": "date",
        "label": "Date",
        "type": "date"
      },
      {
        "key": "note",
        "label": "Note",
        "type": "text"
      }
    ],
    "may_be_na": false,
    "applies": "abandoned",
    "hints": [
      "The social worker's efforts to find the birth parents or family: the date of the media certification, newspaper publication, blotter report, registered mail and so on.",
      "A home visit to the birth mother's or a family member's last known address is required, if possible, besides the registered mail."
    ]
  },
  {
    "key": "b1_paps",
    "block": "B",
    "number": "I",
    "title": "Prospective adoptive parents: identifying information",
    "kind": "pap_table",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Covers the period from application to matching.",
      "Either column may be left empty, for a single adopter or a step-parent, but at least one adopter must be named."
    ]
  },
  {
    "key": "b2_household",
    "block": "B",
    "number": "II",
    "title": "Family composition and other individuals living with the PAPs",
    "kind": "table",
    "columns": [
      {
        "key": "name",
        "label": "Name",
        "type": "text"
      },
      {
        "key": "age",
        "label": "Age",
        "type": "int"
      },
      {
        "key": "relationship",
        "label": "Relationship to the client",
        "type": "text"
      },
      {
        "key": "education",
        "label": "Educational attainment",
        "type": "text"
      },
      {
        "key": "occupation",
        "label": "Occupation",
        "type": "text"
      },
      {
        "key": "disability",
        "label": "Disability or sickness, if any",
        "type": "text"
      }
    ],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Everyone living with the prospective adoptive parents, one row each."
    ]
  },
  {
    "key": "b3_family_background",
    "block": "B",
    "number": "III",
    "title": "Family background information and description of the PAPs",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "The PAPs' parents and siblings, their childhood experiences and how they were reared.",
      "The family's discipline pattern, including sensitive areas: a history of child abuse, alcohol or substance abuse and its penalties (say if arrested, detained or rehabilitated), vices, and how they cope with stress and conflict.",
      "Physical description and personal traits, social relationships and educational history."
    ]
  },
  {
    "key": "b4_motivation",
    "block": "B",
    "number": "IV",
    "title": "Motivation to adopt",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "The reasons for wanting to adopt, who decided, when and how the decision was reached.",
      "Attitude and resolution of feelings about infertility, if applicable, and understanding of adoption issues."
    ]
  },
  {
    "key": "b5_child_care_plans",
    "block": "B",
    "number": "V",
    "title": "Child care and guardianship plans",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "The length of parental leave, if applicable, day-to-day care, childcare after the leave, the child's education, and how work and other commitments will be managed.",
      "The attitude of the extended family and friends, and any future plans for the child.",
      "Their thoughts and plans if a pregnancy occurs after the adoption.",
      "Individuals designated in case the couple cannot parent the child."
    ]
  },
  {
    "key": "b6_marital_history",
    "block": "B",
    "number": "VI",
    "title": "Marital history and relationship",
    "kind": "prose",
    "columns": [],
    "may_be_na": true,
    "applies": "always",
    "hints": [
      "The nature and extent of the marital relationship and how conflicts are resolved.",
      "Annulment history, with the circumstances and the relationship; decision making.",
      "Children from previous marriages who live with them.",
      "Current family relationships: husband and wife, parent and child, siblings and extended family.",
      "Tick Not applicable if it does not apply."
    ]
  },
  {
    "key": "b7_children_in_family",
    "block": "B",
    "number": "VII",
    "title": "Children in the family",
    "kind": "prose",
    "columns": [],
    "may_be_na": true,
    "applies": "always",
    "hints": [
      "For each child: biological or adopted, date of birth, age, characteristics and traits, educational attainment, role in the home, preparation for another child, and feelings and attitude toward adoption.",
      "Any previous history of adoption disruption.",
      "Tick Not applicable if there are no other children."
    ]
  },
  {
    "key": "b8_other_individuals",
    "block": "B",
    "number": "VIII",
    "title": "Other individuals living at the home",
    "kind": "prose",
    "columns": [],
    "may_be_na": true,
    "applies": "always",
    "hints": [
      "A paragraph on every person living in the home.",
      "Adult members are asked about substance abuse, sexual abuse and child abuse; record their responses.",
      "Tick Not applicable if no one else lives in the home."
    ]
  },
  {
    "key": "b9_family_attitude",
    "block": "B",
    "number": "IX",
    "title": "Attitude of immediate family members regarding adoption",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Whether the adoption plan was discussed with the extended family, their thoughts and attitude, and their involvement with the child after placement."
    ]
  },
  {
    "key": "b10_parenting_experience",
    "block": "B",
    "number": "X",
    "title": "Parenting and child caring experience",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Their experience taking care of a child, on a temporary or long-term basis.",
      "Past parenting experience and knowledge of childcare, their attitude toward discipline, and their ability to give nurturing care and supervision in an atmosphere of affection and moral and material security."
    ]
  },
  {
    "key": "b11_employment_finances",
    "block": "B",
    "number": "XI",
    "title": "Employment history and financial resources",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "The employment history, with the reasons they moved or changed work.",
      "A short description of income, savings, investments, expenditures, liabilities and insurance."
    ]
  },
  {
    "key": "b12_home_community",
    "block": "B",
    "number": "XII",
    "title": "Description of home and community",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "The family's home and the child's accommodation; membership and participation in community organizations, projects and activities.",
      "Community resources and facilities for children, such as hospitals, clinics and schools.",
      "Peace and order in the community and the degree of racial tolerance, if applicable, and how these may affect the child's adjustment."
    ]
  },
  {
    "key": "b13_identity",
    "block": "B",
    "number": "XIII",
    "title": "Social, ethno-cultural, linguistic and religious identity of the PAPs",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Spiritual, philosophical, moral or religious beliefs, affiliations and practices."
    ]
  },
  {
    "key": "b14_health_history",
    "block": "B",
    "number": "XIV",
    "title": "Health history",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Any serious illness, physical disability or history of mental illness. A medical report on the family's health status and history should be discussed.",
      "The psychological evaluation belongs in this box: the psychometric tests done, the results of the tests and clinical interviews, the assessment summary and the psychologist's recommendations."
    ]
  },
  {
    "key": "b15_references_clearances",
    "block": "B",
    "number": "XV",
    "title": "Character references and clearances",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "A summary of the character references, including the clearances showing good moral character."
    ]
  },
  {
    "key": "b16_trainings",
    "block": "B",
    "number": "XVI",
    "title": "Adoption trainings and forums attended or completed",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "For adopters who have had pre-adoption training: the title, the number of hours, the topics discussed and what they learned."
    ]
  },
  {
    "key": "b17_adoption_telling",
    "block": "B",
    "number": "XVII",
    "title": "Adoption telling and post-adoption issues",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "How they plan to tell the child the adoption story.",
      "Their opinion on adoption search and reunion with the birth family."
    ]
  },
  {
    "key": "c1_placement",
    "block": "C",
    "number": "I",
    "title": "Placement history",
    "kind": "placement",
    "columns": [],
    "may_be_na": false,
    "applies": "placement_history",
    "hints": [
      "Covers the period from entrustment, through supervised trial custody if any, up to before the petition is filed.",
      "Not asked for an Adult adoption, or for a Relative adoption of a child who was in the adopters' custody for more than two years.",
      "The age at entrustment is worked out from the date of birth."
    ]
  },
  {
    "key": "c2_on_placement",
    "block": "C",
    "number": "II",
    "title": "Child upon placement",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "Describe the child upon placement or entrustment, or when the adopters took custody.",
      "A summary statement of the child's overall development: physical and health, social, emotional and cognitive."
    ]
  },
  {
    "key": "c3_stc_report",
    "block": "C",
    "number": "III",
    "title": "Supervised trial custody report",
    "kind": "prose",
    "columns": [],
    "may_be_na": true,
    "applies": "stc",
    "hints": [
      "A summary statement on the placement, including issues and concerns and how the child and the family adjusted to each other.",
      "Asked for a Regular, IP or Foster-Adopt adoption. Tick Not applicable if there was no supervised trial custody."
    ]
  },
  {
    "key": "c4_measurements",
    "block": "C",
    "number": "IV",
    "title": "Current functioning: height and weight",
    "kind": "measurements",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "The child's current height and weight, and the date they were measured."
    ]
  },
  {
    "key": "c4_functioning",
    "block": "C",
    "number": "IV",
    "title": "Current functioning",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "How the child is doing now: emotional, social, psychological and cognitive functioning after placement.",
      "Emotional and social: how the child relates to the adoptive parents and siblings, extended family, other children, caregivers and strangers, and whether the child has formed an attachment to a specific person.",
      "What makes the child happy or sad, what the child does when frustrated or angry, whether the child can regulate emotions and express feelings freely, and how the child is pacified.",
      "Which discipline works best, any behavioral concerns, and how the adoptive parents handle the behavior.",
      "The child's personality, hobbies and interests.",
      "Psychological: strengths and weaknesses, confidence and self-esteem, how the child deals with frustration, and the ways the child learns.",
      "Educational: grade level, academic performance, the subjects where the child excels or needs help, and the teacher's view. The education level is shown from the record.",
      "For a child in play, occupational or speech therapy: the progress made.",
      "How the adoptive parents support the child's overall growth, and the degree of attachment and bonding."
    ]
  },
  {
    "key": "c5_assessment",
    "block": "C",
    "number": "V",
    "title": "Assessment",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "A summary statement on the progress of the placement and the overall changes to the adopters' family since the placement."
    ]
  },
  {
    "key": "c6_recommendation",
    "block": "C",
    "number": "VI",
    "title": "Recommendation",
    "kind": "prose",
    "columns": [],
    "may_be_na": false,
    "applies": "always",
    "hints": [
      "A summary statement of the social worker's recommendation on the placement, such as the issuance of an Order of Adoption or its denial."
    ]
  }
];

// The adoptive parents' table, in the template's order (box B.I): the same 17
// rows down a Female PAP and a Male PAP column. The ids are stored in the saved
// value and never renamed.
export const PAP_ROWS = [
  {
    "id": "full_name",
    "label": "Complete name (first, middle and last name)"
  },
  {
    "id": "date_of_birth",
    "label": "Date of birth"
  },
  {
    "id": "place_of_birth",
    "label": "Place of birth"
  },
  {
    "id": "religion",
    "label": "Religion"
  },
  {
    "id": "civil_status",
    "label": "Civil status (date and place of marriage, if applicable)"
  },
  {
    "id": "ethnicity_citizenship",
    "label": "Ethnicity / citizenship"
  },
  {
    "id": "languages",
    "label": "Language/s spoken"
  },
  {
    "id": "education",
    "label": "Education"
  },
  {
    "id": "occupation",
    "label": "Occupation"
  },
  {
    "id": "mobile_phone",
    "label": "Mobile phone"
  },
  {
    "id": "home_phone",
    "label": "Home phone number (landline)"
  },
  {
    "id": "work_phone",
    "label": "Work phone (if applicable)"
  },
  {
    "id": "email",
    "label": "E-mail address (if applicable)"
  },
  {
    "id": "employer",
    "label": "Employer"
  },
  {
    "id": "employer_address",
    "label": "Employer address and contact details"
  },
  {
    "id": "monthly_income",
    "label": "Monthly income"
  },
  {
    "id": "other_income",
    "label": "Other sources of income"
  }
];

export const sectionByKey = (key) => SCSR_SECTIONS.find((entry) => entry.key === key);

/* Blocks B (the prospective adoptive parents) and C (the placement) are an
 * adoption's: they apply to an Adoption record only (owner, 10 Oct 2026).
 * Block A is the child's profile for every case type. The same constants as
 * ADOPTION and ADOPTION_ONLY_BLOCKS in backend/case_study/sections.py. */
export const ADOPTION = 'Adoption';
export const ADOPTION_ONLY_BLOCKS = ['B', 'C'];

/* Does this box apply to this child? `child` is the child record as the API
 * gives it (case_type, case_category, type_of_adoption) and `caseStudy` the
 * case study header (custody_over_two_years), which may be null before one is
 * started. The same rules as `applies()` in backend/case_study/sections.py. */
export function appliesTo(entry, child, caseStudy) {
  if (ADOPTION_ONLY_BLOCKS.includes(entry.block) && child.case_type !== ADOPTION) return false;
  switch (entry.applies) {
    case 'surrendered':
      return child.case_category === 'Surrendered';
    case 'abandoned':
      return ['Abandoned', 'Without Known Parents'].includes(child.case_category);
    case 'stc':
      return ['Regular', 'IP', 'Foster-Adopt'].includes(child.type_of_adoption);
    case 'placement_history':
      // Not for an Adult adoption, nor for a Domestic Relative one when the
      // child had been in the adopter's custody for more than two years. An
      // unanswered question does not hide it.
      if (child.type_of_adoption === 'Adult') return false;
      return !(child.type_of_adoption === 'Domestic Relative'
        && caseStudy?.custody_over_two_years === true);
    default:
      return true;
  }
}
