import React, { useEffect, useState } from 'react';
import api from '../../api/client';
import {
  Alert, Badge, Button, FormField, Icon, Input, Select, hoverLift, iconBtn,
} from '../../ui';
import {
  BIRTH_STATUSES, CASE_CATEGORIES, CASE_CATEGORY_OPTIONS, CASE_TYPES,
  CASE_TYPE_FIELDS, LEGAL_STATUSES, SURRENDERED_BY, TYPES_OF_ADOPTION,
} from '../../config/caseData';

/* The add/edit form for a child record — five steps, and the longest single
 * thing in this feature by a wide margin.
 *
 * It lived in Children.jsx, which was 1,102 lines and nine components. This
 * one accounts for 443 of them and reached for exactly two things outside
 * itself, EMPTY and FORM_STEPS, so it moved with both and nothing else
 * changed. The page now imports it; every prop and every line of its body is
 * as it was.
 */

export const EMPTY = {
  first_name: '', middle_initial: '', last_name: '',
  birth_date: '', gender: '', province: '', municipality: '', barangay: '', psgc_province: '', psgc_municipality: '', psgc_barangay: '',
  case_type: '', case_category: '', surrendered_by: '', psychologist: '', assignee_sees_history: true,
  place_of_birth_or_found: '', birth_status: '', legal_status: '',
  date_of_admission: '', date_of_placement_to_custodian: '', type_of_adoption: '',
  referral_source: '', referral_reason: '', education_level: '', current_placement: '', medical_notes: '',
  recommendation: '',
};


const FORM_STEPS = ['Identity', 'Address', 'Case', 'Recommendation', 'Assignment'];


export default function ChildForm({ form, setForm, draftKey, psychologists, blocks = [], error, isPsych = false, canReopen = false, others = [], onSubmit, onClose, onReopen, onOpenExisting }) {
  const [step, setStep] = useState(1);
  // Reopening the form for a different record starts at the beginning again.
  useEffect(() => { setStep(1); }, [form.id]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const isEdit = !!form.id;
  // Draft autosave (create mode only) — debounced write to localStorage so an
  // accidental modal close (or crash) never loses a half-typed intake record.
  useEffect(() => {
    if (form.id) return; // edits are server-backed; drafts are create-only
    const t = setTimeout(() => {
      const data = { ...form };
      delete data._draft; delete data._conflict;
      // A File does not survive JSON.stringify — it serialises to {}, which is
      // TRUTHY on the way back in. The restored draft would then show a chip
      // with no filename and try to upload an empty object. A chosen file is
      // not something a draft can hold, so it is not kept.
      delete data.referralFile;
      if (Object.entries(data).some(([k, v]) => k !== 'assignee_sees_history' && v)) {
        try { localStorage.setItem(draftKey, JSON.stringify(data)); } catch { /* storage full */ }
      }
    }, 500);
    return () => clearTimeout(t);
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
  /* The Case step asks different questions per track — a Type of Adoption on a
   * reunification case is a field nobody can answer, and one more thing to skip
   * past on every intake. */
  const caseFields = CASE_TYPE_FIELDS[form.case_type] || [];
  const asksFor = (field) => caseFields.includes(field);
  const categoryOptions = CASE_CATEGORY_OPTIONS[form.case_type] || CASE_CATEGORIES;

  /* Changing the track clears anything the new one does not ask for, so a case
   * switched from Adoption to Independent Living cannot keep a stale Type of
   * Adoption that no screen will ever show again. */
  const changeCaseType = (nextType) => {
    const nextFields = CASE_TYPE_FIELDS[nextType] || [];
    const nextCategories = CASE_CATEGORY_OPTIONS[nextType] || CASE_CATEGORIES;
    setForm({
      ...form,
      case_type: nextType,
      case_category: nextCategories.includes(form.case_category) ? form.case_category : '',
      surrendered_by: nextFields.includes('surrendered_by') ? form.surrendered_by : '',
      date_of_placement_to_custodian: nextFields.includes('date_of_placement_to_custodian') ? form.date_of_placement_to_custodian : '',
      type_of_adoption: nextFields.includes('type_of_adoption') ? form.type_of_adoption : '',
    });
  };

  const requiredFieldsFilled = form.first_name && form.last_name && form.birth_date && form.gender && form.case_type;
  // Named, not just disabled: the required fields live on two different steps,
  // so a greyed-out Save with no explanation sends people hunting.
  const missing = [
    [!form.first_name, 'first name'], [!form.last_name, 'last name'],
    [!form.birth_date, 'date of birth'], [!form.gender, 'sex'],
    [!form.case_type, 'case type'],
  ].filter(([m]) => m).map(([, label]) => label);
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, zIndex: 70, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
      <form onSubmit={onSubmit} onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(980px, 96vw)', height: 'min(86vh, 820px)', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--ink-50)' }}>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{isEdit ? 'Edit Record' : 'Add Record'}</div>
          <button type="button" onClick={onClose} aria-label="Close" {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--text-muted)')}><Icon name="x" size={17} /></button>
        </div>
        {others.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 24px', background: 'var(--blue-50)', borderBottom: '1px solid var(--blue-100)' }}>
            <Icon name="users" size={14} style={{ color: 'var(--blue-600)' }} />
            {others.map((o, i) => <Badge key={i} tone="brand" size="sm" dot>{o.name} ({o.role}) is here</Badge>)}
          </div>
        )}
        <div className="racco-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {form._draft && (
            <Alert tone="info" icon={<Icon name="history" size={18} />} title="Unsaved draft found">
              You started a record earlier that wasn&apos;t saved. Continue where you left off?
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <Button variant="secondary" size="sm" onClick={() => setForm({ ...EMPTY, ...form._draft, _draft: null })} iconLeft={<Icon name="rotate-ccw" size={14} />}>Restore draft</Button>
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
                const done = step > i + 1;
                return (
                  <React.Fragment key={label}>
                    <button
                      type="button" onClick={() => setStep(i + 1)}
                      aria-current={active ? 'step' : undefined}
                      style={{ padding: '8px 12px', borderRadius: 'var(--radius-pill)', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12.5, border: `1px solid ${active ? 'var(--blue-500)' : done ? 'var(--success-500)' : 'var(--border)'}`, background: active ? 'var(--blue-50)' : done ? 'var(--success-50)' : 'var(--surface)', color: active ? 'var(--blue-700)' : done ? 'var(--success-700)' : 'var(--text-muted)' }}
                    >{label}</button>
                    {i < FORM_STEPS.length - 1 && <span style={{ color: 'var(--text-faint)', fontSize: 13, fontWeight: 700 }}>—</span>}
                  </React.Fragment>
                );
              })}
            </div>
          </div>

          {step === 1 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 10 }}>Identity</div>
            <div className="racco-case-grid">
              {/* Child name is not editable once a record exists (adviser). */}
              {isEdit ? (
                <div style={{ gridColumn: '1 / -1' }}>
                  <div style={{ ...fieldLabel, marginBottom: 6 }}>Full Name</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 13px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)', color: 'var(--text-strong)', fontWeight: 700, fontSize: 14 }}>
                    {form.fullname}
                    <Icon name="lock" size={13} style={{ color: 'var(--text-faint)', marginLeft: 'auto' }} />
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 5 }}>The child&apos;s name cannot be changed after the record is created.</div>
                </div>
              ) : (
                <div style={{ gridColumn: '1 / -1', display: 'grid', gridTemplateColumns: '2fr 64px 2fr', gap: 10 }}>
                  <FormField label="First Name" required>
                    <Input value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} required />
                  </FormField>
                  <FormField label="M.I.">
                    <Input value={form.middle_initial} maxLength={3} onChange={(e) => setForm({ ...form, middle_initial: e.target.value })} />
                  </FormField>
                  <FormField label="Last Name" required>
                    <Input value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} required />
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
              <FormField label="Date of Birth" required={!isEdit}>
                <Input type="date" value={form.birth_date || ''} min={!isEdit ? minBirthDate : undefined} max={!isEdit ? maxBirthDate : undefined} onChange={(e) => setForm({ ...form, birth_date: e.target.value })} required={!isEdit} />
              </FormField>
              <FormField label="Sex" required={!isEdit}>
                <Select value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })} required={!isEdit}>
                  <option value="">—</option><option>Male</option><option>Female</option>
                </Select>
              </FormField>
              <FormField label="Place of Birth or Place Found">
                <Input value={form.place_of_birth_or_found || ''} onChange={(e) => setForm({ ...form, place_of_birth_or_found: e.target.value })} />
              </FormField>
              <FormField label="Birth Status">
                <Select value={form.birth_status || ''} onChange={(e) => setForm({ ...form, birth_status: e.target.value })}>
                  <option value="">— Select —</option>
                  {BIRTH_STATUSES.map((s) => <option key={s}>{s}</option>)}
                </Select>
              </FormField>
            </div>
          </section>
          )}

          {step === 2 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 10 }}>Address</div>
            <div className="racco-case-grid">
              <FormField label="Province">
                <Select value={form.psgc_province || ''} onChange={(e) => pickPlace('province', e.target.value, provinces)}>
                  <option value="">— Select province —</option>
                  {provinces.map((p) => <option key={p.psgc_code} value={p.psgc_code}>{p.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Municipality / City">
                <Select value={form.psgc_municipality || ''} disabled={!form.psgc_province} onChange={(e) => pickPlace('municipality', e.target.value, munis)}>
                  <option value="">{form.psgc_province ? '— Select municipality —' : 'Select a province first'}</option>
                  {munis.map((m) => <option key={m.psgc_code} value={m.psgc_code}>{m.name}</option>)}
                </Select>
              </FormField>
              <FormField label="Barangay" hint={brgys.length ? `${brgys.length} in this municipality` : undefined}>
                <Select value={form.psgc_barangay || ''} disabled={!form.psgc_municipality} onChange={(e) => pickPlace('barangay', e.target.value, brgys)}>
                  <option value="">{form.psgc_municipality ? '— Select barangay —' : 'Select a municipality first'}</option>
                  {brgys.map((b) => <option key={b.psgc_code} value={b.psgc_code}>{b.name}</option>)}
                </Select>
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
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 10 }}>Case</div>
            <div className="racco-case-grid">
              <FormField label="Case Type" required={!isEdit}>
                <Select value={form.case_type || ''} onChange={(e) => changeCaseType(e.target.value)} required={!isEdit}>
                  <option value="">— Select case type —</option>
                  {CASE_TYPES.map((t) => <option key={t}>{t}</option>)}
                </Select>
              </FormField>
              <FormField label="Category">
                <Select value={form.case_category || ''} onChange={(e) => setForm({ ...form, case_category: e.target.value })}>
                  <option value="">— Select category —</option>
                  {categoryOptions.map((c) => <option key={c}>{c}</option>)}
                </Select>
              </FormField>
              {asksFor('surrendered_by') && (
                <FormField label="Previous Custodian">
                  <Select value={form.surrendered_by || ''} onChange={(e) => setForm({ ...form, surrendered_by: e.target.value })}>
                    <option value="">— Select —</option>
                    {SURRENDERED_BY.map((s) => <option key={s}>{s}</option>)}
                  </Select>
                </FormField>
              )}
              <FormField label="Legal Status" hint="With issued CDCLAA / IVC / judicially declared abandoned">
                <Select value={form.legal_status || ''} onChange={(e) => setForm({ ...form, legal_status: e.target.value })}>
                  <option value="">— Select —</option>
                  {LEGAL_STATUSES.map((s) => <option key={s}>{s}</option>)}
                </Select>
              </FormField>
              <FormField label="Date of Admission to the Agency">
                <Input type="date" value={form.date_of_admission || ''} onChange={(e) => setForm({ ...form, date_of_admission: e.target.value })} />
              </FormField>
              {asksFor('date_of_placement_to_custodian') && (
                <FormField label="Date of Placement to Custodian" hint="For Relative/Stepparent/Adult/FA/IP">
                  <Input type="date" value={form.date_of_placement_to_custodian || ''} onChange={(e) => setForm({ ...form, date_of_placement_to_custodian: e.target.value })} />
                </FormField>
              )}
              {asksFor('type_of_adoption') && (
                <FormField label="Type of Adoption">
                  <Select value={form.type_of_adoption || ''} onChange={(e) => setForm({ ...form, type_of_adoption: e.target.value })}>
                    <option value="">— Select —</option>
                    {TYPES_OF_ADOPTION.map((t) => <option key={t}>{t}</option>)}
                  </Select>
                </FormField>
              )}
            </div>
          </section>
          )}

          {step === 4 && (
          <section>
            <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 4 }}>Recommendation</div>
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>Details beyond the agency&apos;s intake interview.</div>
            <div className="racco-case-grid">
              <FormField label="Referral Source" hint="Agency, LGU, or person who referred the child.">
                <Input value={form.referral_source || ''} onChange={(e) => setForm({ ...form, referral_source: e.target.value })} />
              </FormField>
              <FormField label="Educational Placement">
                <Input value={form.education_level || ''} onChange={(e) => setForm({ ...form, education_level: e.target.value })} placeholder="e.g. Grade 4" />
              </FormField>
              <FormField label="Current Whereabouts">
                <Input value={form.current_placement || ''} onChange={(e) => setForm({ ...form, current_placement: e.target.value })} placeholder="e.g. Foster family, residential facility" />
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

          {step === 5 && (
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
        <div style={{ padding: '14px 24px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          {step > 1 && (
            <Button type="button" variant="ghost" onClick={() => setStep((n) => n - 1)} iconLeft={<Icon name="arrow-left" size={15} />}>Back</Button>
          )}
          {step < FORM_STEPS.length && (
            <Button type="button" variant={isEdit ? 'secondary' : 'primary'} onClick={() => setStep((n) => n + 1)}>Next</Button>
          )}
          {/* Editing can save from any step — walking five pages to correct a
              phone number is the kind of thing that makes people avoid the form.
              Creating still has to reach the end, so nothing is missed. */}
          {!isEdit && missing.length > 0 && step === FORM_STEPS.length && (
            <span style={{ alignSelf: 'center', fontSize: 12.5, color: 'var(--text-muted)' }}>
              Still needed: {missing.join(', ')}
            </span>
          )}
          {(isEdit || step === FORM_STEPS.length) && (
            <Button type="submit" variant="primary" disabled={!isEdit && !requiredFieldsFilled} iconLeft={<Icon name="save" size={16} />}>Save Record</Button>
          )}
        </div>
      </form>
    </div>
  );
}
