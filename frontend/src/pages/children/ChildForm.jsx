import React, { useEffect, useRef, useState } from 'react';
import api from '../../api/client';
import {
  Alert, Badge, Button, FormField, Icon, Input, Select, hoverLift, iconBtn, roleLabel,
} from '../../ui';
import { PROCEED, useConfirm } from '../../context/ConfirmContext';
import {
  ADMISSION, BIRTH_STATUSES, CASE_CATEGORIES, CASE_CATEGORY_OPTIONS, CASE_TYPES, CASE_TYPE_FIELDS,
  LEGAL_STATUSES, PLACEMENT, REFERRAL_SOURCES, TYPES_OF_ADOPTION, caseTypesFor, dateFieldFor,
  requiredFields,
} from '../../config/caseData';

/* The add/edit form for a child record — four steps, and the longest single
 * thing in this feature by a wide margin.
 *
 * It lived in Children.jsx, which was 1,102 lines and nine components. This
 * one accounts for 443 of them and reached for exactly two things outside
 * itself, EMPTY and FORM_STEPS, so it moved with both and nothing else
 * changed. The page now imports it.
 *
 * Since 24 Sep 2026 the old Identity and Case steps are one step, "Child's
 * Profile", with the Category first, and every question that applies to the
 * case has to be answered before a new record saves (config/caseData.js
 * requiredFields; the server refuses the same blanks).
 */

export const EMPTY = {
  first_name: '', middle_name: '', last_name: '',
  birth_date: '', date_found: '', gender: '',
  house_number: '', street: '', landmark: '',
  province: '', municipality: '', barangay: '', psgc_province: '', psgc_municipality: '', psgc_barangay: '',
  case_type: '', case_category: '', surrendered_by: '', psychologist: '', assignee_sees_history: true,
  place_of_birth_or_found: '', birth_status: '', legal_status: '',
  date_of_admission: '', date_of_placement_to_custodian: '', type_of_adoption: '',
  referral_source: '', referral_reason: '', education_level: '', current_placement: '', medical_notes: '',
  recommendation: '',
};


const FORM_STEPS = ['Child\u2019s Profile', 'Address', 'Recommendation', 'Assignment'];

/* Where each field lives, and what the "still needed" line calls it. */
const FIELD_INFO = {
  case_category: [1, 'category'], case_type: [1, 'case type'],
  first_name: [1, 'first name'], middle_name: [1, 'middle name'], last_name: [1, 'last name'],
  birth_date: [1, 'date of birth'], date_found: [1, 'date found'], gender: [1, 'sex'],
  place_of_birth_or_found: [1, 'place of birth or found'], birth_status: [1, 'birth status'],
  legal_status: [1, 'legal status'], surrendered_by: [1, 'previous custodian'],
  type_of_adoption: [1, 'type of adoption'],
  [ADMISSION]: [1, 'date of admission'], [PLACEMENT]: [1, 'date of placement'],
  house_number: [2, 'house number'], street: [2, 'street number'], landmark: [2, 'landmark'],
  barangay: [2, 'barangay'], municipality: [2, 'municipality'], province: [2, 'province'],
  psgc_barangay: [2, 'barangay'], psgc_municipality: [2, 'municipality'], psgc_province: [2, 'province'],
  referral_source: [3, 'referral source'], referral_reason: [3, 'referral reason'],
  education_level: [1, 'educational placement'],
  medical_notes: [3, 'medical notes'], recommendation: [3, 'recommendation'],
  psychologist: [4, 'psychologist'],
};
const NAME_FIELDS = ['first_name', 'middle_name', 'last_name'];

/* The options for a list, plus the value this record already holds when that
 * value has since been retired — shown, so an old record does not look blank,
 * and marked, so nobody picks it for a new one. */
const withRetired = (options, value) => (
  value && !options.includes(value) ? [...options, value] : options
);
const optionLabel = (options, value) => (options.includes(value) ? value : `${value} (no longer offered)`);

/* What the form holds, minus its own bookkeeping, for telling whether anything
 * was typed since it opened. */
const snapshot = (f) => JSON.stringify(Object.keys(f).sort()
  .filter((k) => !k.startsWith('_'))
  .map((k) => [k, k === 'referralFile' ? Boolean(f[k]) : f[k]]));


export default function ChildForm({ form, setForm, draftKey, psychologists, blocks = [], error, fieldErrors = null, isPsych = false, canReopen = false, others = [], onSubmit, onClose, onReopen, onOpenExisting }) {
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
  // A refused save opens the step holding the first field the server named,
  // rather than leaving somebody on Assignment reading about a street number.
  useEffect(() => {
    const first = Object.keys(fieldErrors || {}).find((k) => FIELD_INFO[k]);
    if (first) setStep(FIELD_INFO[first][0]);
  }, [fieldErrors]);
  const fieldError = (name) => {
    const e = fieldErrors?.[name];
    return Array.isArray(e) ? e.join(' ') : (e || null);
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
  // Availability-comparison panel helpers (Task 18) — matches AvailabilityBlock 0=Monday.
  const DAY_ABBR = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const availFor = (pid) => blocks.filter((b) => String(b.psychologist) === String(pid));
  const blockLabel = (b) => `${b.date || DAY_ABBR[b.weekday]} ${String(b.start_time).slice(0, 5)}–${String(b.end_time).slice(0, 5)}`;
  // Cascading location pickers; clear children when a parent changes.
  /* Addresses come from the PSGC tables now, not a hand-kept list. Each level
   * is fetched when its parent is chosen, so the browser never holds more than
   * one municipality's barangays — the region has 3,265 of them. */
  const [provinces, setProvinces] = useState([]);
  const [munis, setMunis] = useState([]);
  const [brgys, setBrgys] = useState([]);

  useEffect(() => {
    api.get('/locations/provinces/').then((r) => setProvinces(r.data)).catch(() => setProvinces([]));
  }, []);

  useEffect(() => {
    if (!form.psgc_province) { setMunis([]); return; }
    api.get('/locations/municipalities/', { params: { province: form.psgc_province } })
      .then((r) => setMunis(r.data)).catch(() => setMunis([]));
  }, [form.psgc_province]);

  useEffect(() => {
    if (!form.psgc_municipality) { setBrgys([]); return; }
    api.get('/locations/barangays/', { params: { municipality: form.psgc_municipality } })
      .then((r) => setBrgys(r.data)).catch(() => setBrgys([]));
  }, [form.psgc_municipality]);

  /* Both the code and the name are stored. The code is what survives a place
   * being renamed upstream; the name is what a case worker reads back, and what
   * every record written before this picker existed already holds. */
  const pickPlace = (level, code, options) => {
    const chosen = options.find((o) => o.psgc_code === code);
    if (level === 'province') {
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
  // Agency only serves children aged 5-17: the birth date picker's bounds
  // mirror that (max = today minus 5 years, min = today minus 18 years);
  // the backend's validate_birth_date is the authoritative check.
  const today = new Date();
  const maxBirthDate = new Date(today.getFullYear() - 5, today.getMonth(), today.getDate()).toISOString().slice(0, 10);
  const minBirthDate = new Date(today.getFullYear() - 18, today.getMonth(), today.getDate()).toISOString().slice(0, 10);
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

  /* Changing the track clears anything the new one does not ask for, so a case
   * switched from Adoption to Independent Living cannot keep a stale Type of
   * Adoption that no screen will ever show again — or a date of placement
   * sitting behind the date of admission it now asks for. */
  const changeCaseType = (nextType) => {
    const nextFields = CASE_TYPE_FIELDS[nextType] || [];
    const nextCategories = CASE_CATEGORY_OPTIONS[nextType] || CASE_CATEGORIES;
    const nextAdoption = nextFields.includes('type_of_adoption') ? form.type_of_adoption : '';
    const nextDate = dateFieldFor(nextType, nextAdoption);
    setForm({
      ...form,
      case_type: nextType,
      case_category: nextCategories.includes(form.case_category) ? form.case_category : '',
      surrendered_by: nextFields.includes('surrendered_by') ? form.surrendered_by : '',
      type_of_adoption: nextAdoption,
      [ADMISSION]: nextDate === ADMISSION ? form[ADMISSION] : '',
      [PLACEMENT]: nextDate === PLACEMENT ? form[PLACEMENT] : '',
    });
  };
  // The type of adoption decides which date an adoption records.
  const changeAdoptionType = (next) => {
    const nextDate = dateFieldFor(form.case_type, next);
    setForm({
      ...form,
      type_of_adoption: next,
      [ADMISSION]: nextDate === ADMISSION ? form[ADMISSION] : '',
      [PLACEMENT]: nextDate === PLACEMENT ? form[PLACEMENT] : '',
    });
  };

  const todayIso = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, '0'), String(today.getDate()).padStart(2, '0')].join('-');
  // Named, not just disabled: the required fields live on two different steps,
  // so a greyed-out Save with no explanation sends people hunting. The name is
  // locked on an existing record, so an edit never lists it.
  const missingFields = requiredFields(form.case_type, form.type_of_adoption)
    .filter((f) => !String(form[f] ?? '').trim())
    .filter((f) => !(isEdit && NAME_FIELDS.includes(f)));
  const missing = missingFields.map((f) => FIELD_INFO[f]?.[1] || f);
  const missingOnStep = (n) => missingFields.some((f) => FIELD_INFO[f]?.[0] === n);
  const requiredFieldsFilled = missingFields.length === 0;
  const firstMissingStep = missingFields.length ? FIELD_INFO[missingFields[0]]?.[0] : null;
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
          {form._conflict && (
            <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />} title="This record was just changed by a teammate.">
              Load their latest version, then re-apply your edits.
              <div style={{ marginTop: 10 }}>
                <Button type="button" variant="secondary" size="sm" onClick={() => setForm({ ...EMPTY, ...form._conflict, psychologist: form._conflict.psychologist || '', _origPsychologist: form._conflict.psychologist || '' })}>
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
                // required answer still missing is not "done", and says so.
                const lacking = !isEdit && step > i + 1 && missingOnStep(i + 1);
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
                hint={form.case_type && categoryOptions.length < CASE_CATEGORIES.length ? `The categories for ${form.case_type} cases.` : undefined}>
                <Select value={form.case_category || ''} onChange={(e) => setForm({ ...form, case_category: e.target.value })}>
                  <option value="">— Select category —</option>
                  {withRetired(categoryOptions, form.case_category).map((c) => <option key={c} value={c}>{optionLabel(categoryOptions, c)}</option>)}
                </Select>
              </FormField>
              <FormField label="Case Type" required error={fieldError('case_type')}
                hint={form.case_category && caseTypeOptions.length < CASE_TYPES.length ? `The case types that take ${form.case_category} children.` : undefined}>
                <Select value={form.case_type || ''} onChange={(e) => changeCaseType(e.target.value)}>
                  <option value="">— Select case type —</option>
                  {withRetired(caseTypeOptions, form.case_type).map((t) => <option key={t} value={t}>{t}</option>)}
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
              {!isEdit && dupes.length > 0 && (
                <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />} title="A similar record already exists" style={{ gridColumn: '1 / -1' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
                    {dupes.map((m) => (
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
                    ))}
                  </div>
                </Alert>
              )}
              <FormField label="Date of Birth" required error={fieldError('birth_date')}
                hint={!isEdit ? 'The child must be 5 to 17. For a foundling, the estimated date.' : undefined}>
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
                  {withRetired(BIRTH_STATUSES, form.birth_status).map((v) => <option key={v} value={v}>{optionLabel(BIRTH_STATUSES, v)}</option>)}
                </Select>
              </FormField>
              <FormField label="Legal Status" hint="Leave blank if none has been issued yet." error={fieldError('legal_status')}>
                <Select value={form.legal_status || ''} onChange={(e) => setForm({ ...form, legal_status: e.target.value })}>
                  <option value="">— Select —</option>
                  {withRetired(LEGAL_STATUSES, form.legal_status).map((v) => <option key={v} value={v}>{optionLabel(LEGAL_STATUSES, v)}</option>)}
                </Select>
              </FormField>
              {/* Moved here from Recommendation (24 Sep 2026): every child has
                  an answer, even if the answer is that they are not in school. */}
              <FormField label="Educational Placement" required error={fieldError('education_level')}
                hint="The grade level, or “Not in school”.">
                <Input value={form.education_level || ''} maxLength={100} placeholder="e.g. Grade 4"
                  onChange={(e) => setForm({ ...form, education_level: e.target.value })} />
              </FormField>
              {asksFor('surrendered_by') && (
                /* Typed, not picked: staff record who actually had the child
                   - a name, a relationship, an office - which no short list
                   covers. */
                <FormField label="Previous Custodian" required error={fieldError('surrendered_by')}
                  hint="Who had the child before — a name, relationship or office.">
                  <Input value={form.surrendered_by || ''} maxLength={150}
                    placeholder="e.g. Rosa Dela Cruz (maternal aunt)"
                    onChange={(e) => setForm({ ...form, surrendered_by: e.target.value })} />
                </FormField>
              )}
              {asksFor('type_of_adoption') && (
                <FormField label="Type of Adoption" required error={fieldError('type_of_adoption')}>
                  <Select value={form.type_of_adoption || ''} onChange={(e) => changeAdoptionType(e.target.value)}>
                    <option value="">— Select —</option>
                    {withRetired(TYPES_OF_ADOPTION, form.type_of_adoption).map((v) => <option key={v} value={v}>{optionLabel(TYPES_OF_ADOPTION, v)}</option>)}
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
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 4 }}>Address</div>
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
              <FormField label="Barangay" required error={fieldError('barangay')} hint={brgys.length ? `${brgys.length} in this municipality` : undefined}>
                <Select value={form.psgc_barangay || ''} disabled={!form.psgc_municipality} onChange={(e) => pickPlace('barangay', e.target.value, brgys)}>
                  <option value="">{form.psgc_municipality ? '— Select barangay —' : 'Select a municipality first'}</option>
                  {brgys.map((b) => <option key={b.psgc_code} value={b.psgc_code}>{b.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Municipality / City" required error={fieldError('municipality')}>
                <Select value={form.psgc_municipality || ''} disabled={!form.psgc_province} onChange={(e) => pickPlace('municipality', e.target.value, munis)}>
                  <option value="">{form.psgc_province ? '— Select municipality —' : 'Select a province first'}</option>
                  {munis.map((m) => <option key={m.psgc_code} value={m.psgc_code}>{m.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Province" required error={fieldError('province')}>
                <Select value={form.psgc_province || ''} onChange={(e) => pickPlace('province', e.target.value, provinces)}>
                  <option value="">— Select province —</option>
                  {provinces.map((p) => <option key={p.psgc_code} value={p.psgc_code}>{p.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Landmark" hint="Optional — anything that helps find the house." error={fieldError('landmark')}>
                <Input value={form.landmark || ''} maxLength={200} onChange={(e) => setForm({ ...form, landmark: e.target.value })} />
              </FormField>
              {/* An address typed before the picker existed has no code, so the
                  selects above sit empty and would look like a blank address.
                  Show what the record actually says. */}
              {!form.psgc_province && (form.province || form.municipality || form.barangay) && (
                <div style={{ gridColumn: '1 / -1', fontSize: 12.5, color: 'var(--text-muted)', padding: '10px 12px', background: 'var(--ink-50)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
                  Recorded before the address list existed:{' '}
                  <strong style={{ color: 'var(--text-strong)' }}>
                    {[form.barangay, form.municipality, form.province].filter(Boolean).join(', ')}
                  </strong>
                  . Re-pick it above to attach the official codes — the text stays either way.
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
              {/* A pick since 24 Sep 2026. Current Whereabouts, which sat
                  beside it, was taken off the form the same day. */}
              <FormField label="Referral Source" error={fieldError('referral_source')}
                hint="RACCO · LGU (local government unit) · CCA (child caring agency) · RCF (residential care facility)">
                <Select value={form.referral_source || ''} onChange={(e) => setForm({ ...form, referral_source: e.target.value })}>
                  <option value="">— Select —</option>
                  {withRetired(REFERRAL_SOURCES, form.referral_source).map((v) => <option key={v} value={v}>{optionLabel(REFERRAL_SOURCES, v)}</option>)}
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
                hint={form.id
                  ? 'Already on file? Add or replace it from the child’s record.'
                  : 'PDF or Word. The social worker’s referral — sessions cannot be booked without one, though the record saves either way.'}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <input
                    id="case-referral-file"
                    type="file"
                    /* Exactly what CaseReferralSerializer.validate_file
                       accepts. Offering a .png in the picker and then
                       refusing it on the server is the screen lying. */
                    accept=".pdf,.doc,.docx"
                    onChange={(e) => setForm({ ...form, referralFile: e.target.files?.[0] || null })}
                    style={{ fontFamily: 'var(--font-sans)', fontSize: 13, color: 'var(--text-body)' }}
                  />
                  {form.referralFile && (
                    <Badge tone="success" size="sm">{form.referralFile.name}</Badge>
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
                <FormField label="Assign Psychologist">
                  <Select value={form.psychologist || ''} onChange={(e) => setForm({ ...form, psychologist: e.target.value })}>
                    <option value="">— Unassigned —</option>
                    {psychologists.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.caseload} case{p.caseload === 1 ? '' : 's'}</option>)}
                  </Select>
                </FormField>
                {psychologists.length > 0 && (
                  <div style={{ marginTop: 10, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 12, background: 'var(--ink-50)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div className="racco-eyebrow" style={{ fontSize: 10 }}>Availability — check before you assign</div>
                    {psychologists.map((p) => {
                      const av = availFor(p.id);
                      const on = String(form.psychologist) === String(p.id);
                      return (
                        <button type="button" key={p.id}
                          onClick={() => setForm({ ...form, psychologist: String(p.id) })}
                          aria-pressed={on}
                          style={{ textAlign: 'left', padding: '9px 11px', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontFamily: 'var(--font-sans)', border: `1px solid ${on ? 'var(--blue-500)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : 'var(--surface)', transition: 'var(--transition-base)' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 5 }}>
                            <span style={{ fontWeight: 700, fontSize: 13, color: on ? 'var(--blue-700)' : 'var(--text-strong)' }}>{p.name}</span>
                            <Badge tone={p.caseload >= 5 ? 'amber' : 'neutral'} size="sm">{p.caseload} case{p.caseload === 1 ? '' : 's'}</Badge>
                          </div>
                          {av.length === 0 ? (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, color: 'var(--amber-600)', fontWeight: 600 }}>
                              <Icon name="alert-triangle" size={12} /> No availability set — sessions can&apos;t be booked yet
                            </span>
                          ) : (
                            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                              {av.map((b) => <Badge key={b.id} tone="success" size="sm">{blockLabel(b)}</Badge>)}
                            </div>
                          )}
                        </button>
                      );
                    })}
                  </div>
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
          {missing.length > 0 && (isEdit || step === FORM_STEPS.length) && (
            <span role="status" style={{ alignSelf: 'center', display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12.5, color: isEdit ? 'var(--text-muted)' : 'var(--amber-700)', marginRight: 'auto', minWidth: 0 }}>
              {isEdit ? 'Still blank' : 'Still needed'}: {missing.join(', ')}
              {firstMissingStep && firstMissingStep !== step && (
                <Button type="button" variant="ghost" size="sm" onClick={() => setStep(firstMissingStep)}>Go to it</Button>
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
            <Button type="submit" variant="primary" disabled={!isEdit && !requiredFieldsFilled} iconLeft={<Icon name="save" size={16} />}>Save Record</Button>
          )}
        </div>
      </form>
    </div>
  );
}
