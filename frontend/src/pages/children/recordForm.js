/* What the child record form holds: blank for Add Record, or filled from a
 * record for an edit. Apart from ChildForm.jsx so the page and the form share
 * them without a component file exporting plain values. */

export const EMPTY = {
  first_name: '', middle_name: '', last_name: '',
  birth_date: '', date_found: '', gender: '',
  house_number: '', street: '', landmark: '',
  province: '', municipality: '', barangay: '', psgc_province: '', psgc_municipality: '', psgc_barangay: '',
  case_type: '', case_category: '', custodian_name: '', psychologist: '', assignee_sees_history: true,
  custodian_contact: '', custodian_sms_consent: false,
  place_of_birth_or_found: '', birth_status: '', legal_status: '', legal_status_date: '',
  health_condition: '', special_needs: '', alias: '',
  date_of_admission: '', date_of_placement_to_custodian: '', type_of_adoption: '',
  referral_source: '', referral_reason: '', education_level: '', current_placement: '', medical_notes: '',
  recommendation: '',
};

/* One token for one submission of Add Record (backend children/duplicates.py).
 * The form makes it when it opens a NEW record, keeps it with the draft, and
 * sends it with every attempt to save: a second attempt - a double click that
 * got past the button, or a retry after a response that never arrived - is
 * then told the record was already saved instead of adding the child again.
 * crypto.randomUUID() exists only on https and localhost; a workstation
 * reaching the app by its address on the office network has just
 * getRandomValues, which is enough for a token. */
export function newIntakeToken() {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  const b = new Uint8Array(16);
  if (c?.getRandomValues) c.getRandomValues(b);
  else for (let i = 0; i < b.length; i += 1) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/* What a form holds that is not something the person typed. A draft is worth
 * keeping, and offering back, only when an answer was; the token and the
 * history tick are there from the moment the form opens. */
export const NOT_AN_ANSWER = ['assignee_sees_history', 'intake_token'];

/* Who, and which number, texting consent was given for. Consent belongs to
 * that pair (pages/children/CustodianFields.jsx): change either and it is
 * off, put both back and it is on again. `number` is as stored, +639... */
export const consentKey = (name, number) => `${String(name || '').trim()}|${number || ''}`;

/* The form for an existing record. `psychologist` is who the child SHOULD be
 * with: the one asked, while a request is open, else the one who holds them.
 * The server reads it the same way (ChildViewSet.perform_update), so resending
 * it untouched changes nothing. `_origPsychologist` stays the holder - the
 * carry-history choice is about moving the child away from them. `_record` is
 * the record as it was opened, which the edit rules below compare against. */
export const formFromRecord = (c) => {
  const pending = c.pending_assignment;
  return {
    ...EMPTY, ...c,
    psychologist: String(pending?.psychologist || c.psychologist || ''),
    _origPsychologist: c.psychologist || '',
    _basePsychologist: String(pending?.psychologist || c.psychologist || ''),
    assignee_sees_history: pending ? pending.carry_history : c.assignee_sees_history,
    // The number as a person types it, and what the record held when opened:
    // consent and confirmation belong to that person and number
    // (pages/children/CustodianFields.jsx).
    custodian_contact: c.custodian_contact_display || '',
    _origContact: c.custodian_contact || '',
    _origCustodian: c.custodian_name || '',
    _origConsent: !!c.custodian_sms_consent,
    _consentFor: c.custodian_sms_consent ? consentKey(c.custodian_name, c.custodian_contact) : null,
    _record: c,
  };
};
