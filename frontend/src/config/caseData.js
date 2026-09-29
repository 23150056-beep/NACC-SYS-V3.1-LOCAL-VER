// Company-approved reference lists for child records.
//
// Case types and case categories are now sourced from the official
// NACC-SAMD-GF-000 (June 2025) certification tool. The location lists below
// are STILL PLACEHOLDER VALUES pending confirmation from NACC / RACCO I. The
// The Custodian - who the child lives with now - is typed in, not picked; it
// was "Previous Custodian" until 29 Sep 2026 (backend children/custodian.py).
// V2: "Adoption" included per the psychologist interview ("active/adoption,
// active/foster care"); final list pending RACCO I confirmation. This
// placement-track list is now corroborated by NACC-SAMD-GF-000 KRA III
// (transition strategies: adoption, kinship/foster care, family
// reunification, independent living).

export const CASE_TYPES = [
  'Adoption',
  'Foster Care',
  'Kinship Care',
  'Residential Care',
  'Family Tracing & Reunification',
  'Independent Living',
];

// "Category" per the agency's official "I. Identifying Information" intake
// form (2026-07 revision) — replaces the earlier, broader NACC-SAMD-GF-000
// 18-item Service-Users list. Must match backend Child.CASE_CATEGORY_CHOICES;
// backend/children/tests/test_intake.py compares the two.
export const CASE_CATEGORIES = [
  'Surrendered',
  'Abandoned',
  'Dependent',
  'Neglected',
  'Without Known Parents',
  'Orphaned',
];

// Not every category applies to every track. A child being reunified with
// family or moving to independent living is not "Surrendered" or "Abandoned"
// in the sense the intake form means — those describe how a child entered
// residential care, not how they are leaving it.
export const CASE_CATEGORY_OPTIONS = {
  Adoption: CASE_CATEGORIES,
  'Foster Care': CASE_CATEGORIES,
  'Kinship Care': CASE_CATEGORIES,
  'Residential Care': CASE_CATEGORIES,
  'Family Tracing & Reunification': ['Dependent', 'Neglected', 'Without Known Parents', 'Orphaned'],
  'Independent Living': ['Dependent', 'Neglected', 'Without Known Parents', 'Orphaned'],
};

/* The Category comes first on the form, so the pairing runs both ways: a
 * category picked first narrows the case types to the ones that offer it. A
 * category no longer on the list (a retired value on an old record) narrows
 * nothing. */
export const caseTypesFor = (category) => (
  CASE_CATEGORIES.includes(category)
    ? CASE_TYPES.filter((t) => CASE_CATEGORY_OPTIONS[t].includes(category))
    : CASE_TYPES
);

/* Which of the optional case fields each track actually asks for.
 *
 * One map rather than the separate show-this / clear-that lists V2 kept, which
 * had drifted apart: Residential Care preserved a custodian the form
 * never showed, and Family Tracing showed the field but wiped the value the
 * moment you selected it. Deriving both the rendering and the clearing from
 * this map means they cannot disagree again.
 *
 * The lists follow what V2 *displayed*, since that is the behaviour staff saw.
 * Whether Residential Care should also record a custodian is a
 * question for RACCO I, not one to settle by reading old code. */
export const CASE_TYPE_FIELDS = {
  Adoption: ['custodian_name', 'type_of_adoption'],
  'Foster Care': ['custodian_name'],
  'Kinship Care': ['custodian_name'],
  'Family Tracing & Reunification': ['custodian_name'],
  'Residential Care': [],
  'Independent Living': [],
};

/* Which date the case records — never both. A child the agency took in is
 * dated from the admission; a child placed with a custodian, from the
 * placement. Within Adoption only a Regular adoption is an admission, so the
 * date waits for the Type of Adoption. Mirrors date_field_for in
 * backend/children/intake.py. */
export const ADMISSION = 'date_of_admission';
export const PLACEMENT = 'date_of_placement_to_custodian';
export const dateFieldFor = (caseType, typeOfAdoption) => {
  if (caseType === 'Adoption') {
    if (!typeOfAdoption) return null;
    return typeOfAdoption === 'Regular' ? ADMISSION : PLACEMENT;
  }
  if (['Foster Care', 'Kinship Care', 'Family Tracing & Reunification'].includes(caseType)) return PLACEMENT;
  if (['Residential Care', 'Independent Living'].includes(caseType)) return ADMISSION;
  return null;
};

/* The date a case started, as [label, value]: the one the case records, or on
 * an older record whichever of the two it holds. Mirrors intake_date in
 * backend/children/intake.py. */
export const caseDate = (child) => {
  const rule = dateFieldFor(child.case_type, child.type_of_adoption);
  let field = rule;
  if (!rule || !child[rule]) {
    if (child[ADMISSION]) field = ADMISSION;
    else if (child[PLACEMENT]) field = PLACEMENT;
  }
  field = field || ADMISSION;
  return [field === PLACEMENT ? 'Date of placement' : 'Date of admission', child[field] || null];
};

/* What the Add Record form will not save without (since 24 Sep 2026). Middle
 * name, legal status, landmark and date found are left out on purpose: a
 * foundling may have no middle name, a child new to care may have no legal
 * status yet, and most addresses have no landmark. Mirrors required_fields in
 * backend/children/intake.py, which refuses the same blanks. */
export const ALWAYS_REQUIRED = [
  'case_category', 'case_type',
  'first_name', 'last_name', 'birth_date', 'gender',
  'place_of_birth_or_found', 'birth_status', 'education_level',
  'house_number', 'street', 'province', 'municipality', 'barangay',
];
export const requiredFields = (caseType, typeOfAdoption) => {
  const date = dateFieldFor(caseType, typeOfAdoption);
  return [...ALWAYS_REQUIRED, ...(CASE_TYPE_FIELDS[caseType] || []), ...(date ? [date] : [])];
};

/* Answered by the case type, so asked again whenever the case type or the type
 * of adoption changes: an edit that changes either is refused with one of
 * these blank, even if the record had it blank before. Mirrors DYNAMIC in
 * backend/children/intake.py (test_intake.py pins the two). */
export const DYNAMIC = ['custodian_name', 'type_of_adoption', 'date_of_admission', 'date_of_placement_to_custodian'];

/* The answers a case of this type does not ask for, blanked - for what a save
 * SENDS, never for the form. The form keeps an answer when the case type
 * changes and only hides it, so switching back brings it back; until 29 Sep
 * 2026 it deleted them on the spot, and Foster Care -> Residential Care ->
 * Foster Care lost the custodian, their confirmed number, the texting consent
 * and the date of placement. */
export const unaskedBlanked = (caseType, typeOfAdoption) => {
  const asked = CASE_TYPE_FIELDS[caseType] || [];
  const adoption = asked.includes('type_of_adoption') ? typeOfAdoption : '';
  const date = dateFieldFor(caseType, adoption);
  return {
    ...(asked.includes('type_of_adoption') ? {} : { type_of_adoption: '' }),
    ...(asked.includes('custodian_name') ? {} : {
      custodian_name: '', custodian_contact: '', custodian_sms_consent: false,
    }),
    ...(date === ADMISSION ? {} : { [ADMISSION]: null }),
    ...(date === PLACEMENT ? {} : { [PLACEMENT]: null }),
  };
};

// The type of adoption where the case type asks it, else none.
const askedAdoption = (caseType, typeOfAdoption) => (
  (CASE_TYPE_FIELDS[caseType] || []).includes('type_of_adoption') ? (typeOfAdoption || '') : '');

/* Whether an edit changes what the case asks - the case type, or the type of
 * adoption where one is asked. A type of adoption picked while the case was
 * briefly an Adoption, then left hidden, changes nothing. */
export const caseChanged = (form, record) => form.case_type !== record.case_type
  || askedAdoption(form.case_type, form.type_of_adoption)
    !== askedAdoption(record.case_type, record.type_of_adoption);

/* What a save sends for the answers the final case type does not ask: blank
 * where the case changed (and on a new record), else exactly what the record
 * held - an answer typed while the case type was briefly something else is
 * not kept for a question that is not asked, and an older record's hidden
 * values go back as they came. */
export const unaskedAnswers = (form, record) => {
  const blanked = unaskedBlanked(form.case_type, form.type_of_adoption);
  if (!record || caseChanged(form, record)) return blanked;
  return Object.fromEntries(Object.keys(blanked).map((k) => [k, record[k] ?? blanked[k]]));
};

// New fields from the same official intake form. "N/A" became "Unknown" and
// "Child" was retired on 24 Sep 2026.
export const BIRTH_STATUSES = ['Marital', 'Non-Marital', 'Unknown'];

export const LEGAL_STATUSES = [
  'With Issued CDCLAA',
  'With IVC',
  'Judicially Declared Abandoned',
];

// Who referred the child (owner's list, 24 Sep 2026). Must match backend
// Child.REFERRAL_SOURCE_CHOICES; a record still holding typed text keeps it.
export const REFERRAL_SOURCES = ['RACCO', 'LGU', 'CCA', 'RCF'];

// SIBRA and ICA Relative were retired on 24 Sep 2026. A record that holds one
// keeps it, and the form still shows it on that record.
export const TYPES_OF_ADOPTION = [
  'Regular',
  'Domestic Relative',
  'Relative (Without 2-yr custody)',
  'Step-parent',
  'Adult',
  'IP',
  'Foster-Adopt',
];

// Derived 5-state pre-assessment pipeline status, in pipeline order (must
// match backend Child.pre_assessment_status). Drives the filter chips and
// sorting on the Pre-Assessment child picker plus status badges elsewhere.
export const PA_STATUSES = [
  'No Consent Yet',
  'Not Yet Pre-Assessed',
  'In Progress',
  'Answered',
  'Completed',
];

// Badge tone per pipeline status (ui Badge tones).
export const PA_STATUS_TONES = {
  'No Consent Yet': 'danger',
  'Not Yet Pre-Assessed': 'neutral',
  'In Progress': 'amber',
  Answered: 'brand',
  Completed: 'success',
};

// Why a case is closed - two lists since 28 Sep 2026 (backend
// children/termination.py). The ISA closes for where the child went; must
// match TerminationRecord.CASE_OUTCOMES.
export const TERMINATION_REASONS = [
  'Reunified with family',
  'Adoption finalized',
  'Transferred to another agency',
  'Aged out of program',
  'Services completed',
  'Other',
];

// A psychologist closes for where the clinical work ended, and the server
// offers each only when the record bears it out. Must match
// TerminationRecord.CLINICAL; test_closure_reasons.py pins both.
export const CLINICAL_CLOSURE_REASONS = [
  'Counseling completed',
  'Favorable pre-assessment, no counseling needed',
  'Pre-assessment only, evaluation completed',
  'Counseling discontinued',
  'Referred to another specialist or service',
  'Other',
];

// Every reason a closed case can carry, for filtering the archive.
export const ALL_CLOSURE_REASONS = [...new Set([...TERMINATION_REASONS, ...CLINICAL_CLOSURE_REASONS])];

// Province → Municipality/City → Barangay pickers.
// PLACEHOLDER dataset scoped to Region I (La Union), pending confirmation
// from NACC / RACCO I. Expand per company guidance.

/* Province / municipality / barangay lists used to live here as hand-kept
 * arrays. They are gone: the intake form reads them from the PSGC tables in
 * the `locations` app now, seeded from the Philippine Standard Geographic Code
 * and served over /api/locations/. Region I alone is 125 cities and
 * municipalities and 3,265 barangays — not a list to maintain by hand, and the
 * short version that lived here could not spell most real addresses. */

// PsychologicalReport.TYPE_CHOICES (clinical/models.py), in the same order.
export const REPORT_TYPES = [
  { v: 'initial', label: 'Initial Evaluation' },
  { v: 'progress', label: 'Progress Report' },
  { v: 'final', label: 'Final Report' },
  { v: 'other', label: 'Other' },
];

export const reportTypeLabel = (v) => REPORT_TYPES.find((t) => t.v === v)?.label || v;
