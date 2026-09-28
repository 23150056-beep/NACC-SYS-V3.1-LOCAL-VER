import { useRef, useState } from 'react';
import api from '../api/client';
import { useConfirm } from '../context/ConfirmContext';
import { useToast } from '../context/ToastContext';
import { Alert, Button, FormField, Icon, Input, Modal, Select, iconBtn } from '../ui';
import { FIELD_TYPES } from '../config/formFields';
import { firstError } from '../utils/errors';

/* "Upload a template" on the Clinical interview step (owner, 28 Sep 2026).
 *
 * A template used to be made on another screen by typing every question into
 * its own row; the agency's own interview form has about a hundred. The
 * server reads the uploaded Word or PDF file and proposes the sections and
 * questions (backend clinical/form_import.py), and saves nothing: this dialog
 * shows the draft, lets it be corrected, asks the same attestation the
 * Instruments page asks, and saves through the ordinary create. `onSaved`
 * hands the new template back so the step can select it straight away.
 */
export default function TemplateUpload({ formType = 'clinical_interview', onSaved }) {
  const toast = useToast();
  const confirm = useConfirm();
  const input = useRef(null);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState(null);
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);

  const read = async (file) => {
    if (!file) return;
    setError('');
    setReading(true);
    const fd = new FormData();
    fd.append('file', file);
    fd.append('form_type', formType);
    try {
      const { data } = await api.post('/form-templates/import/', fd);
      setSaveError('');
      setDraft({ ...data, attestation: false });
    } catch (err) {
      setError(firstError(err.response?.data, 'The file could not be read. Try again.'));
    } finally {
      setReading(false);
      if (input.current) input.current.value = '';
    }
  };

  const setField = (i, patch) => setDraft((d) => ({
    ...d, fields: d.fields.map((f, idx) => (idx === i ? { ...f, ...patch } : f)),
  }));
  const questions = draft ? draft.fields.filter((f) => f.field_type !== 'section' && f.label.trim()).length : 0;
  const sections = draft ? draft.fields.filter((f) => f.field_type === 'section' && f.label.trim()).length : 0;

  const save = async () => {
    setSaveError('');
    const title = draft.title.trim();
    if (!(await confirm({
      description: `This saves "${title}" as one of your Clinical Interview forms, ready to use with any child.`,
      confirmLabel: 'Yes, save the form',
      details: [['Questions', String(questions)], ['Sections', String(sections)], ['Read from', draft.source]],
    }))) return;
    setSaving(true);
    try {
      const { data } = await api.post('/form-templates/', {
        form_type: draft.form_type, title, body: draft.body || '', attestation: true,
        fields: draft.fields.filter((f) => f.label.trim()).map((f) => ({
          label: f.label.trim(), field_type: f.field_type,
          options: f.field_type === 'choice' ? (f.options || []) : [],
        })),
      });
      toast.success(`"${title}" saved and selected`);
      setDraft(null);
      onSaved?.(data);
    } catch (err) {
      setSaveError(firstError(err.response?.data, 'Could not save the form. Try again.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <input
        ref={input} type="file" accept=".docx,.pdf" style={{ display: 'none' }}
        aria-label="Interview form file" onChange={(e) => read(e.target.files?.[0])}
      />
      <Button type="button" variant="secondary" disabled={reading} onClick={() => input.current?.click()}
        iconLeft={<Icon name="upload" size={16} />}>
        {reading ? 'Reading the form…' : 'Upload a template'}
      </Button>
      {error && (
        <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />} style={{ marginTop: 8 }}>{error}</Alert>
      )}
      {draft && (
        <Modal
          open onClose={() => setDraft(null)} tone="brand" icon={<Icon name="file-text" size={19} />} width={680}
          title="Check the form before saving"
          subtitle={`Read from ${draft.source} · ${questions} question${questions === 1 ? '' : 's'} in ${sections} section${sections === 1 ? '' : 's'}`}
          footer={<>
            <Button variant="ghost" onClick={() => setDraft(null)}>Cancel</Button>
            <Button variant="primary" onClick={save} iconLeft={<Icon name="save" size={16} />}
              disabled={saving || !draft.attestation || !draft.title.trim() || questions === 0}>
              {saving ? 'Saving…' : 'Save template'}
            </Button>
          </>}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Alert tone="info" icon={<Icon name="info" size={18} />}>
              Nothing is saved yet. Each heading became a section and each line under it a question.
              Correct anything that came through wrong, or remove it.
            </Alert>
            {(draft.warnings || []).map((w) => (
              <Alert key={w} tone="warning" icon={<Icon name="alert-triangle" size={18} />}>{w}</Alert>
            ))}
            {saveError && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{saveError}</Alert>}
            <FormField label="Form title" required>
              <Input value={draft.title} maxLength={200} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            </FormField>
            <div className="racco-eyebrow" style={{ fontSize: 11 }}>Fields ({draft.fields.length})</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: '46vh', overflowY: 'auto', paddingRight: 4 }}>
              {draft.fields.map((f, i) => {
                const section = f.field_type === 'section';
                return (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: section ? '8px 8px 6px' : '0 8px', background: section ? 'var(--ink-50)' : 'transparent', borderRadius: 'var(--radius-md)', marginTop: section && i > 0 ? 6 : 0 }}>
                    <span className="racco-mono" style={{ width: 26, flex: 'none', fontSize: 11.5, color: 'var(--text-faint)', textAlign: 'right' }}>{i + 1}</span>
                    <div style={{ width: 172, flex: 'none' }}>
                      <Select size="sm" value={f.field_type} aria-label={`Type of field ${i + 1}`}
                        onChange={(e) => setField(i, { field_type: e.target.value })}>
                        {FIELD_TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
                      </Select>
                    </div>
                    <Input size="sm" value={f.label} aria-label={`Field ${i + 1}`}
                      style={section ? { fontWeight: 800 } : undefined}
                      onChange={(e) => setField(i, { label: e.target.value })} />
                    <button type="button" title="Remove" aria-label={`Remove field ${i + 1}`}
                      onClick={() => setDraft({ ...draft, fields: draft.fields.filter((_, idx) => idx !== i) })}
                      style={iconBtn('var(--red-500)', 28)}>
                      <Icon name="trash-2" size={14} />
                    </button>
                  </div>
                );
              })}
            </div>
            <div style={{ padding: '12px 14px', borderRadius: 'var(--radius-lg)', background: 'var(--warning-50)', border: '1px solid var(--warning-100)' }}>
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 12.5, color: 'var(--text-strong)', cursor: 'pointer' }}>
                <input type="checkbox" checked={draft.attestation} onChange={(e) => setDraft({ ...draft, attestation: e.target.checked })} style={{ marginTop: 2, accentColor: 'var(--blue-600)' }} />
                <span><strong>Attestation (required):</strong> this form is agency-authored or an official government form, <strong>not</strong> a published assessment instrument.</span>
              </label>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
