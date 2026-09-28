# Interview Templates by Upload, and Closure Reasons a Psychologist Can Stand Behind

**Date:** 2026-09-28
**Status:** Built on `claude/ai-feature-access-control-xuieuz`
**Owner's request:** "On the part of Pre-Assessment: Interview page - add an
upload template to easy access on utilizing this page. Fix the psychologist
terminate case reason dropdown, to base on council activity if done or didnt
need to council, or good pre assessment, counseling complete brainstorm this
part to make an appropriate drop down on the psychologist side only"

## 1. Upload a template on the Interview step

### What happens today

Step 3 of the pre-assessment (Clinical interview) offers a dropdown of the
psychologist's Clinical Interview form templates. A template is a list of
fields — section headings and questions — and the only way to make one is on
another screen (Pre-Assessment Instruments → Agency forms), typing every
question into its own row. The agency's real interview form,
`docs/agency-forms/Pre-assessment.docx`, has two questionnaires and about a
hundred questions. Nobody is going to type that in, so in practice the
dropdown is empty and the step gets skipped.

### What changes

An **Upload a template** button beside the dropdown. The psychologist picks
their Word (or PDF) interview form; the server reads it and hands back a
draft; a review dialog shows what was found; one save later the form is in
the dropdown and already selected, on the page they were using.

- **The server reads the file with the reader reports already use**
  (`clinical/services.extract_text`: standard library for .docx, PyMuPDF for
  PDF, headings found from the file itself). Measured on the agency's form, it
  finds every Roman-numbered section and every question line.
- **Turning text into fields** (`clinical/form_import.py`):
  - a heading becomes a *Section heading*;
  - a line that is all capitals once its bracketed note is set aside also
    becomes a section — that is how the form's second questionnaire ("…FOR
    THE CHILD (Some questions may not be answered…)") announces itself, and
    it is too long for the report reader's heading rule;
  - a `Label: ____` line, or a short table cell ending in a colon, becomes
    *Short text* (a *Date* when the label says date);
  - every other line under a section becomes *Long text* — the interview's
    answers are sentences;
  - lines before the first section are the form's introduction, and go to
    the document text shown above the fields;
  - the first heading is offered as the title, and the file name when there
    is none;
  - a question repeated word for word gets "(2)" added, because answers are
    stored against the question's wording and two identical labels would
    share one answer.
- **Nothing is saved by the upload.** It returns a draft. The psychologist
  reviews it, can rename it, retype or remove any field, change a field's
  type, and must tick the same attestation the Instruments page asks for
  ("agency-authored or an official government form, not a published
  assessment instrument"). Saving goes through the existing
  `POST /form-templates/`, so ownership, versioning and the attestation rule
  are the ones that already exist.
- **Who:** the roles that may create a form template (ISA and psychologists).
  A psychologist's upload is their own form, as on the Instruments page.
- **Refused:** `.doc` (Word 97-2003 cannot be read — save it as .docx), files
  over 5 MB, and a file in which no question could be found (the dialog says
  so rather than saving an empty form).

### Considered and not done

- **Keeping the original file for download.** The form prints blank from its
  fields already ("Print blank form"); a stored copy would be a second
  version of the same form that could drift from the first.
- **Uploading on the Instruments page too.** Asked for on the Interview page;
  the review dialog is one component, so a second door is one line if wanted.

## 2. Closure reasons on the psychologist's side

### What happens today

Terminating a case asks for a reason from one list, whoever is closing it:
Reunified with family, Adoption finalized, Transferred to another agency, Aged
out of program, Services completed, Other. Those are **case outcomes** — the
ISA's view of where the child went. A psychologist closes a case for a
**clinical** reason: the counseling is finished, or it was never needed. The
list has no words for either, so it gets "Services completed" or "Other" and
the Agency Summary's count by reason says nothing about the clinical work.

### What changes

A psychologist chooses from a clinical list, and **each reason is offered
only when the record bears it out**. The ISA's list is unchanged.

| Reason | Offered when the record shows |
|---|---|
| Counseling completed | at least one counseling session (Session or Follow-up) marked **completed** on the calendar |
| Favorable pre-assessment, no counseling needed | a **completed** pre-assessment, and no counseling: the case never moved to Counseling and no session was held |
| Pre-assessment only, evaluation completed | the same as above — the referral asked for an evaluation (an adoption psychological evaluation, say), not counseling |
| Counseling discontinued | counseling was started: the case moved to Counseling, or a session was booked or held |
| Referred to another specialist or service | always |
| Other | always |

"If done or didn't need to counsel, or good pre-assessment, counseling
complete" maps onto the first three. The last three cover what the first three
cannot: a child who stops coming, a child who needs a psychiatrist, and
anything else — the closing summary is required whatever is chosen.

- **The rule lives on the server** (`children/termination.py`), and the
  terminate endpoint refuses a reason the record does not support — the same
  "the screen and the endpoint compute it once" rule the calendar follows.
  The dialog asks the server which reasons are open
  (`GET /children/{id}/closure-reasons/`) rather than keeping its own copy.
- **An unavailable reason is shown, greyed, with why** ("No counseling
  session is marked completed on the calendar yet"). Hiding it would leave a
  psychologist wondering where "Counseling completed" went; saying why tells
  them what to record first.
- **The dialog shows what the record says** — pre-assessment status, case
  stage, sessions held — so the choice is made against the facts.
- **Past closures are untouched.** A psychologist's earlier "Services
  completed" stays as it was written; the Records filter and the Agency
  Summary list every reason, old and new.
- **`frontend/src/config/caseData.js` keeps both lists** for the Records
  filter, and a test pins them to the server's.

### Considered and not done

- **Letting the ISA use the clinical reasons.** Asked for on the
  psychologist's side only.
- **Counting treatment-plan status** ("completed") as evidence of finished
  counseling. Plans are free text and optional; a session marked completed is
  the one record every counseling case produces.
