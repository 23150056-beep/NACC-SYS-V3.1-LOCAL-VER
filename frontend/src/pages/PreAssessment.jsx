import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useToast } from '../context/ToastContext';
import { useAuth } from '../context/AuthContext';
import { caseRef } from '../utils/child';
import { Card, Button, Badge, Input, Select, FormField, FileUpload, Alert, EmptyState, Avatar, Icon, iconBtn, hoverLift, PAGE } from '../ui';
import { PA_STATUSES, PA_STATUS_TONES } from '../config/caseData';
import { printBlankForm } from '../utils/printForm';
import InstrumentFormDrawer, { EMPTY_INSTRUMENT } from '../components/InstrumentFormDrawer';

const STEPS = ['Child', 'Consent', 'Interview', 'Instruments', 'Problems', 'Complete'];

const textarea = { width: '100%', resize: 'vertical', padding: '11px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.55 };

function FormBody({ body }) {
  if (!body) return null;
  return (
    <div className="racco-scroll" style={{ maxHeight: 260, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: '14px 16px', background: 'var(--ink-50)', marginBottom: 14 }}>
      {body.split('\n').map((line, i) => {
        const s = line.trim();
        if (!s) return null;
        if (s.startsWith('## ')) return <div key={i} style={{ fontWeight: 800, fontSize: 12.5, color: 'var(--text-strong)', margin: '12px 0 4px', letterSpacing: '0.02em' }}>{s.slice(3)}</div>;
        return <p key={i} style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 8px', lineHeight: 1.6 }}>{s}</p>;
      })}
    </div>
  );
}

export default function PreAssessment() {
  const toast = useToast();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [step, setStep] = useState(0);
  const [maxStep, setMaxStep] = useState(0);
  const [children, setChildren] = useState([]);
  const [statusFilter, setStatusFilter] = useState('all'); // pipeline-status chip
  const [child, setChild] = useState(null);
  const [pa, setPa] = useState(null); // the in-progress PreAssessment
  const [consents, setConsents] = useState([]);
  const [consentTemplates, setConsentTemplates] = useState([]);
  const [interviewTemplates, setInterviewTemplates] = useState([]);
  const [instruments, setInstruments] = useState([]);
  const [selectedInstruments, setSelectedInstruments] = useState([]);
  const [problems, setProblems] = useState([]);
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [instForm, setInstForm] = useState(null); // instrument add/edit drawer
  const [instError, setInstError] = useState('');

  useEffect(() => {
    api.get('/children/').then((r) => setChildren(r.data.filter((c) => c.status === 'active'))).catch(() => {});
    api.get('/form-templates/?type=consent').then((r) => setConsentTemplates(r.data)).catch(() => {});
    api.get('/form-templates/?type=clinical_interview').then((r) => setInterviewTemplates(r.data)).catch(() => {});
    api.get('/instruments/').then((r) => setInstruments(r.data)).catch(() => {});
  }, []);

  const goToStep = (i) => { if (i <= maxStep) setStep(i); };

  const advanceTo = (i) => { setMaxStep((m) => Math.max(m, Math.floor(i))); setStep(i); };

  const resumeStepFor = (data) => {
    if (!data.consent) return 1;
    if (!data.interview) return 2;
    if ((data.instruments || []).length === 0) return 3;
    return 4;
  };

  const start = async (c) => {
    setError('');
    try {
      const { data } = await api.post('/pre-assessments/', { child: c.id });
      setChild(c); setPa(data);
      setSelectedInstruments(data.instruments || []);
      setNotes(data.notes || '');
      const existing = await api.get(`/consents/?child=${c.id}`);
      setConsents(existing.data);
      advanceTo(resumeStepFor(data));
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not start the pre-assessment.');
    }
  };

  const patchPa = async (patch) => {
    const { data } = await api.patch(`/pre-assessments/${pa.id}/`, patch);
    setPa(data);
    return data;
  };

  const linkConsent = async (consentId) => {
    setError('');
    try { await patchPa({ consent: consentId }); advanceTo(2); }
    catch (err) { setError(JSON.stringify(err.response?.data || 'Could not link consent.')); }
  };

  const saveInstruments = async () => {
    setError('');
    if (selectedInstruments.length === 0) { setError('Select at least one instrument title.'); return; }
    try { await patchPa({ instruments: selectedInstruments }); advanceTo(4); }
    catch (err) { setError(JSON.stringify(err.response?.data || 'Could not save instruments.')); }
  };

  // Instrument catalog module embedded in step 4 — add/edit titles inline
  // without leaving the wizard.
  const reloadInstruments = () => api.get('/instruments/').then((r) => setInstruments(r.data)).catch(() => {});
  const saveInstrument = async () => {
    setInstError('');
    if (!instForm.title.trim()) { setInstError('Title is required.'); return; }
    const payload = { ...instForm };
    delete payload.owner; delete payload.owner_name; delete payload.updated_at;
    try {
      if (instForm.id) await api.put(`/instruments/${instForm.id}/`, payload);
      else await api.post('/instruments/', payload);
      toast.success(instForm.id ? 'Instrument updated' : 'Instrument added');
      setInstForm(null); reloadInstruments();
    } catch (err) { setInstError(JSON.stringify(err.response?.data || 'Save failed')); }
  };

  const complete = async () => {
    setError('');
    try {
      if (notes.trim()) await patchPa({ notes: notes.trim() });
      const { data } = await api.post(`/pre-assessments/${pa.id}/complete/`);
      setPa(data);
      toast.success('Pre-assessment completed');
      advanceTo(5);
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Could not complete.'));
    }
  };

  return (
    <div style={{ ...PAGE, maxWidth: 880 }}>
      {/* Who this is, and how far through. A wizard's worst failure is the
          person losing track of which child they are recording — so the name
          sits above the stepper on every step, not only the first. */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 13 }}>
        {child ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Avatar name={child.fullname} tone="brand" size={38} />
            <span style={{ flex: 1, minWidth: 160 }}>
              <span style={{ display: 'block', fontWeight: 800, fontSize: 15.5, color: 'var(--text-strong)' }}>{child.fullname}</span>
              <span style={{ display: 'block', fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>
                {caseRef(child.id)} · {child.case_type || 'No case type'} · {child.psychologist_name || 'unassigned'}
              </span>
            </span>
            <Badge tone={step >= 5 ? 'success' : 'warning'} size="md">
              {step >= 5 ? 'Complete' : `In progress · step ${step + 1} of ${STEPS.length}`}
            </Badge>
          </div>
        ) : (
          <div>
            <h2 style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 17, lineHeight: 1.2, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>Pre-Assessment</h2>
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 }}>Six steps: consent, interview, instrument titles, presenting problems, then close the flow.</p>
          </div>
        )}

        {/* Step rail — click any visited step to look back at it. */}
        <div style={{ display: 'flex', alignItems: 'center' }}>
          {STEPS.map((s, i) => {
            const done = i < step;
            const now = i === step;
            const reachable = i <= maxStep && i < 5;
            const barBefore = i === 0 ? 'transparent' : i <= step ? 'var(--blue-600)' : 'var(--divider)';
            const barAfter = i === STEPS.length - 1 ? 'transparent' : i < step ? 'var(--blue-600)' : 'var(--divider)';
            return (
              <div key={s} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                  <span style={{ flex: 1, height: 3, background: barBefore }} />
                  <button
                    type="button" onClick={() => reachable && goToStep(i)} disabled={!reachable}
                    title={reachable ? `Go to ${s}` : `${s} — not reached yet`} aria-current={now ? 'step' : undefined}
                    style={{
                      width: 28, height: 28, flex: 'none', borderRadius: '50%',
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      background: done ? 'var(--success-500)' : now ? 'var(--blue-600)' : 'var(--surface)',
                      color: done || now ? '#fff' : 'var(--text-faint)',
                      border: `2px solid ${done ? 'var(--success-500)' : now ? 'var(--blue-600)' : 'var(--border-strong)'}`,
                      fontFamily: 'var(--font-mono)', fontWeight: 800, fontSize: 11.5,
                      cursor: reachable ? 'pointer' : 'default', padding: 0,
                    }}
                  >
                    {done ? <Icon name="check" size={15} /> : i + 1}
                  </button>
                  <span style={{ flex: 1, height: 3, background: barAfter }} />
                </div>
                <span style={{ fontWeight: now ? 800 : 600, fontSize: 11.5, color: now ? 'var(--text-strong)' : 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>{s}</span>
              </div>
            );
          })}
        </div>
      </div>

      {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}

      {step === 0 && (() => {
        // Categorized picker: chip filter + pipeline-order sort (earliest
        // stage first — the children with the most work left float to the top).
        const counts = children.reduce((acc, c) => {
          acc[c.pre_assessment_status] = (acc[c.pre_assessment_status] || 0) + 1;
          return acc;
        }, {});
        const visible = children
          .filter((c) => statusFilter === 'all' || c.pre_assessment_status === statusFilter)
          .sort((a, b) => PA_STATUSES.indexOf(a.pre_assessment_status) - PA_STATUSES.indexOf(b.pre_assessment_status)
            || a.fullname.localeCompare(b.fullname, undefined, { sensitivity: 'base' }));
        const chips = [{ key: 'all', label: 'All', count: children.length },
          ...PA_STATUSES.map((s) => ({ key: s, label: s, count: counts[s] || 0 }))];
        return (
          <Card eyebrow="Step 1" title="Select a child" padding="16px">
            <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 14px' }}>
              Start a guided pre-assessment for one of your assigned children.
            </p>
            {children.length === 0 ? (
              <EmptyState icon={<Icon name="users" size={24} />} title="No assigned children" description="Ask the administrator or staff to assign children to you." />
            ) : (
              <>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
                  {chips.map((f) => (
                    <button key={f.key} onClick={() => setStatusFilter(f.key)}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 12px', borderRadius: 'var(--radius-pill)', fontSize: 12, fontWeight: 700, cursor: 'pointer', fontFamily: 'var(--font-sans)', background: statusFilter === f.key ? 'var(--blue-600)' : 'var(--ink-50)', color: statusFilter === f.key ? '#fff' : 'var(--text-muted)', border: `1px solid ${statusFilter === f.key ? 'var(--blue-600)' : 'var(--border)'}` }}>
                      {f.label}
                      <span className="racco-mono" style={{ fontSize: 11, opacity: 0.85 }}>{f.count}</span>
                    </button>
                  ))}
                </div>
                {visible.length === 0 ? (
                  <div style={{ fontSize: 13, color: 'var(--text-muted)', padding: '10px 2px' }}>
                    No children in “{statusFilter}”.
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {visible.map((c) => (
                      <button key={c.id} onClick={() => start(c)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)', background: 'var(--surface)', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)', transition: 'var(--transition-base)' }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--blue-50)')} onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--surface)')}>
                        <Avatar name={c.fullname} tone="brand" size="sm" />
                        <div style={{ flex: 1 }}>
                          <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{c.fullname}</div>
                          <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{c.case_type || 'No case type'} · Pre-assessment: {c.pre_assessment_status}</div>
                        </div>
                        <Badge tone={PA_STATUS_TONES[c.pre_assessment_status] || 'neutral'} size="sm" dot>{c.pre_assessment_status}</Badge>
                        <Icon name="chevron-right" size={16} style={{ color: 'var(--text-faint)' }} />
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </Card>
        );
      })()}

      {step === 1 && (
        <ConsentStep child={child} consents={consents} templates={consentTemplates}
          onLinked={linkConsent} onRefresh={async () => {
            const r = await api.get(`/consents/?child=${child.id}`); setConsents(r.data);
          }} setError={setError} />
      )}

      {step === 2 && (
        <InterviewStep child={child} templates={interviewTemplates} setError={setError}
          onDone={async (interviewId) => {
            try {
              if (interviewId) await patchPa({ interview: interviewId });
              advanceTo(3);
            } catch (err) { setError(JSON.stringify(err.response?.data || 'Could not link interview.')); }
          }} />
      )}

      {step === 3 && (
        <Card eyebrow="Step 4" title="Select instrument titles" padding="16px">
          <Alert disclaimer style={{ marginBottom: 14 }} title="Paper administration.">
            Instruments are administered offline using the psychologist&apos;s own printed materials. The system records titles only.
          </Alert>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 14 }}>
            <div className="racco-eyebrow" style={{ fontSize: 11 }}>Instrument catalog</div>
            <Button variant="secondary" onClick={() => { setInstError(''); setInstForm({ ...EMPTY_INSTRUMENT }); }} iconLeft={<Icon name="plus" size={15} />}>Add instrument title</Button>
          </div>

          {instruments.length === 0 ? (
            <EmptyState icon={<Icon name="clipboard-pen" size={24} />} title="Your catalog is empty" description="Add the instrument titles you administer, right here." />
          ) : (
            <div style={{ marginBottom: 16 }}>
              {[
                ['For children', instruments.filter((i) => i.audience !== 'adoptive_parent')],
                ['For prospective adoptive parents', instruments.filter((i) => i.audience === 'adoptive_parent' || i.audience === 'both')],
              ].map(([label, list]) => (
                <div key={label} style={{ marginBottom: 16 }}>
                  <div className="racco-eyebrow" style={{ fontSize: 10.5, marginBottom: 8 }}>{label}</div>
                  {list.length === 0 ? (
                    <div style={{ fontSize: 12.5, color: 'var(--text-faint)', padding: '4px 2px 8px' }}>None yet.</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {list.map((i) => {
                        const on = selectedInstruments.includes(i.id);
                        const owned = String(i.owner) === String(user?.id);
                        return (
                          <div key={i.id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <label style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 11, padding: '11px 14px', borderRadius: 'var(--radius-lg)', border: `1px solid ${on ? 'var(--blue-400)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : 'var(--surface)', cursor: 'pointer' }}>
                              <input type="checkbox" checked={on} style={{ accentColor: 'var(--blue-600)' }}
                                onChange={() => setSelectedInstruments((s) => on ? s.filter((x) => x !== i.id) : [...s, i.id])} />
                              <div style={{ flex: 1 }}>
                                <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{i.title}</div>
                                <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{[i.publisher, i.age_range && `ages ${i.age_range}`].filter(Boolean).join(' · ') || '—'}</div>
                              </div>
                              <Badge tone="neutral" size="sm">{i.category}</Badge>
                            </label>
                            {owned && (
                              <button title="Edit" onClick={() => { setInstError(''); setInstForm({ ...i, owner: i.owner || '' }); }} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--blue-600)')}><Icon name="pencil" size={15} /></button>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
            <Button variant="primary" onClick={saveInstruments} disabled={selectedInstruments.length === 0} iconLeft={<Icon name="check" size={16} />}>Save & continue</Button>
          </div>
        </Card>
      )}

      {step === 4 && (
        <ProblemsStep child={child} problems={problems} setProblems={setProblems} setError={setError} onNext={() => advanceTo(4.5)} />
      )}
      {step === 4.5 && (
        <Card eyebrow="Step 6" title="Review & complete" padding="16px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 16 }}>
            {[['Child', child?.fullname],
              ['Consent', pa?.consent ? `Linked (${pa.consent_status || 'signed'})` : 'Missing'],
              ['Clinical interview', pa?.interview ? 'Recorded' : 'Skipped'],
              ['Instruments', (pa?.instrument_titles || []).join(', ') || selectedInstruments.length + ' selected'],
              ['Problems logged', String(problems.length)]].map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, paddingBottom: 10, borderBottom: '1px solid var(--divider-row)' }}>
                <span style={{ fontSize: 13, color: 'var(--text-muted)', fontWeight: 600 }}>{k}</span>
                <span style={{ fontSize: 13.5, color: 'var(--text-strong)', fontWeight: 700, textAlign: 'right' }}>{v}</span>
              </div>
            ))}
          </div>
          <FormField label="Session notes (optional)">
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} style={textarea} placeholder="Observations during the session…" />
          </FormField>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 14 }}>
            <Button variant="primary" onClick={complete} iconLeft={<Icon name="check-circle-2" size={16} />}>Mark completed</Button>
          </div>
        </Card>
      )}

      {step === 5 && (
        <Card padding="28px">
          <div style={{ textAlign: 'center', padding: '16px 0' }}>
            <span style={{ display: 'inline-flex', width: 56, height: 56, borderRadius: '50%', background: 'var(--success-50)', color: 'var(--success-600)', alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}><Icon name="check-circle-2" size={28} /></span>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 20, color: 'var(--text-strong)' }}>Pre-assessment completed</div>
            <p style={{ fontSize: 13.5, color: 'var(--text-muted)', margin: '8px 0 20px' }}>
              {child?.fullname}&apos;s profile now shows “Answered” with the instrument titles used.
            </p>
            <div style={{ display: 'flex', justifyContent: 'center', gap: 10 }}>
              <Button variant="secondary" onClick={() => { setStep(0); setMaxStep(0); setPa(null); setChild(null); setSelectedInstruments([]); setProblems([]); setNotes(''); api.get('/children/').then((r) => setChildren(r.data.filter((c) => c.status === 'active'))); }}>Start another</Button>
              <Button variant="primary" onClick={() => navigate(`/report/child/${child.id}`)}>Open child report</Button>
            </div>
          </div>
        </Card>
      )}

      {instForm && (
        <InstrumentFormDrawer form={instForm} setForm={setInstForm} error={instError} onSave={saveInstrument} onClose={() => setInstForm(null)} />
      )}
    </div>
  );
}

function ConsentStep({ child, consents, templates, onLinked, onRefresh, setError }) {
  const toast = useToast();
  const [form, setForm] = useState({ template: '', signer_name: '', signer_relationship: '', fileObj: null });
  const [preview, setPreview] = useState(null); // { url, type, title }
  const [busy, setBusy] = useState(false);
  const tpl = templates.find((t) => String(t.id) === String(form.template));

  // Status is derived, never picked: an attached scan of the signed paper is
  // what makes a consent "signed"; without it the record stays pending.
  const derivedStatus = form.fileObj ? 'signed' : 'pending';

  const recordNew = async () => {
    setError('');
    if (!form.signer_name.trim()) { setError('Enter who accomplished the consent form.'); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('child', child.id);
      if (form.template) fd.append('template', form.template);
      fd.append('signer_name', form.signer_name);
      fd.append('signer_relationship', form.signer_relationship);
      fd.append('status', derivedStatus);
      if (form.fileObj) fd.append('scan', form.fileObj);
      const { data } = await api.post('/consents/', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      await onRefresh();
      if (data.status === 'signed') onLinked(data.id);
      else setError('Consent recorded as Pending — upload the scanned signed form to record a Signed consent and continue.');
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Could not record consent.'));
    } finally { setBusy(false); }
  };

  const openPreview = async (c) => {
    try {
      const res = await api.get(`/consents/${c.id}/download/`, { responseType: 'blob' });
      setPreview({ url: URL.createObjectURL(res.data), type: res.data.type, title: c.scan_filename || c.signer_name });
    } catch { toast.error('Could not load the file.'); }
  };
  const closePreview = () => { if (preview) URL.revokeObjectURL(preview.url); setPreview(null); };

  const th = { textAlign: 'left', padding: '9px 12px', fontSize: 10.5, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)', whiteSpace: 'nowrap' };
  const td = { padding: '9px 12px', fontSize: 12.5, color: 'var(--text-body)' };

  return (
    <Card eyebrow="Step 2" title={`Consent — ${child.fullname}`} padding="16px">
      <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 14px' }}>
        The agency&apos;s consent document is embedded below — read it with the guardian, then upload the scanned signed paper. The consent is marked Signed automatically once the scan is attached.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <FormField label="Consent form template" hint="Your agency-authored consent form.">
          <Select value={form.template} onChange={(e) => setForm({ ...form, template: e.target.value })}>
            <option value="">— None / generic —</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.title} (v{t.version})</option>)}
          </Select>
        </FormField>
        {tpl && (
          <div style={{ marginBottom: 4 }}>
            <FormBody body={tpl.body} />
            <Button variant="ghost" onClick={() => printBlankForm(tpl)} iconLeft={<Icon name="printer" size={15} />}>Print blank form</Button>
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <FormField label="Accomplished by" required><Input value={form.signer_name} onChange={(e) => setForm({ ...form, signer_name: e.target.value })} placeholder="Guardian’s full name" /></FormField>
          <FormField label="Relationship to child"><Input value={form.signer_relationship} onChange={(e) => setForm({ ...form, signer_relationship: e.target.value })} placeholder="e.g. Foster mother" /></FormField>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <FormField label="Status" hint={derivedStatus === 'signed'
            ? 'Set automatically — the signed scan is attached.'
            : 'Set automatically once the scanned signed form is uploaded.'}>
            <div style={{ display: 'flex', alignItems: 'center', height: 'var(--field-h)' }}>
              {derivedStatus === 'signed'
                ? <Badge tone="success" dot>Signed</Badge>
                : <Badge tone="amber" dot>Pending — awaiting signed form</Badge>}
            </div>
          </FormField>
          <FormField label="Scanned signed form" hint="PDF or photo of the signed paper — uploading it marks the consent as Signed.">
            <FileUpload file={form.fileObj} accept=".pdf,.jpg,.jpeg,.png"
              onChange={(f) => setForm({ ...form, fileObj: f })} />
          </FormField>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button variant="primary" onClick={recordNew} disabled={busy} iconLeft={<Icon name="save" size={16} />}>Record consent & continue</Button>
        </div>
      </div>

      {/* All consents on file — tabular, at the bottom */}
      <div style={{ marginTop: 20, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
        <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Consents on file ({consents.length})</div>
        {consents.length === 0 ? (
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No consents recorded for {child.fullname} yet.</div>
        ) : (
          <div className="racco-scroll" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 640, borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--ink-50)', borderBottom: '1px solid var(--border)' }}>
                {['Accomplished by', 'Relationship', 'Template', 'Date', 'Status', 'File', ''].map((h, i) => <th key={i} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {consents.map((c) => (
                  <tr key={c.id} style={{ borderBottom: '1px solid var(--divider-row)' }}>
                    <td style={{ ...td, fontWeight: 700, color: 'var(--text-strong)' }}>{c.signer_name || '—'}</td>
                    <td style={td}>{c.signer_relationship || '—'}</td>
                    <td style={td}>{c.template_title || '—'}</td>
                    <td style={td}>{c.date}</td>
                    <td style={td}><Badge tone={c.status === 'signed' ? 'success' : c.status === 'declined' ? 'amber' : 'neutral'} size="sm" dot>{c.status}</Badge></td>
                    <td style={td}>
                      {c.has_scan
                        ? <Button variant="ghost" onClick={() => openPreview(c)} iconLeft={<Icon name="eye" size={14} />}>Preview</Button>
                        : <span style={{ color: 'var(--text-faint)' }}>—</span>}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>
                      {c.status === 'signed' && (
                        <Button variant="secondary" onClick={() => onLinked(c.id)} iconLeft={<Icon name="check" size={14} />}>Use this consent</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Scan preview modal */}
      {preview && (
        <div onClick={closePreview} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 90 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 720, maxWidth: '94%', height: '86vh', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 15, color: 'var(--text-strong)' }}>Consent scan — {preview.title}</span>
              <button onClick={closePreview} aria-label="Close preview" style={iconBtn('var(--text-muted)')}><Icon name="x" size={16} /></button>
            </div>
            <div style={{ flex: 1, minHeight: 0, background: 'var(--ink-50)' }}>
              {preview.type.startsWith('image/')
                ? <img src={preview.url} alt="Consent scan" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                : (
                  /* sandbox with no allow-scripts. The src is a blob: URL,
                     and a blob inherits THIS page's origin — so anything
                     executable in it would run as the app and could read the
                     tokens in localStorage. The server now refuses to serve a
                     consent scan as anything but a PDF or an image, and this
                     is the second lock: a PDF still previews without scripts.
                     Do not add allow-scripts to make some viewer work. */
                  <iframe
                    title="Consent scan"
                    src={preview.url}
                    sandbox=""
                    referrerPolicy="no-referrer"
                    style={{ width: '100%', height: '100%', border: 'none' }}
                  />
                )}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

const RESPONDENT_OPTIONS = ['Custodian/PAP', 'Child', 'Guardian', 'Other…'];

function InterviewStep({ child, templates, onDone, setError }) {
  const [templateId, setTemplateId] = useState('');
  const [answers, setAnswers] = useState({});
  const [respondent, setRespondent] = useState('');
  const [respondentOther, setRespondentOther] = useState('');
  const [firstSavedId, setFirstSavedId] = useState(null);
  const [savedCount, setSavedCount] = useState(0);
  const [saving, setSaving] = useState(false);
  const tpl = templates.find((t) => String(t.id) === String(templateId));

  const respondentValue = respondent === 'Other…' ? respondentOther.trim() : respondent;

  const saveRecord = async () => {
    const { data } = await api.post('/interviews/', {
      child: child.id, template: templateId || null, answers,
      respondent: respondentValue,
    });
    if (firstSavedId === null) setFirstSavedId(data.id);
    setSavedCount((n) => n + 1);
    return data;
  };

  const resetForm = () => { setTemplateId(''); setAnswers({}); setRespondent(''); setRespondentOther(''); };

  const saveAndAnother = async () => {
    setError('');
    setSaving(true);
    try { await saveRecord(); resetForm(); }
    catch (err) { setError(JSON.stringify(err.response?.data || 'Could not save the interview.')); }
    finally { setSaving(false); }
  };

  const saveAndContinue = async () => {
    setError('');
    setSaving(true);
    try {
      const data = await saveRecord();
      onDone(firstSavedId ?? data.id);
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Could not save the interview.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card eyebrow="Step 3" title="Clinical interview" padding="16px">
      <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 14px' }}>
        Record the answers to your own Clinical Interview form, or skip if not conducted today.
        {savedCount > 0 && <strong> {savedCount} interview{savedCount > 1 ? 's' : ''} saved this session.</strong>}
      </p>
      <FormField label="Interview form template">
        <Select value={templateId} onChange={(e) => { setTemplateId(e.target.value); setAnswers({}); }}>
          <option value="">— Select template —</option>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.title} (v{t.version})</option>)}
        </Select>
      </FormField>
      {tpl && (
        <>
          <FormBody body={tpl.body} />
          <FormField label="Respondent" hint="Who is answering this interview.">
            <Select value={respondent} onChange={(e) => setRespondent(e.target.value)}>
              <option value="">—</option>
              {RESPONDENT_OPTIONS.map((r) => <option key={r}>{r}</option>)}
            </Select>
          </FormField>
          {respondent === 'Other…' && (
            <FormField label="Respondent (other)">
              <Input value={respondentOther} onChange={(e) => setRespondentOther(e.target.value)} placeholder="e.g. Teacher" maxLength={100} />
            </FormField>
          )}
        </>
      )}
      {tpl && (tpl.fields || []).map((f, idx) => (
        f.field_type === 'section' ? (
          <div key={`${f.label}-${idx}`} style={{ fontWeight: 800, fontSize: 12.5, color: 'var(--text-strong)', margin: '18px 0 6px', letterSpacing: '0.03em', textTransform: 'uppercase' }}>{f.label}</div>
        ) : (
        <FormField key={`${f.label}-${idx}`} label={f.label}>
          {f.field_type === 'long_text' ? (
            <textarea value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })} rows={3} style={textarea} />
          ) : f.field_type === 'date' ? (
            <Input type="date" value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })} />
          ) : f.field_type === 'yes_no' ? (
            <Select value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })}>
              <option value="">—</option><option>Yes</option><option>No</option>
            </Select>
          ) : f.field_type === 'choice' ? (
            <Select value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })}>
              <option value="">—</option>
              {(f.options || []).map((o) => <option key={o}>{o}</option>)}
            </Select>
          ) : (
            <Input value={answers[f.label] || ''} onChange={(e) => setAnswers({ ...answers, [f.label]: e.target.value })} />
          )}
        </FormField>
        )
      ))}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 14 }}>
        <Button variant="ghost" onClick={() => onDone(firstSavedId)} disabled={saving}>
          {savedCount > 0 ? 'Continue' : 'Skip for now'}
        </Button>
        <Button variant="ghost" onClick={saveAndAnother} disabled={!templateId || saving}>
          Save & interview another respondent
        </Button>
        <Button variant="primary" onClick={saveAndContinue} disabled={!templateId || saving} iconLeft={<Icon name="save" size={16} />}>
          Save interview & continue
        </Button>
      </div>
    </Card>
  );
}

function ProblemsStep({ child, problems, setProblems, setError, onNext }) {
  const [desc, setDesc] = useState('');
  const [cat, setCat] = useState('');

  const add = async () => {
    if (!desc.trim()) return;
    setError('');
    try {
      const { data } = await api.post('/problems/', { child: child.id, description: desc.trim(), category: cat.trim() });
      setProblems([...problems, data]);
      setDesc(''); setCat('');
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Could not log the problem.'));
    }
  };

  return (
    <Card eyebrow="Step 5" title="Problems encountered" padding="16px">
      <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 14px' }}>
        Log the problems observed in {child.fullname} during this session (optional).
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr auto', gap: 10, marginBottom: 14 }}>
        <Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Problem description…" />
        <Input value={cat} onChange={(e) => setCat(e.target.value)} placeholder="Category (optional)" />
        <Button variant="secondary" onClick={add} disabled={!desc.trim()} iconLeft={<Icon name="plus" size={16} />}>Add</Button>
      </div>
      {problems.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
          {problems.map((p) => (
            <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
              <Icon name="alert-triangle" size={15} style={{ color: 'var(--amber-500)' }} />
              <span style={{ flex: 1, fontSize: 13.5, color: 'var(--text-strong)' }}>{p.description}</span>
              {p.category && <Badge tone="neutral" size="sm">{p.category}</Badge>}
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button variant="primary" onClick={onNext} iconLeft={<Icon name="chevron-right" size={16} />}>Continue to review</Button>
      </div>
    </Card>
  );
}
