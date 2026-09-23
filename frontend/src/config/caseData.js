// Company-approved reference lists for child records.
//
// Case types and case categories are now sourced from the official
// NACC-SAMD-GF-000 (June 2025) certification tool. SURRENDERED_BY and the
// location lists below are STILL PLACEHOLDER VALUES pending confirmation
// from NACC / RACCO I.
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
// 18-item Service-Users list. Must match backend Child.CASE_CATEGORY_CHOICES.
export const CASE_CATEGORIES = [
  'Surrendered',
  'Abandoned',
  'Dependent',
  'Neglected',
  'Without Known Parents',
  'Orphan',
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
  'Family Tracing & Reunification': ['Dependent', 'Neglected', 'Without Known Parents', 'Orphan'],
  'Independent Living': ['Dependent', 'Neglected', 'Without Known Parents', 'Orphan'],
};

/* Which of the optional case fields each track actually asks for.
 *
 * One map rather than the separate show-this / clear-that lists V2 kept, which
 * had drifted apart: Residential Care preserved a Previous Custodian the form
 * never showed, and Family Tracing showed the field but wiped the value the
 * moment you selected it. Deriving both the rendering and the clearing from
 * this map means they cannot disagree again.
 *
 * The lists follow what V2 *displayed*, since that is the behaviour staff saw.
 * Whether Residential Care should also record a Previous Custodian is a
 * question for RACCO I, not one to settle by reading old code. */
export const CASE_TYPE_FIELDS = {
  Adoption: ['surrendered_by', 'date_of_placement_to_custodian', 'type_of_adoption'],
  'Foster Care': ['surrendered_by', 'date_of_placement_to_custodian'],
  'Kinship Care': ['surrendered_by', 'date_of_placement_to_custodian'],
  'Family Tracing & Reunification': ['surrendered_by', 'date_of_placement_to_custodian'],
  'Residential Care': [],
  'Independent Living': [],
};

// New fields from the same official intake form.
export const BIRTH_STATUSES = ['Marital', 'Non-Marital', 'Child', 'N/A'];

export const LEGAL_STATUSES = [
  'With Issued CDCLAA',
  'With IVC',
  'Judicially Declared Abandoned',
];

export const TYPES_OF_ADOPTION = [
  'Regular',
  'Domestic Relative',
  'Step-parent',
  'Adult',
  'SIBRA',
  'ICA Relative',
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

// Termination reason categories (must match backend TerminationRecord.REASON_CHOICES).
export const TERMINATION_REASONS = [
  'Reunified with family',
  'Adoption finalized',
  'Transferred to another agency',
  'Aged out of program',
  'Services completed',
  'Other',
];

// Adviser: record who surrendered the child to NACC/RACCO I.
// PLACEHOLDER — pending confirmation from NACC / RACCO I.
export const SURRENDERED_BY = [
  'Social Worker',
  'Police',
  'Relatives',
];

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
