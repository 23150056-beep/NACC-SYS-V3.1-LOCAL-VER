# Social Case Study Report on the child's record — design

**Status:** agreed with the owner on 8 Oct 2026; built in phases (below).
**Source:** `docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx`, the
blank NACC template. Its Part I (Identifying Information) has been the record
form since 7 Oct 2026; see CLAUDE.md "The record form". This document covers
the rest of it.

The owner's aim, in his words: put the SCSR "digitally on the child record
module". So it lives **inside the child's record**, on a Case Study tab, and
prints from there. It is not a file to export: no Word download is planned.

## The owner's decisions (8 Oct 2026)

1. **The ISA is the agency's IT support, not the Head of Office.** Nobody in the
   system approves an SCSR. The Head of Office signs the printed copy.
2. **Psychologists read block A**, drafts included: the child, the birth
   family, and the termination or abandonment facts. They never see block B
   (the adoptive parents) or block C (the placement).
3. **There is one case study per child.**
4. **The license number and validity come from the template's signature
   block:** "(Name of Social Worker) (License Number and Validity Date)".
   They are kept on the SW's profile.
5. **A SW's SCSR work is their own**, the same reasoning as names on the
   calendar. Other SWs cannot see it, and the ISA sees status only.
6. **Domestic Relative:** a yes/no asks "custody for more than two years?".
   A yes hides Placement History.
7. **AI summaries stay "confirmed only".** Nothing in this feature reads a
   draft summary.
8. **Briefs.** SWs get a "Case brief", facts first; a model-written part
   follows after `ai_eval` on the owner's PC. The ISA gets facts only.

## Numbering follows the template

Section ids and the print use the template's own numbering: three lettered
blocks, with the roman numbers restarting in each. (An earlier draft of this
document renumbered them "Parts II-V"; that was wrong.)

The header carries **Date prepared**. Every age on the report is worked out
as of that date, never as of today.

### A. The Child/Adoptee

| Id | Template heading | Shape |
|---|---|---|
| — | I. Identifying Information | read from the record, frozen at Final |
| `a2_sources` | II. Sources of information | ordered list of people and documents consulted |
| `a2_circumstances` | II. Circumstances of referral/admission | prose; seed: referral source, `referral_reason`, date of the case referral |
| `a3_description` | III. Description on admission/entrustment | prose (anthropometrics in the text) |
| `a3_medical` | III. Medical history before placement | prose; seed: `medical_notes`, `special_needs` |
| `a3_psych_highlights` | III. Highlights of the psychological evaluation | prose; seed: the latest psychological report's CONFIRMED summary the SW can see |
| `a3_immunizations` | III. Immunizations table | rows: vaccine, date (year, year-month or full date), place |
| `a3_development` | III. Developmental history | prose, including toilet training and activities |
| `a4_family_composition` | IV. Family composition | rows: name, relationship, age, sex, civil status, education, employment/income; may be Not applicable |
| `a4_family_description` | IV. Family description | prose; may be Not applicable |
| `a5_summary` | V. Summary, CDCLAA application | prose, always. The CDCLAA date is shown from the record (`legal_status_date`) when the legal status is "With Issued CDCLAA" |
| `a5_dvc_signed`, `a5_dvc_notarized` | V. DVC signed / notarized | dates; notarized not before signed. **Surrendered** only |
| `a5_counselling` | V. Counselling before, during and after the DVC | rows: date, stage (before/during/after), goals. Surrendered |
| `a5_assistance` | V. Assistance to the birth parents; efforts to place with relatives or prevent surrender | prose. Surrendered |
| `a5_aware_irrevocable`, `a5_explained_vernacular` | V. The two required statements | ticks that print the template's sentences; both required to finalize. Surrendered |
| `a5_abandonment` | V. Facts of abandonment | prose. **Abandoned, Without Known Parents**. Date found, place found and age when found are shown from the record |
| `a5_search_efforts` | V. Efforts to locate the birth family | rows: kind (media certification, newspaper publication, blotter, registered mail, home visit, other), date, note. Abandoned, Without Known Parents |

### B. The Prospective Adoptive Parents

| Id | Template heading | Shape |
|---|---|---|
| `b1_paps` | I. Identifying information | fixed 17 rows × Female PAP / Male PAP. Either column may be empty (single adopter, step-parent); at least one must be named to finalize. "Use the custodian's name" fills the first name |
| `b2_household` | II. Family composition and others living with the PAPs | rows: name, age, relationship, education, occupation, disability/sickness |
| `b3_family_background` … `b17_adoption_telling` | III-XVII | prose, one box per numbered heading. VI marital history, VII children in the family and VIII other individuals may be Not applicable. The PAPs' psychological evaluation is part of XIV, not its own box |

### C. Adoption Placement

| Id | Template heading | Shape |
|---|---|---|
| `c1_placement` | I. Placement history | matching date (optional), RACCO/CPA name, date accepted (optional), date of entrustment; in that order. Age at entrustment is computed. Hidden for **Adult**, and for **Domestic Relative** with custody over two years |
| `c2_on_placement` | II. Child upon placement | prose |
| `c3_stc_report` | III. Supervised trial custody report | prose. **Regular, IP, Foster-Adopt** only (template preamble); may be Not applicable |
| `c4_measurements` | IV. Current functioning: height and weight | cm, kg, date measured (not in the future) |
| `c4_functioning` | IV. Current functioning | prose; education level shown from the record |
| `c5_assessment` | V. Assessment | prose |
| `c6_recommendation` | VI. Recommendation | prose |

**Guidance, not fields.** The template's bullet points show beside each box as
a checklist, and the past-tense and "no graphic details" rules as hints. None
of them is checked. When the agency rewords a bullet, no id changes.

**Not applicable.** Every "(if applicable)" section has a Not applicable
tick. It prints "Not applicable." and counts as answered.

**Domestic Relative.** `custody_over_two_years` is asked for this type only.
It is pre-answered from `date_of_placement_to_custodian` against Date
prepared, and the SW can change the answer.

## Storage

The work goes in a new app, **`case_study`** (never `adoption`; see CLAUDE.md
"Removed").

- **`CaseStudy`**, one per child (case type Adoption only):
  - status: `draft` or `final`;
  - `date_prepared`;
  - `custody_over_two_years` (null/yes/no);
  - `created_at`, `updated_at`.
- **`CaseStudySection`**, one row per section, unique on (case_study, key):
  - `value` (JSON), `not_applicable`;
  - `version` (int), `updated_by`, `updated_at`.

  A new section needs no migration. **Keys are never renamed or reused.** The
  pinning test keeps a frozen list of every key ever issued: a JSON key rename
  is the 0020 trap in another form.
- **`CaseStudyFinal`**, immutable, one row per finalization:
  - the sections;
  - Part I as the record held it;
  - the preparer's name, license number and validity, from `UserProfile`;
  - the agency name, address and Head of Office, from `AgencyProfile`;
  - `finalized_by`, `finalized_at`.

  Reprints show what was final that day, even after a license renewal or a
  record edit.

**The catalogue lives in two places:**
- `case_study/sections.py` holds `SCSR_SECTIONS`: id, block, number, title,
  kind (prose / list / table / pap_table / date / tick / measurements /
  placement), columns, may-be-N/A, and `applies(child, case_study)`.
- `frontend/src/config/scsr.js` holds the browser's copy.

A test pins the two together, the same arrangement as `intake.py` and
`caseData.js`.

**The server validates every save:**
- unknown keys are refused;
- each kind has its own shape;
- prose is at most 20,000 characters;
- lists and tables have at most 50 rows;
- dates are not in the future, except the license validity;
- DVC notarized is not before signed;
- matching ≤ accepted ≤ entrustment;
- height is 30-250 cm and weight 2-250 kg.

## Who sees what

All of this lives in `case_study/access.py`, NOT in the clinical base class.
There, "Administrators see everything" would hand IT support every SW's case
studies, the adoptive parents' incomes included.

| Who | Draft | Final |
|---|---|---|
| The record's SW (`child.social_worker`) | read, write | read, print, Reopen |
| The assigned psychologist (not a pending one) | block A, read only | block A, read only |
| The ISA | status only | status only |
| Anyone else | 404 | 404 |

- **The ISA's status view:**
  - whether a case study exists;
  - its status;
  - the holder, shown as "(inactive)" when archived so a stranded draft can be
    transferred;
  - when it was last edited and when it was finalized;
  - how many applicable sections are still empty.

  No text, no print, no write. The model is **not registered in Django
  admin**, where the seeded ISA is a superuser.
- **Psychologists:** `a3_psych_highlights` is hidden from a psychologist
  reader when the child's history is not carried to them
  (`assignee_sees_history` False). Otherwise the previous psychologist's
  findings would reach them through the SW's text, past the carry-history
  control.
- **Writes are refused** when the child's case type is not Adoption, and when
  the case is terminated. Changing the case type away from Adoption keeps
  the rows; the tab disappears.
- **Transfer:** a transfer by the ISA, or a take-over at reopen, moves the
  case study with the record. Each `CaseStudyFinal` keeps its own preparer.
- **Nothing that reads widely reads it:**
  - no chatbot tool;
  - no assistant prompt, the SW brief included;
  - not the duplicate check;
  - not the Agency Summary.

## Final and Reopen

- **Final:** the SW only.
  - It runs `missing_sections()`: every applicable section has content or is
    ticked Not applicable, the two Surrendered ticks are both ticked, at least
    one PAP is named, the entrustment date is set where Placement History
    applies, and Date prepared is set.
  - The refusal lists the missing sections by title, like the record form's
    "Needed before saving".
  - On success it writes a `CaseStudyFinal` and sets the status to `final`.
- **Reopen:** the SW only. The status goes back to `draft`, the last final
  stays printable, and finalizing again writes a new `CaseStudyFinal`.
- **The Head of Office signs on paper.** There is no approval step in the
  system.
- **Activity events:** only on creation, Final and Reopen, never on a save.
  - They use `entity_type="CaseStudy"` with `entity_id` = the child.
  - They are added to the SW's activity filter.
  - Final is addressed to the assigned psychologist.
  - The label is the child's name, never section text.

## Saving a document written over weeks

- **Saving:**
  - Each section saves on its own: `PUT .../sections/<key>/` with
    `expected_version`. This is a conditional
    `UPDATE ... WHERE version = n`; zero rows updated gives 409 with the
    current value. The screen then offers "Load latest" or "Keep mine",
    because sessions are per tab and two tabs are normal.
  - "Save section" and "Save all changes" each ask `useConfirm()` once. The
    latter lists the changed sections.
- **Unsaved typing:**
  - It is kept in `localStorage` under `nacc-draft:case-study:<user>:<child>:<key>`
    with the base version, and offered back as "Unsaved text from 3:40 PM".
    That is not a save, so nothing asks.
  - Logout also clears the `nacc-draft:` prefix.
  - `beforeunload` warns while anything is unsaved.

## On the child's record

- A **Case Study** tab on the child's page, for Adoption records, shown to
  the SW, the assigned psychologist (block A) and the ISA (status card).
- The page's **Print** button prints the SCSR while that tab is open, and the
  psychological report otherwise. `ScsrPrint` becomes the page's print-only
  element in place of `PsychReportPrint` on that tab; see index.css
  `.racco-psych-print-root`.
- **What `ScsrPrint` contains:**
  - the agency header from `AgencyProfile`;
  - the title and Date prepared;
  - the three blocks in the template's numbering;
  - "Not applicable." where ticked;
  - lines to complete by hand where an applicable section is blank;
  - tables with their empty rows;
  - headings kept with their text.
- **The signature block:**
  - Prepared by: the SW's name, license number and validity;
  - Approved by: a blank line over the Head of Office's name and title, or a
    blank line where Settings has none.
- **A draft prints marked DRAFT; a final prints its snapshot.** Only the SW
  prints.

## Demo data

Demo data ships with each phase and satisfies the same rules
(`missing_sections()` for a seeded Final).

- `seed_demo_data` gives some adoption children a draft and some a final.
  All of it is fictional, and Part B has **no phone numbers or emails**,
  for the reason demo custodians have none.
- The three models join `DEMO_MODELS`, and `rehome_people` maps their user
  fields to the child's SW.
- `import_demo_data` strips any phone or email in `b1_paps`.

## Phases

- **P0 (built 8 Oct):** groundwork the rest relies on.
  - `AgencyProfile`, edited in Settings by the ISA;
  - license number and validity on `/profile` (Staff and Psychologists);
  - the Case brief: facts for SWs and the ISA, with the written brief
    becoming the psychologist's only.
- **P1:** the app, the catalogue (every block's ids fixed now), the API,
  access, the tab, block A's editor, saving and conflicts, psychologist read,
  the ISA status card, print of block A plus the signature, and demo drafts.
- **P2:** block B and block C's editors, the Domestic Relative question,
  Final and Reopen with snapshots, activity events, the full print, and demo
  finals.
- **P3:** the SW brief's written part. It is built and tested here with the
  model faked, and switched on only after `ai_eval --feature case_brief` on
  the owner's PC and his reading of samples. It reads no remarks, no
  self-report words, no case study text and no unconfirmed summary.

## Later, if wanted

Block A (background, medical history, immunizations, development, family) is
useful for every case type, not only adoptions. Extending the tab to Foster
Care and the others would make it the child's profile everywhere, with the
SCSR print still Adoption-only.
