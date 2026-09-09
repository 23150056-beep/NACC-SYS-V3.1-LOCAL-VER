import { useEffect, useState } from 'react';
import api from '../api/client';
import { useActivity } from '../context/ActivityContext';
import { useAuth } from '../context/AuthContext';
import {
  Alert, Badge, Button, EmptyState, FormField, hoverLift, Icon, iconBtn, Input, PAGE, PageHeader,
  Segmented, Select, TD, TH, THEAD_ROW, TR,
} from '../ui';
import { useToast } from '../context/ToastContext';
import { printBlankForm } from '../utils/printForm';
import InstrumentFormDrawer, { CATEGORIES, EMPTY_INSTRUMENT } from '../components/InstrumentFormDrawer';

const FORM_TYPES = [
  { v: 'consent', label: 'Consent Form' },
  { v: 'clinical_interview', label: 'Clinical Interview Form' },
  { v: 'problem_checklist', label: 'Problem Checklist' },
  { v: 'self_report_gov', label: 'Self-Report (Government Form)' },
];
const FIELD_TYPES = [
  { v: 'section', label: 'Section heading' },
  { v: 'text', label: 'Short text' },
  { v: 'long_text', label: 'Long text' },
  { v: 'date', label: 'Date' },
  { v: 'yes_no', label: 'Yes / No' },
  { v: 'choice', label: 'Choice list' },
];

const blankField = () => ({ label: '', field_type: 'text', options: [] });
const EMPTY_TEMPLATE = { form_type: 'consent', title: '', body: '', fields: [blankField()], attestation: false };

export default function Instruments() {
  const { refresh: refreshActivity } = useActivity();
  const { user } = useAuth();
  const isAdmin = user?.role_name === 'Administrator';
  // Catalog management now lives inside the Pre-Assessment wizard (step 4);
  // psychologists only manage their agency form templates from this page.
  const showCatalog = isAdmin;
  const toast = useToast();
  const [tab, setTab] = useState(isAdmin ? 'catalog' : 'forms');
  const [instruments, setInstruments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [psychologists, setPsychologists] = useState([]);
  const [form, setForm] = useState(null); // instrument drawer
  const [tpl, setTpl] = useState(null); // template drawer
  const [error, setError] = useState('');

  const load = () => {
    api.get('/instruments/').then((r) => setInstruments(r.data)).catch(() => {});
    api.get('/form-templates/').then((r) => setTemplates(r.data)).catch(() => {});
  };
  useEffect(() => {
    load();
    if (isAdmin) api.get('/users/').then((r) => setPsychologists(r.data.filter((u) => u.role_name === 'Psychologist'))).catch(() => {});
  }, [isAdmin]);

  const saveInstrument = async () => {
    setError('');
    if (!form.title.trim()) { setError('Title is required.'); return; }
    const payload = { ...form };
    // Shared (owner=null) instruments are legal now — an empty selection means
    // "shared", not "invalid", so send an explicit null rather than ''.
    if (isAdmin) payload.owner = payload.owner || null;
    else delete payload.owner;
    delete payload.owner_name; delete payload.updated_at;
    try {
      if (form.id) await api.put(`/instruments/${form.id}/`, payload);
      else await api.post('/instruments/', payload);
      toast.success(form.id ? 'Instrument updated' : 'Instrument added to catalog');
      setForm(null); load(); refreshActivity();
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Save failed'));
    }
  };

  const deactivateInstrument = async (i) => {
    if (!window.confirm(`Deactivate “${i.title}”?`)) return;
    try { await api.post(`/instruments/${i.id}/deactivate/`); toast.success(`“${i.title}” deactivated`); load(); refreshActivity(); }
    catch { toast.error('Could not deactivate.'); }
  };

  const setTplField = (i, patch) => setTpl((t) => ({ ...t, fields: t.fields.map((f, idx) => (idx === i ? { ...f, ...patch } : f)) }));
  const saveTemplate = async () => {
    setError('');
    if (!tpl.title.trim()) { setError('Title is required.'); return; }
    if (!tpl.attestation) { setError('You must tick the attestation checkbox to save an agency form.'); return; }
    const payload = {
      form_type: tpl.form_type, title: tpl.title, body: tpl.body || '', attestation: true,
      fields: tpl.fields.filter((f) => f.label.trim()).map((f) => ({
        label: f.label, field_type: f.field_type,
        options: f.field_type === 'choice' ? (f.options || []) : [],
      })),
    };
    try {
      if (tpl.id) await api.patch(`/form-templates/${tpl.id}/`, payload);
      else await api.post('/form-templates/', payload);
      toast.success(tpl.id ? 'Form template updated' : 'Form template created');
      setTpl(null); load(); refreshActivity();
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Save failed'));
    }
  };

  const deactivateTemplate = async (t) => {
    if (!window.confirm(`Deactivate “${t.title}”?`)) return;
    try { await api.post(`/form-templates/${t.id}/deactivate/`); toast.success(`“${t.title}” deactivated`); load(); refreshActivity(); }
    catch { toast.error('Could not deactivate.'); }
  };

  return (
    <div style={{ ...PAGE, position: 'relative' }}>
      <PageHeader
        title={showCatalog ? 'Instruments & Agency Forms' : 'Pre-Assessment Instruments'}
        subtitle="RACCO I · titles and metadata only"
      >
        {showCatalog && (
          <Segmented
            label="Instrument sections"
            value={tab} onChange={setTab}
            options={[{ value: 'catalog', label: 'Instrument catalog' }, { value: 'forms', label: 'Agency forms' }]}
          />
        )}
        {tab === 'catalog'
          ? <Button variant="primary" onClick={() => { setError(''); setForm({ ...EMPTY_INSTRUMENT }); }} iconLeft={<Icon name="plus" size={18} />}>Add instrument title</Button>
          : <Button variant="primary" onClick={() => { setError(''); setTpl({ ...EMPTY_TEMPLATE, fields: [blankField()] }); }} iconLeft={<Icon name="plus" size={18} />}>New agency form</Button>}
      </PageHeader>

      {/* The copyright rule, at the top of the screen it constrains. It is the
          reason this catalog holds titles and nothing else, and it belongs
          where someone is about to add a row — not in a help page. */}
      <div style={{ display: 'flex', gap: 11, padding: '12px 14px', background: 'var(--blue-50)', border: '1px solid var(--blue-100)', borderRadius: 11 }}>
        <Icon name={showCatalog ? 'shield-check' : 'file-text'} size={19} style={{ color: 'var(--blue-600)', flex: 'none', marginTop: 1 }} />
        <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-body)' }}>
          {showCatalog
            ? <>The catalog stores instrument <strong>titles and metadata only</strong> — never questions, scales, or scoring keys. Published instruments are administered on paper using the psychologist&apos;s own materials.</>
            : <>Manage your consent and interview form templates. Instrument titles are managed inside the Pre-Assessment wizard, step 4.</>}
        </p>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        {tab === 'catalog' ? (
          instruments.length === 0 ? (
            <EmptyState icon={<Icon name="clipboard-pen" size={24} />} title="No instruments in the catalog" description="Add the titles of the instruments you administer on paper." />
          ) : (
            <div className="racco-scroll" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={THEAD_ROW}>
                  {['Instrument title', 'Publisher', 'Category', 'Age range', ...(isAdmin ? ['Owner'] : []), ''].map((h, i) => <th key={h || i} scope="col" style={TH}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {instruments.map((i) => (
                    <tr key={i.id} style={TR}>
                      <td style={{ ...TD, fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{i.title}</td>
                      <td style={TD}>{i.publisher || '—'}</td>
                      <td style={TD}><Badge tone="neutral" size="sm">{CATEGORIES.find((c) => c.v === i.category)?.label || i.category}</Badge></td>
                      <td style={TD}>{i.age_range || '—'}</td>
                      {isAdmin && <td style={TD}>{i.owner_name || '—'}</td>}
                      <td style={{ ...TD, textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: 6 }}>
                          <button title={`Edit ${i.title}`} aria-label={`Edit ${i.title}`} onClick={() => { setError(''); setForm({ ...i, owner: i.owner || '' }); }} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--blue-600)')}><Icon name="pencil" size={15} /></button>
                          <button title={`Deactivate ${i.title}`} aria-label={`Deactivate ${i.title}`} onClick={() => deactivateInstrument(i)} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--red-700)')}><Icon name="archive" size={15} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          templates.length === 0 ? (
            <EmptyState icon={<Icon name="file-text" size={24} />} title="No agency forms yet" description="Create your consent form or clinical interview form template." />
          ) : (
            <div className="racco-scroll" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={THEAD_ROW}>
                  {['Form title', 'Type', 'Fields', 'Version', ...(isAdmin ? ['Owner'] : []), ''].map((h, i) => <th key={h || i} scope="col" style={TH}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {templates.map((t) => (
                    <tr key={t.id} style={TR}>
                      <td style={{ ...TD, fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{t.title}</td>
                      <td style={TD}><Badge tone="brand" size="sm">{FORM_TYPES.find((f) => f.v === t.form_type)?.label || t.form_type}</Badge></td>
                      <td className="racco-mono" style={TD}>{t.fields?.length ?? 0}</td>
                      <td className="racco-mono" style={TD}>v{t.version}</td>
                      {isAdmin && <td style={TD}>{t.owner_name || '—'}</td>}
                      <td style={{ ...TD, textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: 6 }}>
                          <button title="Print blank form (e.g. for guardians to sign)" aria-label={`Print ${t.title}`} onClick={() => printBlankForm(t)} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--text-muted)')}><Icon name="printer" size={15} /></button>
                          {(isAdmin || t.owner !== null) && (
                            <>
                              <button title={`Edit ${t.title}`} aria-label={`Edit ${t.title}`} onClick={() => { setError(''); setTpl({ ...t, fields: t.fields?.length ? t.fields : [blankField()], attestation: false }); }} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--blue-600)')}><Icon name="pencil" size={15} /></button>
                              <button title={`Deactivate ${t.title}`} aria-label={`Deactivate ${t.title}`} onClick={() => deactivateTemplate(t)} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--red-700)')}><Icon name="archive" size={15} /></button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>
      {form && (
        <InstrumentFormDrawer
          form={form} setForm={setForm}
          psychologists={psychologists} isAdmin={isAdmin}
          error={error} onSave={saveInstrument} onClose={() => setForm(null)}
        />
      )}

      {tpl && (
        <div onClick={() => setTpl(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 560, maxWidth: '94%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column', animation: 'racco-slide-left var(--dur-slow) var(--ease-out)' }}>
            <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--ink-50)' }}>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{tpl.id ? `Edit Agency Form (v${tpl.version})` : 'New Agency Form'}</div>
              <button type="button" onClick={() => setTpl(null)} aria-label="Close" {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--text-muted)')}><Icon name="x" size={17} /></button>
            </div>
            <div className="racco-scroll" style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
              <FormField label="Form Type" required>
                <Select value={tpl.form_type} onChange={(e) => setTpl({ ...tpl, form_type: e.target.value })}>
                  {FORM_TYPES.map((f) => <option key={f.v} value={f.v}>{f.label}</option>)}
                </Select>
              </FormField>
              <FormField label="Title" required><Input value={tpl.title} onChange={(e) => setTpl({ ...tpl, title: e.target.value })} /></FormField>
              <FormField label="Document text" hint="Optional. Shown before the fields on screen and in print. Lines starting with '## ' become section headings.">
                <textarea value={tpl.body || ''} onChange={(e) => setTpl({ ...tpl, body: e.target.value })} rows={8}
                  style={{ width: '100%', resize: 'vertical', padding: '11px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 13.5, lineHeight: 1.55 }} />
              </FormField>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
                <div className="racco-eyebrow" style={{ fontSize: 11 }}>Fields ({tpl.fields.length})</div>
                <Button variant="ghost" onClick={() => setTpl({ ...tpl, fields: [...tpl.fields, blankField()] })} iconLeft={<Icon name="plus" size={15} />}>Add</Button>
              </div>
              {tpl.fields.map((f, i) => (
                <div key={i} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 12, background: 'var(--ink-50)', display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', paddingTop: 9 }}>{i + 1}</span>
                  <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <Input value={f.label} onChange={(e) => setTplField(i, { label: e.target.value })} placeholder="Field label" />
                    <Select value={f.field_type} onChange={(e) => setTplField(i, { field_type: e.target.value })}>
                      {FIELD_TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
                    </Select>
                    {f.field_type === 'choice' && (
                      <Input value={(f.options || []).join(', ')} onChange={(e) => setTplField(i, { options: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} placeholder="Options, comma-separated" />
                    )}
                  </div>
                  <button title="Remove" onClick={() => setTpl({ ...tpl, fields: tpl.fields.filter((_, idx) => idx !== i) })} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--red-500)', 28)}><Icon name="trash-2" size={14} /></button>
                </div>
              ))}

              <div style={{ padding: '12px 14px', borderRadius: 'var(--radius-lg)', background: 'var(--warning-50)', border: '1px solid var(--warning-100)' }}>
                <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text-strong)', cursor: 'pointer' }}>
                  <input type="checkbox" checked={tpl.attestation} onChange={(e) => setTpl({ ...tpl, attestation: e.target.checked })} style={{ marginTop: 2, accentColor: 'var(--blue-600)' }} />
                  <span><strong>Attestation (required):</strong> this form is agency-authored or an official government form, <strong>not</strong> a published assessment instrument.</span>
                </label>
              </div>
            </div>
            <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
              <Button variant="primary" fullWidth onClick={saveTemplate} disabled={!tpl.attestation} iconLeft={<Icon name="save" size={16} />}>Save Form Template</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
