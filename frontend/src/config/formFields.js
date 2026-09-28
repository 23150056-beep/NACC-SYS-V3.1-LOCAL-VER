// The kinds of field an agency form template can hold - the Instruments
// page's template editor and the Clinical interview step's "Upload a
// template" review (components/TemplateUpload.jsx) offer the same list, and
// the interview step renders each of them.
export const FIELD_TYPES = [
  { v: 'section', label: 'Section heading' },
  { v: 'text', label: 'Short text' },
  { v: 'long_text', label: 'Long text' },
  { v: 'date', label: 'Date' },
  { v: 'yes_no', label: 'Yes / No' },
  { v: 'choice', label: 'Choice list' },
];
