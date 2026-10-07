# Social Case Study Report, Parts II-V — design

**Status:** proposal for the owner. Nothing here is built.
**Source:** `docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx` (blank
NACC template). Part I (Identifying Information) is already the record form,
since 7 Oct 2026; see CLAUDE.md "The record form".

## What the SCSR is

The adoption case report a social worker writes. For Regular, IP and Foster
adoption it is written after the PAPA is issued or the Supervised Trial Custody
is done. For Relative, Step-parent and Adult adoption it is written before the
petition is filed. It is mostly prose: about 35 sections with
paper-form instructions, plus five tables. It ends with a recommendation that
the head of office approves.

## What the template asks for, after Part I

| Part | Section | Shape | Applies when |
|---|---|---|---|
| II | Sources of information | numbered list | always |
| II | Circumstances of referral/admission | prose, past tense | always |
| III | Description of the child on admission | prose, includes anthropometrics | always |
| III | Medical history before placement | prose, including birth details, newborn screening, AOG, Ballard, APGAR | AOG, Ballard and APGAR only if born in a hospital |
| III | Psychological evaluation highlights | prose | child 5 years or older |
| III | Immunizations | **table**: vaccine, date, place | always |
| III | Developmental history and milestones | prose | always |
| III | Toilet training | prose | young children |
| III | Activities | prose | always |
| III | Birth family composition | **table**: name, relationship, age, sex, civil status, education, employment/income | "if applicable" |
| III | Birth family description | 10 prompts in prose, covering physical, health, prenatal, personality, dynamics, abuse history, treatment | "if applicable" |
| III | Termination of parental rights | prose, plus the DVC signed date, the DVC notarized date, counselling dates and two required statements | Surrendered |
| III | Facts of abandonment | prose, plus the search efforts and their dates (media certification, publication, blotter, registered mail, home visit) | Abandoned, Without Known Parents |
| III | CDCLAA summary and issuance date | prose and a date | where a CDCLAA applies |
| IV | PAP identifying information | **table**, one column per PAP: 17 rows including income, phones and employer | always |
| IV | Household of the PAPs | **table**: name, age, relationship, education, occupation, disability | always |
| IV | 16 narrative sections | prose | always; "children in the family" and "other individuals" only if applicable |
| V | Placement history | **table**: matching date, RACCO/CPA, date accepted, date of entrustment, age at entrustment | **not** Adult, and not Relative with more than 2 years' custody |
| V | Child on placement, STC report, current functioning (incl. height and weight) | prose | STC "if applicable" |
| V | Assessment, Recommendation | prose | always |
| — | Prepared by: SW name, **license number and validity**. Approved by: head of office | signature block | always |

## Proposed design

### 1. One SCSR per child, a document with a status

A new app called `case_study`, never `adoption`. The `adoption` name is free
again, but reusing it invites confusion with the tracker that was removed on
17 Sep. The app holds one model, `CaseStudy`:

- `child` is one-to-one with the child, and only for case type Adoption.
- `status` is one of Draft → Submitted → Approved, or Returned with a note.
- `sections` is a JSON object with one key per section id. A table section
  holds a list of rows.
- It also holds `prepared_by`, `submitted_at`, `approved_by`, `approved_at`,
  `return_note`, and `part_one_snapshot` (frozen at approval).

The sections are stored as JSON rather than about 40 columns and three child
tables. Nothing ever queries "every child whose APGAR was…", and the shape is a
paper form that will change when the agency revises it. Each JSON change is
additive and needs no column migration. That avoids the 0020 deploy trap
altogether.

Validation does what the database cannot. The section list lives in
`case_study/sections.py` as `SCSR_SECTIONS`: id, part, title, kind
(prose / list / table / date), the columns for a table, and an `applies(child)`
rule. The browser copy lives in `config/scsr.js`, and a test pins the two
together. This is the same arrangement as `intake.py` and `caseData.js`. The
server refuses unknown keys, malformed rows and future dates, so a JSON field
is not a free-for-all.

### 2. Who writes, who reads

- **The child's SW writes it.** It is their report, and it follows
  `Child.social_worker`: when the ISA moves the record, the draft moves with
  it.
- **The ISA reads every SCSR, can write any, and approves.** The template's
  "Head of Office" is assumed to be the ISA. *(Owner to confirm.)*
- **Psychologists do not see it.** Part IV holds two adults' incomes, phone
  numbers and employers, which a psychologist has no use for. The one thing
  they contribute, the psychological evaluation highlights, is pre-filled from
  their confirmed report (see 3), so they need not open the document.
  *(Owner to confirm.)*
- Every write goes through `useConfirm()`. Each save, submit, approval and
  return is an activity event.

### 3. Filled from what is already recorded, never re-typed

- **Part I is not a copy.** The report shows the record's Part I live, with a
  link to edit the record, until approval freezes it into `part_one_snapshot`.
  An approved report must keep printing what was approved even after the
  record changes.
- **Pre-filled once, then the SW's text:**
  - Sources of information: the referral source and the date of the case
    referral.
  - Psychological evaluation highlights: the confirmed summary of the latest
    psychological report, or nothing.
  - Age at entrustment: computed from the birth date and the date of
    entrustment, and never typed.
  - The CDCLAA issuance date: the `legal_status_date` already on the record.

  A pre-fill is a starting text the SW edits. It does not change afterwards
  if the source changes.
- **The custodian on the record is the PAP for adoptions.** The demo already
  does this. A one-click "Use the custodian's name" fills in the first PAP
  column. The custodian is not otherwise linked.

### 4. Sections that do not apply are hidden, never deleted

`applies(child)` drives this, the same rule as the record form:

- Termination of parental rights shows for Surrendered.
- Facts of abandonment shows for Abandoned or Without Known Parents.
- Placement history is hidden for Adult. It is shown for "Relative (Without
  2-yr custody)", which by its name had no long custody. For "Domestic
  Relative" it depends on whether the PAPs had the child for more than two
  years, which the record does not hold (question 6).
- Psychological evaluation highlights shows from age 5.
- Hospital birth details (AOG, Ballard, APGAR) show behind a "Born in a
  hospital?" yes/no.

A change of category keeps the text that was written. The text is just not
shown and not printed.

### 5. Drafts save anything; Submit checks

A draft saves with any section blank, because the report is written over
weeks. **Submit** refuses until every section that applies has content. It
lists the missing sections by name, the same "Needed before saving" pattern as
the record form. The past-tense instructions and the "no graphic details" rule
appear as hints beside the box and are not checked.

The ISA can **Approve**, which locks the report and freezes Part I, or
**Return** it with a note, which sends it back to Draft. An approved report is
revised by **Revise**, which starts a new draft from it. The approved version
stays printable until the new one is approved.

### 6. Printing

`components/ScsrPrint.jsx` prints in the template's order and headings:

- a section that does not apply is left out;
- a blank section that applies prints as lines to complete by hand, as
  `PsychReportPrint` does;
- tables print with their empty rows;
- the signature block prints the SW's name, license number and validity.

**Users have no license field today.** This needs `UserProfile.license_number`
and `license_valid_until`, as an additive migration, edited on `/profile`.
*(Owner to confirm that the SWs hold and want these shown.)*

### 7. Out of scope

- **No AI drafting.** The model drifts on Taglish, these are records about a
  child and two adults, and a hosted deployment refuses drafting anyway.
- **No upload of a filled SCSR.** The agency's own Word copies stay case
  referrals or reports.
- **No matching or PAPA workflow.** That was the adoption tracker, removed on
  17 Sep at the owner's request.

## Build order

Each phase ships on its own, with tests, lint, build and a browser check.

1. **Skeleton, Part II and Part III:**
   - the model, `SCSR_SECTIONS` with its pinning test, and the API;
   - a "Case study" tab on the child's page (Adoption only, SW and ISA);
   - the section editor, the tables, the applies-rules and the pre-fills;
   - the print.
2. **Part IV**, the PAPs: the two-column identifying table, the household
   table and the sixteen prose sections.
3. **Part V**, then Submit, Approve, Return and Revise, the Part I snapshot,
   and the license fields on the profile and in the signature block.
4. **Demo data:** a few seeded SCSRs at each status, fictional throughout, with
   no phone numbers or emails, for the reason the demo custodians have none.

## Questions for the owner before phase 1

1. Is the ISA the "Head of Office" who approves? If not, who is, in the
   system?
2. Psychologists: no access at all (proposed), or read-only on Parts II-III?
3. Is it one SCSR per child? Siblings placed with the same PAPs would each
   carry Part IV. Proposed: yes, one per child, with "Copy Part IV from
   another child's case study" added later if it proves tedious.
4. License number and validity on the SW's profile: yes?
5. Is there a filled, redacted SCSR the agency can share? The headings come
   from the blank template. How long each section really runs, and which
   "if applicable" ones are usually skipped, can only be seen in a real one.
6. "Domestic Relative": did the PAPs have the child for more than two years?
   Proposed: a yes/no on the case study that hides Placement History, rather
   than a new adoption type.
