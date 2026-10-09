import React, { useEffect, useRef, useState } from 'react';
import api from '../../api/client';
import {
  Alert, Badge, Button, FormField, Icon, Input, Select, hoverLift, iconBtn, roleLabel,
} from '../../ui';
import { PROCEED, useConfirm } from '../../context/ConfirmContext';
import {
  ADMISSION, ALIAS_CATEGORY, BIRTH_STATUSES, CASE_CATEGORIES, CASE_CATEGORY_OPTIONS, CASE_TYPES, CASE_TYPE_FIELDS,
  DYNAMIC, HEALTH_CONDITIONS, LEGAL_STATUSES, PLACEMENT, REFERRAL_SOURCES, SPECIAL_NEEDS, TYPES_OF_ADOPTION, ageRange, ageRefusal, caseChanged, caseTypesFor,
  dateFieldFor, requiredFields, unaskedAnswers,
} from '../../config/caseData';
import { shortDate, timeAgo } from '../../utils/time';
import CustodianFields from './CustodianFields';
import PsychologistPicker from './PsychologistPicker';
import { EMPTY, formFromRecord } from './recordForm';

// "2008-09-29" from the date's LOCAL parts. toISOString() gives the UTC date,
// which in Manila (UTC+8) is the previous day for anything before 8 a.m.
const localIsoDay = (d) => [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'),
  String(d.getDate()).padStart(2, '0')].join('-');

/* The add/edit form for a child record — four steps, and the longest single
 * thing in this feature by a wide margin.
 *
 * It lived in Children.jsx, which was 1,102 lines and nine components. This
 * one accounts for 443 of them and reached for exactly two things outside
 * itself, EMPTY and FORM_STEPS, so it moved with both and nothing else
 * changed. The page now imports it. EMPTY has since moved to recordForm.js,
 * beside formFromRecord, which both the page and "Load latest" use.
 *
 * Since 24 Sep 2026 the old Identity and Case steps are one step, "Child's
 * Profile", with the Category first, and every question that applies to the
 * case has to be answered before a new record saves (config/caseData.js
 * requiredFields; the server refuses the same blanks).
 */


/* "Present Environment" was "Address" until 29 Sep 2026 (owner): where the
 * child lives now AND with whom - the address, the custodian and their
 * contact number - so the step that asks for all three is named for both. */
const FORM_STEPS = ['Child\u2019s Profile', 'Present Environment', 'Recommendation', 'Assignment'];

/* Where each field lives, and what the "still needed" line calls it. */
const FIELD_INFO = {
  case_category: [1, 'category'], case_type: [1, 'case type'],
  first_name: [1, 'first name'], middle_name: [1, 'middle name'], last_name: [1, 'last name'],
  birth_date: [1, 'date of birth or given date of birth'], date_found: [1, 'date found'], gender: [1, 'sex'],
  place_of_birth_or_found: [1, 'place of birth or found'], birth_status: [1, 'birth status'],
  legal_status: [1, 'legal status'], legal_status_date: [1, 'date legal status issued'],
  health_condition: [1, 'health condition'], special_needs: [1, 'special needs'],
  current_placement: [1, 'current whereabouts'], alias: [1, 'alias'],
  custodian_name: [2, 'custodian'], custodian_contact: [2, 'contact number'],
  custodian_sms_consent: [2, 'consent to texts'],
  type_of_adoption: [1, 'type of adoption'],
  [ADMISSION]: [1, 'date of admission'], [PLACEMENT]: [1, 'date of placement'],
  house_number: [2, 'house number'], street: [2, 'street number'], landmark: [2, 'landmark'],
  barangay: [2, 'barangay'], municipality: [2, 'municipality'], province: [2, 'province'],
  psgc_barangay: [2, 'barangay'], psgc_municipality: [2, 'municipality'], psgc_province: [2, 'province'],
  referral_source: [3, 'referral source'], referral_reason: [3, 'referral reason'],
  education_level: [1, 'educational placement'],
  medical_notes: [3, 'medical notes'], recommendation: [3, 'recommendation'],
  psychologist: [4, 'psychologist'],
  social_worker: [4, 'social worker'],
};
const NAME_FIELDS = ['first_name', 'middle_name', 'last_name'];

/* The fields that show their own error under the control. A refusal for any
 * other field - or for one the current case type hides - is listed at the top
 * instead, so "they are marked below" is never said over nothing marked. */
const SHOWS_ERROR = [
  'case_category', 'case_type', 'first_name', 'middle_name', 'last_name', 'birth_date', 'date_found',
  'gender', 'place_of_birth_or_found', 'birth_status', 'legal_status', 'legal_status_date', 'education_level',
  'health_condition', 'special_needs', 'current_placement', 'alias',
  'type_of_adoption', ADMISSION, PLACEMENT, 'custodian_name', 'custodian_contact',
  'house_number', 'street', 'province', 'municipality', 'barangay', 'landmark', 'referral_source',
];

/* A refusal is about the answers it was given. Once the field - or an answer
 * it was checked against - changes, its message goes, rather than staying
 * beside a corrected date until the next save. */
const CHECKED_AGAINST = {
  birth_date: ['case_type', 'type_of_adoption'],
  date_found: ['birth_date'],
  legal_status_date: ['birth_date', 'legal_status'],
  [ADMISSION]: ['birth_date', 'case_type', 'type_of_adoption'],
  [PLACEMENT]: ['birth_date', 'case_type', 'type_of_adoption'],
  special_needs: ['health_condition'],
  case_category: ['case_type'], case_type: ['case_category'], type_of_adoption: ['case_type'],
  custodian_name: ['custodian_contact', 'custodian_sms_consent', 'case_type'],
  custodian_contact: ['custodian_name', 'custodian_sms_consent', 'case_type'],
  custodian_sms_consent: ['custodian_name', 'custodian_contact', 'case_type'],
};

/* "A, B and C". */
const listed = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);

/* The options for a list, plus the value this record already holds when that
 * value has since been retired — shown, so an old record does not look blank,
 * and marked, so nobody picks it for a new one. The record's own value as
 * opened is passed too, so it stays in the list after something else is
 * picked and can be picked back; it used to vanish, with no way back but
 * discarding the whole edit. The server accepts it, being unchanged. */
const withRetired = (options, ...values) => [
  ...options,
  ...[...new Set(values)].filter((v) => v && !options.includes(v)),
];
const optionLabel = (options, value) => (options.includes(value) ? value : `${value} (no longer offered)`);

/* What the form holds, minus its own bookkeeping, for telling whether anything
 * was typed since it opened. */
const snapshot = (f) => JSON.stringify(Object.keys(f).sort()
  .filter((k) => !k.startsWith('_'))
  .map((k) => [k, k === 'referralFile' ? Boolean(f[k]) : f[k]]));


export default function ChildForm({ form, setForm, draftKey, psychologists, socialWorkers = null, blocks = [], error, fieldErrors = null, refusedWith = null, isPsych = false, canReopen = false, others = [], saving = false, onSubmit, onWithdraw, onClose, onReopen, onOpenExisting }) {
  const [step, setStep] = useState(1);
  // Reopening the form for a different record starts at the beginning again.
  useEffect(() => { setStep(1); }, [form.id]);

  /* Closing asks first once anything has been typed — by the X, Cancel,
     Escape or a click outside. An edit loses the changes; a new record keeps
     them as a draft on this device, and the question says which. */
  const confirm = useConfirm();
  const opened = useRef(snapshot(form));
  useEffect(() => { opened.current = snapshot(form); },
    // Only when a different record is opened, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form.id]);
  const requestClose = async () => {
    if (snapshot(form) !== opened.current) {
      const ok = await confirm(form.id ? {
        title: 'Discard your changes?',
        description: `Your changes to ${form.fullname}'s record have not been saved. ${PROCEED}`,
        confirmLabel: 'Discard changes', cancelLabel: 'Keep editing', tone: 'warning',
      } : {
        title: 'Close without saving?',
        description: `This record has not been added yet. What you typed stays as a draft on this device, and Add Record offers to restore it. ${PROCEED}`,
        confirmLabel: 'Close the form', cancelLabel: 'Keep editing', tone: 'warning',
      });
      if (!ok) return;
      // The last keystrokes may still be inside the autosave's half second.
      saveDraft(form);
    }
    onClose();
  };
  const closeRef = useRef(requestClose);
  closeRef.current = requestClose;
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') closeRef.current(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  const isEdit = !!form.id;
  // Draft autosave (create mode only) — debounced write to localStorage so an
  // accidental modal close (or crash) never loses a half-typed intake record.
  const saveDraft = (current) => {
    if (current.id) return; // edits are server-backed; drafts are create-only
    const data = { ...current };
    delete data._draft; delete data._conflict;
    // A File does not survive JSON.stringify — it serialises to {}, which is
    // TRUTHY on the way back in. The restored draft would then show a chip
    // with no filename and try to upload an empty object. A chosen file is
    // not something a draft can hold, so it is not kept.
    delete data.referralFile;
    if (Object.entries(data).some(([k, v]) => k !== 'assignee_sees_history' && v)) {
      try { localStorage.setItem(draftKey, JSON.stringify(data)); } catch { /* storage full */ }
    }
  };
  useEffect(() => {
    if (form.id) return undefined;
    const t = setTimeout(() => saveDraft(form), 500);
    return () => clearTimeout(t);
    // saveDraft reads only its argument and draftKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form, draftKey]);
  // Duplicate/returning-child detection (create mode only): debounce-check
  // while typing so intake staff can reopen an archived record instead of
  // accidentally creating a second one.
  const [dupes, setDupes] = useState([]);
  useEffect(() => {
    if (form.id || !form.last_name?.trim() || !(form.first_name?.trim() || form.birth_date)) { setDupes([]); return; }
    const t = setTimeout(() => {
      const p = new URLSearchParams({ first_name: form.first_name || '', last_name: form.last_name, birth_date: form.birth_date || '' });
      api.get(`/children/check-duplicate/?${p}`).then((r) => setDupes(r.data.matches || [])).catch(() => setDupes([]));
    }, 600);
    return () => clearTimeout(t);
  }, [form.first_name, form.last_name, form.birth_date, form.id]);
  // Cascading location pickers; clear children when a parent changes.
  /* Addresses come from the PSGC tables now, not a hand-kept list. Each level
   * is fetched when its parent is chosen, so the browser never holds more than
   * one municipality's barangays — the region has 3,265 of them. */
  const [provinces, setProvinces] = useState([]);
  /* Each list remembers which place it was fetched for, and is shown only
   * while that place is still the one picked. Lists used to be taken in
   * whatever order the replies came: on a slow line the reply for the
   * province just left arrived last, and Ilocos Sur offered Ilocos Norte's
   * municipalities (29 Sep 2026). Until the right list is here the picker
   * says so, rather than looking like a province with no municipalities. */
  const [muniList, setMuniList] = useState({ of: '', places: [] });
  const [brgyList, setBrgyList] = useState({ of: '', places: [] });
  const munis = muniList.of === form.psgc_province ? muniList.places : [];
  const brgys = brgyList.of === form.psgc_municipality ? brgyList.places : [];
  const munisLoading = !!form.psgc_province && muniList.of !== form.psgc_province;
  const brgysLoading = !!form.psgc_municipality && brgyList.of !== form.psgc_municipality;

  useEffect(() => {
    api.get('/locations/provinces/').then((r) => setProvinces(r.data)).catch(() => setProvinces([]));
  }, []);

  useEffect(() => {
    const of = form.psgc_province;
    if (!of) return undefined;
    let current = true;
    api.get('/locations/municipalities/', { params: { province: of } })
      .then((r) => { if (current) setMuniList({ of, places: r.data }); })
      .catch(() => { if (current) setMuniList({ of, places: [] }); });
    return () => { current = false; };
  }, [form.psgc_province]);

  useEffect(() => {
    const of = form.psgc_municipality;
    if (!of) return undefined;
    let current = true;
    api.get('/locations/barangays/', { params: { municipality: of } })
      .then((r) => { if (current) setBrgyList({ of, places: r.data }); })
      .catch(() => { if (current) setBrgyList({ of, places: [] }); });
    return () => { current = false; };
  }, [form.psgc_municipality]);

  /* An address typed before the lists existed, on the record as it was
   * opened. It stays on screen while the address is picked again, and
   * choosing "— Select province —" puts it back: re-picking used to wipe the
   * typed municipality and barangay with no way back but discarding the
   * whole edit. */
  const typedAddress = form._record && !form._record.psgc_province
    ? [form._record.barangay, form._record.municipality, form._record.province].filter(Boolean).join(', ')
    : '';

  /* Both the code and the name are stored. The code is what survives a place
   * being renamed upstream; the name is what a case worker reads back, and what
   * every record written before this picker existed already holds. */
  const pickPlace = (level, code, options) => {
    const chosen = options.find((o) => o.psgc_code === code);
    if (level === 'province' && !code && typedAddress) {
      const r = form._record;
      setForm({ ...form, psgc_province: '', psgc_municipality: '', psgc_barangay: '',
                province: r.province || '', municipality: r.municipality || '', barangay: r.barangay || '' });
    } else if (level === 'province') {
      setForm({ ...form, psgc_province: code, province: chosen?.name || '',
                psgc_municipality: '', municipality: '', psgc_barangay: '', barangay: '' });
    } else if (level === 'municipality') {
      setForm({ ...form, psgc_municipality: code, municipality: chosen?.name || '',
                psgc_barangay: '', barangay: '' });
    } else {
      setForm({ ...form, psgc_barangay: code, barangay: chosen?.name || '' });
    }
  };
  const fieldLabel = { fontSize: 13, color: 'var(--text-muted)', fontWeight: 600 };
  const textarea = { width: '100%', resize: 'vertical', padding: '10px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.5 };
  // The age rule is the backend's (children/intake.py age_range, mirrored in
  // caseData.js): 5-17, or 18 and over for an Adult adoption. By exact
  // birthday, and the picker offers exactly the dates it accepts, worked out
  // with the same age arithmetic from local dates (not toISOString, which in
  // Manila is the day before).
  const today = new Date();
  const ageOn = (born) => today.getFullYear() - born.getFullYear()
    - ((today.getMonth() < born.getMonth()
      || (today.getMonth() === born.getMonth() && today.getDate() < born.getDate())) ? 1 : 0);
  const [youngestAge, oldestAge] = ageRange(form.case_type, form.type_of_adoption);
  // Stepped rather than computed so 29 February lands where the server's rule
  // puts it; neither loop runs more than twice.
  const earliestBirth = oldestAge == null ? null
    : new Date(today.getFullYear() - oldestAge - 1, today.getMonth(), today.getDate());
  while (earliestBirth && ageOn(earliestBirth) > oldestAge) earliestBirth.setDate(earliestBirth.getDate() + 1);
  const latestBirth = new Date(today.getFullYear() - youngestAge, today.getMonth(), today.getDate());
  while (ageOn(latestBirth) < youngestAge) latestBirth.setDate(latestBirth.getDate() - 1);
  const minBirthDate = earliestBirth ? localIsoDay(earliestBirth) : undefined;
  const maxBirthDate = localIsoDay(latestBirth);
  /* The profile asks different questions per track — a Type of Adoption on a
   * reunification case is a question nobody can answer, and one more thing to
   * skip past on every intake. */
  const caseFields = CASE_TYPE_FIELDS[form.case_type] || [];
  const asksFor = (field) => caseFields.includes(field);
  const dateField = dateFieldFor(form.case_type, form.type_of_adoption);
  /* Category comes first, so the pairing is filtered both ways: whichever of
   * the two is picked narrows the other. Neither can then hold a combination
   * the other would refuse. */
  const categoryOptions = form.case_type ? (CASE_CATEGORY_OPTIONS[form.case_type] || CASE_CATEGORIES) : CASE_CATEGORIES;
  const caseTypeOptions = caseTypesFor(form.case_category);
  // What each list leaves out, said in its hint: a list that silently lacks
  // Family Tracing reads as a form that cannot do it.
  const hiddenCategories = form.case_type ? CASE_CATEGORIES.filter((c) => !categoryOptions.includes(c)) : [];
  const hiddenCaseTypes = form.case_category ? CASE_TYPES.filter((t) => !caseTypeOptions.includes(t)) : [];
  // "(no longer offered)" is for a retired value only. A current category the
  // case type does not use is not retired, and saying so misled.
  const categoryLabel = (c) => {
    if (!CASE_CATEGORIES.includes(c)) return `${c} (no longer offered)`;
    return categoryOptions.includes(c) ? c : `${c} (not used for ${form.case_type} cases)`;
  };
  const caseTypeLabel = (t) => {
    if (!CASE_TYPES.includes(t)) return `${t} (no longer offered)`;
    return caseTypeOptions.includes(t) ? t : `${t} (does not take ${form.case_category} children)`;
  };

  /* Changing the case type (or the type of adoption) changes only what is
   * ASKED. An answer the new one does not ask for is hidden, not deleted:
   * switching back brings it back, and the save leaves out whatever the final
   * choice does not ask (caseData.js unaskedBlanked; Children.jsx save).
   * Until 29 Sep 2026 they were deleted on the spot, so a slip of the dropdown
   * lost the custodian, their confirmed number and texting consent, and the
   * date - and clearing the Case Type to re-pair the Category cost what
   * clearing the Category did not. */

  const todayIso = localIsoDay(today);
  const original = isEdit ? form._record || null : null;
  const differs = (f) => String(original?.[f] ?? '') !== String(form[f] ?? '');
  // Every answer is new on Add Record; on an edit, only what was changed.
  const changed = (f) => !isEdit || !original || differs(f);
  const retyped = !isEdit || (!!original && caseChanged(form, original));

  /* Which blanks the save refuses (backend ChildSerializer._require): on Add
   * Record, every one. On an edit, a blank the record had filled, or one the
   * case type asks again because it changed - never one a record has had
   * since before the rules, and never the custodian of a psychologist, who
   * cannot record one. The footer used to call all of them "Still blank", in
   * grey, and let the save go to be refused. The name is locked on an
   * existing record, so an edit never lists it. */
  const legacyRecord = !!original && !(original.first_name || original.last_name);
  const refusedIfBlank = (f) => {
    if (f === 'custodian_name' && isPsych) return false;
    if (!isEdit) return true;
    // No record held a health condition before it was asked, so special needs
    // typed for "With special needs" are never an older blank.
    if (f === 'special_needs') return true;
    if (!original || legacyRecord) return false;
    return !!String(original[f] ?? '').trim() || (retyped && DYNAMIC.includes(f));
  };
  const missingFields = requiredFields(form.case_type, form.type_of_adoption, form.health_condition)
    .filter((f) => !String(form[f] ?? '').trim())
    .filter((f) => !(isEdit && NAME_FIELDS.includes(f)));
  const needed = missingFields.filter(refusedIfBlank);
  const oldBlanks = missingFields.filter((f) => !refusedIfBlank(f));

  /* The server's checks on the dates and the pairing (ChildSerializer
   * validate_birth_date, _check_dates, _check_case), made here as the answers
   * change: a date only the save refused sent people back from the last step
   * with no warning on the way. Only what the save will send is checked. */
  const sent = { ...form, ...unaskedAnswers(form, original) };
  const wholeDate = (v) => /^\d{4}-\d{2}-\d{2}$/.test(v || '') && Number(String(v).slice(0, 4)) >= 1900;
  const problems = {};
  // Judged again when the case type or the type of adoption changes: that
  // changes the rule (the server does the same).
  if (wholeDate(form.birth_date) && (changed('birth_date') || changed('case_type') || changed('type_of_adoption'))) {
    const [y, m, d] = form.birth_date.split('-').map(Number);
    const age = ageOn(new Date(y, m - 1, d));
    if (age < youngestAge || (oldestAge != null && age > oldestAge)) {
      problems.birth_date = ageRefusal(form.case_type, form.type_of_adoption);
    }
  }
  if (form.legal_status && wholeDate(form.legal_status_date)
    && (changed('legal_status_date') || (isEdit && changed('birth_date')))) {
    if (form.legal_status_date > todayIso) problems.legal_status_date = 'The date issued cannot be in the future.';
    else if (wholeDate(form.birth_date) && form.legal_status_date < form.birth_date) {
      problems.legal_status_date = 'The date issued cannot be before the date of birth.';
    }
  }
  const bornMoved = isEdit && changed('birth_date');
  for (const [f, label] of [['date_found', 'The date found'], [ADMISSION, 'The date of admission'],
    [PLACEMENT, 'The date of placement']]) {
    const v = sent[f];
    // Set by this save - judged by what is sent, as the server judges it, not
    // by a hidden value the save will not send.
    const setting = !isEdit || !original || String(original[f] ?? '') !== String(v ?? '');
    // Against a moved birth date, only the dates the case shows: an older
    // record can hold the other one, which no screen shows or edits.
    if (!wholeDate(v) || (!setting && !(bornMoved && (f === 'date_found' || f === dateField)))) continue;
    if (setting && v > todayIso) problems[f] = `${label} cannot be in the future.`;
    else if (wholeDate(form.birth_date) && v < form.birth_date) problems[f] = `${label} cannot be before the date of birth.`;
  }
  if ((changed('case_type') || changed('case_category')) && CASE_CATEGORIES.includes(form.case_category)
    && CASE_CATEGORY_OPTIONS[form.case_type] && !CASE_CATEGORY_OPTIONS[form.case_type].includes(form.case_category)) {
    problems.case_category = `${form.case_category} is not a category for ${form.case_type} cases.`;
  }

  // The server's refusal, while the answers it refused are still the ones here.
  const serverMessage = (name) => {
    const e = fieldErrors?.[name];
    if (!e || !refusedWith) return null;
    const same = [name, ...(CHECKED_AGAINST[name] || [])]
      .every((k) => String(form[k] ?? '') === String(refusedWith[k] ?? ''));
    return same ? (Array.isArray(e) ? e.join(' ') : String(e)) : null;
  };
  const fieldError = (name) => serverMessage(name) || problems[name] || null;
  const displays = (k) => {
    if (!SHOWS_ERROR.includes(k)) return false;
    if (NAME_FIELDS.includes(k)) return !isEdit;
    if (k === 'custodian_name' || k === 'custodian_contact') return asksFor('custodian_name') && !isPsych;
    if (k === 'type_of_adoption') return asksFor('type_of_adoption');
    if (k === ADMISSION || k === PLACEMENT) return dateField === k;
    return true;
  };
  const refused = Object.keys(fieldErrors || {})
    .filter((k) => !['detail', 'non_field_errors'].includes(k) && serverMessage(k));
  const unmarked = [...new Set([...refused, ...Object.keys(problems)])].filter((k) => !displays(k));
  const labelOf = (k) => FIELD_INFO[k]?.[1] || k.replace(/_/g, ' ');
  // A refused save opens the step holding the first field the server marked,
  // rather than leaving somebody on Assignment reading about a street number.
  useEffect(() => {
    const first = Object.keys(fieldErrors || {}).find((k) => FIELD_INFO[k] && displays(k));
    if (first) setStep(FIELD_INFO[first][0]);
    // Once per refusal, not on every keystroke after it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldErrors]);

  const blockers = [...needed, ...Object.keys(problems).filter((k) => !needed.includes(k))];
  const blockedOnStep = (n) => blockers.some((f) => FIELD_INFO[f]?.[0] === n);
  const firstBlockerStep = blockers.length ? FIELD_INFO[blockers[0]]?.[0] : null;
  return (
    <div onClick={requestClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, zIndex: 70, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
      <form onSubmit={onSubmit} onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(980px, 96vw)', height: 'min(86vh, 820px)', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--ink-50)' }}>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{isEdit ? 'Edit Record' : 'Add Record'}</div>
          <button type="button" onClick={requestClose} aria-label="Close" {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--text-muted)')}><Icon name="x" size={17} /></button>
        </div>
        {others.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 24px', background: 'var(--blue-50)', borderBottom: '1px solid var(--blue-100)' }}>
            <Icon name="users" size={14} style={{ color: 'var(--blue-600)' }} />
            {others.map((o, i) => <Badge key={i} tone="brand" size="sm" dot>{o.name} ({roleLabel(o.role)}) is here</Badge>)}
          </div>
        )}
        <div className="racco-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {form._draft && (
            <Alert tone="info" icon={<Icon name="history" size={18} />} title="Unsaved draft found">
              You started a record earlier that wasn&apos;t saved. Continue where you left off?
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <Button variant="secondary" size="sm" onClick={() => {
                  // A draft typed before 24 Sep 2026 holds a middle initial.
                  const { middle_initial: mi, ...draft } = form._draft;
                  setForm({ ...EMPTY, ...draft, middle_name: draft.middle_name || mi || '', _draft: null });
                }} iconLeft={<Icon name="rotate-ccw" size={14} />}>Restore draft</Button>
                <Button variant="ghost" size="sm" onClick={() => { try { localStorage.removeItem(draftKey); } catch { /* private browsing */ } setForm((f) => ({ ...f, _draft: null })); }}>Discard</Button>
              </div>
            </Alert>
          )}
          {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
          {(refused.length > 0 || unmarked.length > 0) && (
            <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}
              title={refused.some(displays) || Object.keys(problems).some(displays)
                ? 'Some answers need correcting — they are marked below.' : 'Some answers need correcting.'}>
              {unmarked.length > 0 && (
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                  {unmarked.map((k) => {
                    const what = labelOf(k);
                    return <li key={k}>{what.charAt(0).toUpperCase() + what.slice(1)}: {serverMessage(k) || problems[k]}</li>;
                  })}
                </ul>
              )}
            </Alert>
          )}
          {form._conflict && (
            <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />} title="This record was just changed by a teammate.">
              Load their latest version, then re-apply your edits.
              <div style={{ marginTop: 10 }}>
                <Button type="button" variant="secondary" size="sm" onClick={() => setForm(formFromRecord(form._conflict))}>
                  Load latest
                </Button>
              </div>
            </Alert>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <div className="racco-eyebrow" style={{ fontSize: 10 }}>Profiling steps</div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 700 }}>Step {step} of {FORM_STEPS.length}</div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              {FORM_STEPS.map((label, i) => {
                const active = step === i + 1;
                // Visited and nothing left blank. A step walked past with a
                // required answer still missing is not "done", and says so;
                // on an edit, any step holding something the save refuses.
                const lacking = (isEdit || step > i + 1) && blockedOnStep(i + 1);
                const done = step > i + 1 && !lacking;
                return (
                  <React.Fragment key={label}>
                    <button
                      type="button" onClick={() => setStep(i + 1)}
                      aria-current={active ? 'step' : undefined}
                      aria-label={lacking ? `${label} — still needs answers` : undefined}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 12px', borderRadius: 'var(--radius-pill)', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12.5, border: `1px solid ${active ? 'var(--blue-500)' : lacking ? 'var(--amber-500)' : done ? 'var(--success-500)' : 'var(--border)'}`, background: active ? 'var(--blue-50)' : lacking ? 'var(--amber-50)' : done ? 'var(--success-50)' : 'var(--surface)', color: active ? 'var(--blue-700)' : lacking ? 'var(--amber-700)' : done ? 'var(--success-700)' : 'var(--text-muted)' }}
                    >{lacking && <Icon name="alert-circle" size={13} />}{label}</button>
                    {i < FORM_STEPS.length - 1 && <span style={{ color: 'var(--text-faint)', fontSize: 13, fontWeight: 700 }}>—</span>}
                  </React.Fragment>
                );
              })}
            </div>
          </div>

          {step === 1 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 10 }}>Child&apos;s Profile</div>
            <div className="racco-case-grid">
              <FormField label="Category" required error={fieldError('case_category')}
                hint={hiddenCategories.length
                  ? `${listed(hiddenCategories)} ${hiddenCategories.length === 1 ? 'is' : 'are'} not used for ${form.case_type} cases. To use one, change the Case Type first.`
                  : undefined}>
                <Select value={form.case_category || ''} onChange={(e) => setForm({ ...form, case_category: e.target.value })}>
                  <option value="">— Select category —</option>
                  {withRetired(categoryOptions, form.case_category, form._record?.case_category).map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}
                </Select>
              </FormField>
              <FormField label="Case Type" required error={fieldError('case_type')}
                hint={hiddenCaseTypes.length
                  ? `${listed(hiddenCaseTypes)} ${hiddenCaseTypes.length === 1 ? 'does' : 'do'} not take ${form.case_category} children. To pick one, change the Category first.`
                  : undefined}>
                <Select value={form.case_type || ''} onChange={(e) => setForm({ ...form, case_type: e.target.value })}>
                  <option value="">— Select case type —</option>
                  {withRetired(caseTypeOptions, form.case_type).map((t) => <option key={t} value={t}>{caseTypeLabel(t)}</option>)}
                </Select>
              </FormField>

              {/* Child name is not editable once a record exists (adviser). */}
              {isEdit ? (
                <div style={{ gridColumn: '1 / -1' }}>
                  <div style={{ ...fieldLabel, marginBottom: 6 }}>Full Name</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 13px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)', color: 'var(--text-strong)', fontWeight: 700, fontSize: 14 }}>
                    {form.fullname}
                    <Icon name="lock" size={13} style={{ color: 'var(--text-faint)', marginLeft: 'auto' }} />
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 5 }}>
                    The child&apos;s name cannot be changed after the record is created.
                    {form.middle_name ? ` Middle name on record: ${form.middle_name}.` : ''}
                  </div>
                </div>
              ) : (
                <div className="racco-name-grid" style={{ gridColumn: '1 / -1' }}>
                  <FormField label="First Name" required error={fieldError('first_name')}>
                    <Input value={form.first_name} maxLength={100} onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
                  </FormField>
                  <FormField label="Middle Name" hint="Leave blank if the child has none." error={fieldError('middle_name')}>
                    <Input value={form.middle_name || ''} maxLength={100} onChange={(e) => setForm({ ...form, middle_name: e.target.value })} />
                  </FormField>
                  <FormField label="Last Name" required error={fieldError('last_name')}>
                    <Input value={form.last_name} maxLength={100} onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
                  </FormField>
                </div>
              )}
              {/* The SCSR: "For Child Without Known Parents, indicate the given
                  first and last name and alias, if applicable". Shown for that
                  category only; an alias already saved stays when the category
                  changes - hidden, never deleted - and is sent with the save. */}
              {form.case_category === ALIAS_CATEGORY && (
                <FormField label="Alias" hint="Optional — if the child is known by another name." error={fieldError('alias')}>
                  <Input value={form.alias || ''} maxLength={150} onChange={(e) => setForm({ ...form, alias: e.target.value })} />
                </FormField>
              )}
              {!isEdit && dupes.length > 0 && (
                <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />} title="A similar record already exists" style={{ gridColumn: '1 / -1' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
                    {dupes.map((m, i) => (m.yours === false ? (m.reopenable && canReopen ? (
                      /* Another worker's CLOSED case: reopen it here and it
                         becomes yours (owner, 30 Sep 2026). Still nothing
                         from the record but the name you typed and who held it. */
                      <div key={`held-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 13 }}>
                        <strong>{m.fullname}</strong>
                        <Badge tone="neutral" size="sm" dot>Archived (Terminated)</Badge>
                        <span style={{ color: 'var(--text-muted)' }}>
                          held by {m.held_by || 'no social worker'}
                        </span>
                        <Button variant="secondary" onClick={() => onReopen(m)} iconLeft={<Icon name="rotate-ccw" size={14} />}>
                          Reopen it — it becomes yours
                        </Button>
                      </div>
                    ) : (
                      /* Another social worker's ACTIVE record: that it exists
                         and who holds it, nothing from the record (owner's
                         decision, 24 Sep 2026 - children/views.py check_duplicate). */
                      <div key={`held-${i}`} style={{ fontSize: 13 }}>
                        A record for this child is already held by{' '}
                        <strong>{m.held_by || 'no social worker yet'}</strong>.
                        Ask the ISA (Administrator) to transfer it to you instead of adding a second record.
                      </div>
                    )) : (
                      <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        <strong style={{ fontSize: 13 }}>{m.fullname}</strong>
                        <Badge tone={m.status === 'inactive' ? 'neutral' : 'success'} size="sm" dot>
                          {m.status === 'inactive' ? 'Archived (Terminated)' : 'Active'}
                        </Badge>
                        {m.birth_date && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>b. {m.birth_date}</span>}
                        {m.status === 'inactive'
                          ? (canReopen
                              ? <Button variant="secondary" onClick={() => onReopen(m)} iconLeft={<Icon name="rotate-ccw" size={14} />}>Reopen this record instead</Button>
                              : <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Ask staff or an administrator to reopen this archived record instead of creating a new one.</span>)
                          : <Button variant="secondary" onClick={() => onOpenExisting(m)} iconLeft={<Icon name="eye" size={14} />}>Open existing record</Button>}
                      </div>
                    )))}
                  </div>
                </Alert>
              )}
              <FormField label="Date of Birth or Given Date of Birth" required error={fieldError('birth_date')}
                hint={!isEdit
                  ? (oldestAge == null
                    ? `Must be 18 or older for an Adult adoption: born ${shortDate(latestBirth)} or earlier. For a foundling, the given date.`
                    : `The child must be ${youngestAge} to ${oldestAge} today: born ${shortDate(earliestBirth)} to ${shortDate(latestBirth)}. For a foundling, the given date.`)
                  : undefined}>
                <Input type="date" value={form.birth_date || ''} min={!isEdit ? minBirthDate : undefined} max={!isEdit ? maxBirthDate : undefined} onChange={(e) => setForm({ ...form, birth_date: e.target.value })} />
              </FormField>
              <FormField label="Date Found" hint="Only for a child who was found." error={fieldError('date_found')}>
                <Input type="date" value={form.date_found || ''} min={form.birth_date || undefined} max={todayIso} onChange={(e) => setForm({ ...form, date_found: e.target.value })} />
              </FormField>
              <FormField label="Sex" required error={fieldError('gender')}>
                <Select value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}>
                  <option value="">—</option><option>Male</option><option>Female</option>
                </Select>
              </FormField>
              <FormField label="Place of Birth or Place Found" required error={fieldError('place_of_birth_or_found')}>
                <Input value={form.place_of_birth_or_found || ''} maxLength={150} onChange={(e) => setForm({ ...form, place_of_birth_or_found: e.target.value })} />
              </FormField>
              <FormField label="Birth Status" required error={fieldError('birth_status')}>
                <Select value={form.birth_status || ''} onChange={(e) => setForm({ ...form, birth_status: e.target.value })}>
                  <option value="">— Select —</option>
                  {withRetired(BIRTH_STATUSES, form.birth_status, form._record?.birth_status).map((v) => <option key={v} value={v}>{optionLabel(BIRTH_STATUSES, v)}</option>)}
                </Select>
              </FormField>
              <FormField label="Legal Status" hint="Leave blank if none has been issued yet." error={fieldError('legal_status')}>
                <Select value={form.legal_status || ''} onChange={(e) => setForm({ ...form, legal_status: e.target.value })}>
                  <option value="">— Select —</option>
                  {withRetired(LEGAL_STATUSES, form.legal_status, form._record?.legal_status).map((v) => <option key={v} value={v}>{optionLabel(LEGAL_STATUSES, v)}</option>)}
                </Select>
              </FormField>
              {form.legal_status && (
                <FormField label="Date Issued" hint="Optional: when the legal status was issued." error={fieldError('legal_status_date')}>
                  <Input type="date" value={form.legal_status_date || ''} min={form.birth_date || undefined} max={todayIso}
                    onChange={(e) => setForm({ ...form, legal_status_date: e.target.value })} />
                </FormField>
              )}
              <FormField label="Health Condition" required error={fieldError('health_condition')}>
                <Select value={form.health_condition || ''} onChange={(e) => setForm({ ...form, health_condition: e.target.value })}>
                  <option value="">— Select —</option>
                  {withRetired(HEALTH_CONDITIONS, form.health_condition, form._record?.health_condition).map((v) => <option key={v} value={v}>{optionLabel(HEALTH_CONDITIONS, v)}</option>)}
                </Select>
              </FormField>
              {/* Kept, hidden, when the answer changes; the server drops it
                  for anything but "With special needs". */}
              {form.health_condition === SPECIAL_NEEDS && (
                <FormField label="Specify the special needs" required error={fieldError('special_needs')}>
                  <Input value={form.special_needs || ''} maxLength={300} placeholder="e.g. Hearing loss, in therapy"
                    onChange={(e) => setForm({ ...form, special_needs: e.target.value })} />
                </FormField>
              )}
              {/* Moved here from Recommendation (24 Sep 2026): every child has
                  an answer, even if the answer is that they are not in school. */}
              <FormField label="Educational Placement" required error={fieldError('education_level')}
                hint="The grade level, or “Not in school”.">
                <Input value={form.education_level || ''} maxLength={100} placeholder="e.g. Grade 4"
                  onChange={(e) => setForm({ ...form, education_level: e.target.value })} />
              </FormField>
              {/* Taken off on 24 Sep 2026 and asked again on 7 Oct (owner): the
                  SCSR's Part I lists it. */}
              <FormField label="Current Whereabouts" required error={fieldError('current_placement')}>
                <Input value={form.current_placement || ''} maxLength={150} placeholder="e.g. Foster family, residential facility"
                  onChange={(e) => setForm({ ...form, current_placement: e.target.value })} />
              </FormField>
              {asksFor('type_of_adoption') && (
                <FormField label="Type of Adoption" required error={fieldError('type_of_adoption')}>
                  <Select value={form.type_of_adoption || ''} onChange={(e) => setForm({ ...form, type_of_adoption: e.target.value })}>
                    <option value="">— Select —</option>
                    {withRetired(TYPES_OF_ADOPTION, form.type_of_adoption, form._record?.type_of_adoption).map((v) => <option key={v} value={v}>{optionLabel(TYPES_OF_ADOPTION, v)}</option>)}
                  </Select>
                </FormField>
              )}
              {/* One date, never both: an admission for a child the agency took
                  in, a placement for a child placed with a custodian. */}
              {dateField === ADMISSION && (
                <FormField label="Date of Admission to the Agency" required error={fieldError(ADMISSION)}>
                  <Input type="date" value={form[ADMISSION] || ''} min={form.birth_date || undefined} max={todayIso} onChange={(e) => setForm({ ...form, [ADMISSION]: e.target.value })} />
                </FormField>
              )}
              {dateField === PLACEMENT && (
                <FormField label="Date of Placement to Custodian" required error={fieldError(PLACEMENT)}>
                  <Input type="date" value={form[PLACEMENT] || ''} min={form.birth_date || undefined} max={todayIso} onChange={(e) => setForm({ ...form, [PLACEMENT]: e.target.value })} />
                </FormField>
              )}
              {!dateField && (
                <div style={{ alignSelf: 'end', fontSize: 12.5, color: 'var(--text-muted)', padding: '10px 12px', background: 'var(--ink-50)', border: '1px dashed var(--border-strong)', borderRadius: 'var(--radius-md)' }}>
                  {form.case_type === 'Adoption'
                    ? 'Pick the type of adoption: a Regular adoption asks for the date of admission, every other type for the date of placement to custodian.'
                    : 'Pick the case type to see which date it asks for.'}
                </div>
              )}
            </div>
          </section>
          )}

          {step === 2 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 4 }}>Present Environment</div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
              Where the child lives now, and with whom.
            </div>
            {asksFor('custodian_name') && (
              <CustodianFields form={form} setForm={setForm} fieldError={fieldError} readOnly={isPsych} />
            )}
            <div className="racco-eyebrow" style={{ fontSize: 10, margin: '6px 0 4px' }}>Address</div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
              Choose the province first — the municipality and barangay lists follow from it.
            </div>
            <div className="racco-case-grid">
              <FormField label="House Number" required error={fieldError('house_number')}>
                <Input value={form.house_number || ''} maxLength={50} onChange={(e) => setForm({ ...form, house_number: e.target.value })} />
              </FormField>
              <FormField label="Street Number" required hint="Or the purok or sitio, where there is no street." error={fieldError('street')}>
                <Input value={form.street || ''} maxLength={150} onChange={(e) => setForm({ ...form, street: e.target.value })} />
              </FormField>
              {/* Province, then municipality, then barangay: the order the
                  lists unlock in, each filtered by the one before it. They
                  were laid out the other way round, so the address was filled
                  right to left past two disabled selects. */}
              <FormField label="Province" required error={fieldError('province')}>
                <Select value={form.psgc_province || ''} onChange={(e) => pickPlace('province', e.target.value, provinces)}>
                  <option value="">— Select province —</option>
                  {provinces.map((p) => <option key={p.psgc_code} value={p.psgc_code}>{p.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Municipality / City" required error={fieldError('municipality')}>
                <Select value={form.psgc_municipality || ''} disabled={!form.psgc_province || munisLoading} onChange={(e) => pickPlace('municipality', e.target.value, munis)}>
                  <option value="">{!form.psgc_province ? 'Select a province first' : munisLoading ? 'Loading…' : '— Select municipality —'}</option>
                  {munis.map((m) => <option key={m.psgc_code} value={m.psgc_code}>{m.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Barangay" required error={fieldError('barangay')} hint={brgys.length ? `${brgys.length} in this municipality` : undefined}>
                <Select value={form.psgc_barangay || ''} disabled={!form.psgc_municipality || brgysLoading} onChange={(e) => pickPlace('barangay', e.target.value, brgys)}>
                  <option value="">{!form.psgc_municipality ? 'Select a municipality first' : brgysLoading ? 'Loading…' : '— Select barangay —'}</option>
                  {brgys.map((b) => <option key={b.psgc_code} value={b.psgc_code}>{b.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Landmark" hint="Optional — anything that helps find the house." error={fieldError('landmark')}>
                <Input value={form.landmark || ''} maxLength={200} onChange={(e) => setForm({ ...form, landmark: e.target.value })} />
              </FormField>
              {/* An address typed before the picker existed has no code, so the
                  selects above sit empty and would look like a blank address.
                  Show what the record actually says. */}
              {typedAddress && (
                <div style={{ gridColumn: '1 / -1', fontSize: 12.5, color: 'var(--text-muted)', padding: '10px 12px', background: 'var(--ink-50)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
                  Recorded before the address list existed:{' '}
                  <strong style={{ color: 'var(--text-strong)' }}>{typedAddress}</strong>.{' '}
                  {form.psgc_province
                    ? 'To keep it as it was instead, choose “— Select province —” above.'
                    : 'Re-pick it above to attach the official codes, or leave it as it is.'}
                </div>
              )}
            </div>
          </section>
          )}

          {step === 3 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 4 }}>Recommendation</div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>Details beyond the agency&apos;s intake interview.</div>
            <div className="racco-case-grid">
              {/* A pick since 24 Sep 2026. */}
              <FormField label="Referral Source" error={fieldError('referral_source')}
                hint="RACCO · LGU (local government unit) · CCA (child caring agency) · RCF (residential care facility)">
                <Select value={form.referral_source || ''} onChange={(e) => setForm({ ...form, referral_source: e.target.value })}>
                  <option value="">— Select —</option>
                  {withRetired(REFERRAL_SOURCES, form.referral_source, form._record?.referral_source).map((v) => <option key={v} value={v}>{optionLabel(REFERRAL_SOURCES, v)}</option>)}
                </Select>
              </FormField>
              <FormField label="Referral Reason" style={{ gridColumn: '1 / -1' }}>
                <textarea value={form.referral_reason || ''} onChange={(e) => setForm({ ...form, referral_reason: e.target.value })} rows={3} style={textarea} />
              </FormField>
              <FormField label="Medical Notes" style={{ gridColumn: '1 / -1' }}>
                <textarea value={form.medical_notes || ''} onChange={(e) => setForm({ ...form, medical_notes: e.target.value })} rows={3} style={textarea} />
              </FormField>
              <FormField label="Recommendation" hint="Follow-ups, referrals, and notes outside the intake timeline." style={{ gridColumn: '1 / -1' }}>
                <textarea value={form.recommendation || ''} onChange={(e) => setForm({ ...form, recommendation: e.target.value })} rows={3} style={textarea} />
              </FormField>
            </div>
          </section>
          )}

          {step === 4 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 10 }}>Assignment</div>

            {/* The referral belongs on this step because this is the step where
                somebody hands the child to a psychologist, and no session can
                be booked without it. Without the field here the record saves
                fine and then refuses every booking, with the only way to fix it
                on a different screen — a dead end somebody has to be told about
                rather than shown. Uploaded after the record is created, because
                a referral belongs to a child and there is no id until then. */}
            {!isPsych && (
              <FormField
                label="Case referral"
                /* The control is inside a wrapper, so FormField's own id
                   injection would label the wrapper: point at the input. */
                htmlFor="case-referral-file"
                hint={form.id
                  ? 'Already on file? Add or replace it from the child’s record.'
                  : 'PDF or Word. The social worker’s referral — sessions cannot be booked without one, though the record saves either way.'}
              >
                <div id="case-referral-row" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <input
                    id="case-referral-file"
                    type="file"
                    /* Exactly what CaseReferralSerializer.validate_file
                       accepts. Offering a .png in the picker and then
                       refusing it on the server is the screen lying. */
                    accept=".pdf,.doc,.docx"
                    /* Each step unmounts when it is left, and the box came
                       back saying "No file chosen" while the form still held
                       the file and filed it on save. It is put back, so the
                       box says what will be filed; Remove empties both. */
                    ref={(el) => {
                      if (!el) return;
                      const chosen = form.referralFile;
                      if (!chosen && el.files?.length) el.value = '';
                      if (chosen instanceof File && el.files?.[0] !== chosen) {
                        try {
                          const dt = new DataTransfer();
                          dt.items.add(chosen);
                          el.files = dt.files;
                        } catch { /* no DataTransfer: the badge still names it */ }
                      }
                    }}
                    onChange={(e) => setForm({ ...form, referralFile: e.target.files?.[0] || null })}
                    style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--text-body)' }}
                  />
                  {form.referralFile && (
                    <>
                      <Badge tone="success" size="sm">{form.referralFile.name}</Badge>
                      <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${form.referralFile.name}`}
                        onClick={() => setForm({ ...form, referralFile: null })}>Remove</Button>
                    </>
                  )}
                </div>
              </FormField>
            )}

            {isPsych ? (
              <FormField label="Assigned Psychologist" hint="Reassignment is done by admin/staff.">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 13px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)', color: 'var(--text-strong)', fontWeight: 700, fontSize: 14 }}>
                  {form.psychologist_name || '—'}
                  <Icon name="lock" size={13} style={{ color: 'var(--text-faint)', marginLeft: 'auto' }} />
                </div>
              </FormField>
            ) : (
              <>
                {socialWorkers && (
                  /* The ISA only: whose record this is. A SW's new record is
                     theirs, and only the ISA moves one (accounts/scoping.py). */
                  <FormField label="Social Worker" hint="Only this social worker, and the ISA, can see the record.">
                    <Select value={form.social_worker || ''} onChange={(e) => setForm({ ...form, social_worker: e.target.value })}>
                      <option value="">— No social worker yet —</option>
                      {socialWorkers.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                      {form.social_worker && !socialWorkers.some((w) => String(w.id) === String(form.social_worker)) && (
                        <option value={form.social_worker}>{form.social_worker_name || 'Current social worker'} (inactive)</option>
                      )}
                    </Select>
                  </FormField>
                )}
                {/* One way to assign, not two: a dropdown above this list
                    offered the same psychologists without their availability,
                    and went at the owner's request (28 Sep 2026). Its one
                    extra, "Unassigned", lives on as "Leave unassigned": a
                    record may wait for a psychologist, and a pick made by
                    mistake has to be undoable. */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minHeight: 30 }}>
                    <span id="assign-psychologist-label" style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>Assign Psychologist</span>
                    {form.psychologist && (
                      <Button type="button" variant="ghost" size="sm" onClick={() => setForm({ ...form, psychologist: '' })}>Leave unassigned</Button>
                    )}
                  </div>
                  {/* Assigning is asking (backend children/assignment.py):
                      the record shows them as the child's psychologist only
                      once they accept, and this is the place to say so. */}
                  <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    The psychologist you pick is asked first. The child joins their records when they accept; if they decline, you see why.
                  </span>
                  {form.pending_assignment && (
                    <Alert tone="warning" icon={<Icon name="hourglass" size={18} />}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', justifyContent: 'space-between' }}>
                        <span>
                          Waiting for <strong>{form.pending_assignment.psychologist_name}</strong> to accept
                          {' '}(asked {timeAgo(form.pending_assignment.created_at)}
                          {form.pending_assignment.requested_by_name ? ` by ${form.pending_assignment.requested_by_name}` : ''}).
                          {form.psychologist_name ? ` ${form.psychologist_name} keeps the case until then.` : ''}
                        </span>
                        {onWithdraw && (
                          <Button type="button" variant="secondary" size="sm" onClick={onWithdraw}>Withdraw request</Button>
                        )}
                      </div>
                    </Alert>
                  )}
                  {!form.pending_assignment && form.declined_assignment && (
                    <Alert tone="danger" icon={<Icon name="user-x" size={18} />}>
                      <strong>{form.declined_assignment.psychologist_name}</strong> declined
                      {' '}{timeAgo(form.declined_assignment.decided_at)}: &ldquo;{form.declined_assignment.reason}&rdquo;
                      {' '}Pick someone else below.
                    </Alert>
                  )}
                  {!form.psychologist && (
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                      {psychologists.length > 0
                        ? 'Not assigned yet. Pick a psychologist below, or save and assign later.'
                        : 'No psychologist accounts yet. The record saves unassigned.'}
                    </span>
                  )}
                  {/* Held by somebody no longer on the list (an archived
                      account): nothing below is highlighted, so say who. */}
                  {form.psychologist && !psychologists.some((p) => String(p.id) === String(form.psychologist)) && (
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                      Currently {(String(form.pending_assignment?.psychologist) === String(form.psychologist)
                        ? form.pending_assignment.psychologist_name : form.psychologist_name) || 'a psychologist'}, who is no longer active. Pick someone below to reassign.
                    </span>
                  )}
                </div>
                {psychologists.length > 0 && (
                  <PsychologistPicker
                    psychologists={psychologists} blocks={blocks} value={form.psychologist}
                    onPick={(id) => setForm({ ...form, psychologist: String(id) })}
                    pendingId={form.pending_assignment?.psychologist ?? null}
                    holderId={form.id ? form._origPsychologist : null}
                  />
                )}
                {isEdit && form.psychologist && String(form.psychologist) !== String(form._origPsychologist) && (
                  <div style={{ marginTop: 10, padding: '11px 13px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-200)' }}>
                    <label style={{ display: 'flex', gap: 9, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text-strong)', cursor: 'pointer' }}>
                      <input type="checkbox" checked={form.assignee_sees_history !== false} onChange={(e) => setForm({ ...form, assignee_sees_history: e.target.checked })} style={{ marginTop: 2, accentColor: 'var(--blue-600)' }} />
                      <span>Carry this child&apos;s session history to the new psychologist (they&apos;ll see prior records). Uncheck to give them a fresh start.</span>
                    </label>
                  </div>
                )}
              </>
            )}
          </section>
          )}
        </div>
        <div style={{ padding: '14px 24px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          {/* Editing can save from any step — walking four pages to correct a
              phone number is the kind of thing that makes people avoid the form.
              Creating still has to reach the end, so nothing is missed. An old
              record may have blanks from before the fields were required; the
              edit names them without refusing to save, and the server refuses
              only an answer being taken away. */}
          {(isEdit || step === FORM_STEPS.length) && (blockers.length > 0 || (isEdit && oldBlanks.length > 0)) && (
            <span role="status" style={{ alignSelf: 'center', display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12.5, marginRight: 'auto', minWidth: 0 }}>
              {needed.length > 0 && (
                <span style={{ color: 'var(--amber-700)', fontWeight: 600 }}>
                  {isEdit ? 'Needed before saving' : 'Still needed'}: {needed.map(labelOf).join(', ')}
                </span>
              )}
              {Object.keys(problems).length > 0 && (
                <span style={{ color: 'var(--red-600)', fontWeight: 600 }}>Check: {Object.keys(problems).map(labelOf).join(', ')}</span>
              )}
              {isEdit && oldBlanks.length > 0 && (
                <span style={{ color: 'var(--text-muted)' }}>Blank from before, saves as it is: {oldBlanks.map(labelOf).join(', ')}</span>
              )}
              {firstBlockerStep && firstBlockerStep !== step && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setStep(firstBlockerStep)}>Go to it</Button>
              )}
            </span>
          )}
          <Button type="button" variant="secondary" onClick={requestClose}>Cancel</Button>
          {step > 1 && (
            <Button type="button" variant="ghost" onClick={() => setStep((n) => n - 1)} iconLeft={<Icon name="arrow-left" size={15} />}>Back</Button>
          )}
          {step < FORM_STEPS.length && (
            <Button type="button" variant={isEdit ? 'secondary' : 'primary'} onClick={() => setStep((n) => n + 1)}>Next</Button>
          )}
          {(isEdit || step === FORM_STEPS.length) && (
            <Button type="submit" variant="primary" disabled={blockers.length > 0 || saving} aria-busy={saving || undefined} iconLeft={<Icon name="save" size={16} />}>{saving ? 'Saving…' : 'Save Record'}</Button>
          )}
        </div>
      </form>
    </div>
  );
}
