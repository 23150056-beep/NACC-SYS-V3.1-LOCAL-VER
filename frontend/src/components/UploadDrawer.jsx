import { useState } from 'react';
import api from '../api/client';
import { useToast } from '../context/ToastContext';
import { REPORT_TYPES } from '../config/caseData';
import { Alert, Button, Drawer, FileUpload, FormField, Icon, Input, Select } from '../ui';

/**
 * Filing a psychological report or a case referral. One form, used by the
 * Results & Reports screen and by a child's own record, so the check before
 * filing cannot exist in one place and not the other.
 *
 * `child` fixes the child (the record page already knows whose it is);
 * otherwise `childOptions` fills a picker, starting on `initialChild` if given.
 */
export default function UploadDrawer({ kind, child = null, childOptions = [], initialChild = '', onClose, onUploaded }) {
  const toast = useToast();
  const referral = kind === 'case_referral';
  const [form, setForm] = useState({
    child: child ? String(child.id) : String(initialChild || ''),
    report_type: 'progress', coverage: '', fileObj: null, check: null,
  });
  const [busy, setBusy] = useState(null); // 'checking' | 'uploading'
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (!form.child || !form.fileObj) { setError('Choose a child and a file.'); return; }
    // A report is checked before it is filed, while it can still be swapped
    // for the right file: another child's name or case number left in from
    // the report it was started from. Nothing found and readable means it is
    // filed straight away; otherwise the findings are shown and the next
    // press files it as it is.
    if (!referral && !form.check) {
      const probe = new FormData();
      probe.append('child', form.child);
      probe.append('file', form.fileObj);
      setBusy('checking');
      try {
        const { data } = await api.post('/report-files/check/', probe,
          { headers: { 'Content-Type': 'multipart/form-data' } });
        if (data.findings.length || !data.readable) {
          setForm((f) => ({ ...f, check: data }));
          setBusy(null);
          return;
        }
      } catch (err) {
        setBusy(null);
        setError(JSON.stringify(err.response?.data || 'Could not check the file.'));
        return;
      }
    }
    const fd = new FormData();
    fd.append('child', form.child);
    fd.append('file', form.fileObj);
    if (referral) {
      fd.append('description', form.coverage || '');
    } else {
      fd.append('report_type', form.report_type);
      fd.append('coverage', form.coverage || '');
    }
    setBusy('uploading');
    try {
      await api.post(referral ? '/case-referrals/' : '/report-files/',
        fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success(referral ? 'Case referral uploaded' : 'Report uploaded');
      onUploaded?.();
      onClose();
    } catch (err) {
      setBusy(null);
      setError(JSON.stringify(err.response?.data || 'Upload failed'));
    }
  };

  return (
    <Drawer
      as="form" onSubmit={submit} noValidate
      title={referral ? 'Upload Case Referral' : 'Upload Psychological Report'}
      subtitle={child ? child.fullname : null}
      // A request in flight is not abandoned by a stray click on the backdrop.
      dismissible={!busy}
      onClose={onClose}
      footer={
        <Button type="submit" variant="primary" fullWidth disabled={!!busy}
                iconLeft={<Icon name="file-up" size={16} />}>
          {busy === 'checking' ? 'Checking…' : busy === 'uploading' ? 'Uploading…'
            : form.check ? 'Upload anyway' : 'Upload'}
        </Button>
      }
    >
      {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
      {!child && (
        <FormField label="Child" required>
          <Select value={form.child} onChange={(e) => setForm({ ...form, child: e.target.value, check: null })}>
            <option value="">— Select child —</option>
            {childOptions.map((c) => <option key={c.id} value={c.id}>{c.fullname}</option>)}
          </Select>
        </FormField>
      )}
      <FormField label={referral ? 'Case referral file (PDF / Word)' : 'Report file (PDF / Word)'} required
                 hint={referral ? "The child's official case referral document." : 'Your own report, in your own format.'}>
        <FileUpload file={form.fileObj} accept=".pdf,.doc,.docx"
                    onChange={(f) => setForm({ ...form, fileObj: f, check: null })} />
      </FormField>
      {form.check?.findings?.length > 0 && (
        <Alert tone="warning" title="Check before filing." icon={<Icon name="alert-triangle" size={18} />}>
          <ul style={{ margin: '4px 0 6px', paddingLeft: 18 }}>
            {form.check.findings.map((f) => <li key={f.message} style={{ marginBottom: 2 }}>{f.message}</li>)}
          </ul>
          Choose a different file, or upload this one as it is.
        </Alert>
      )}
      {form.check && !form.check.readable && (
        <Alert tone="info" title="This file could not be read.">
          It can still be uploaded, but without its text it cannot be checked or summarised.
          Saved as .docx, or as a PDF with real text rather than a scan, it can be.
        </Alert>
      )}
      {!referral && (
        <FormField label="Report type">
          <Select value={form.report_type} onChange={(e) => setForm({ ...form, report_type: e.target.value })}>
            {REPORT_TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
          </Select>
        </FormField>
      )}
      <FormField label={referral ? 'Description' : 'Session / date coverage'}>
        <Input value={form.coverage} onChange={(e) => setForm({ ...form, coverage: e.target.value })}
               placeholder={referral ? 'e.g. Intake case referral' : 'e.g. Sessions 1-3, Jan-Mar 2026'} />
      </FormField>
    </Drawer>
  );
}
