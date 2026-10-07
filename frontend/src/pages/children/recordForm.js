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
