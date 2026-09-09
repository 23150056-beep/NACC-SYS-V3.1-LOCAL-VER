import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { caseRef } from '../utils/child';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import {
  Alert, Avatar, Badge, Button, EmptyState, FileUpload, FormField, Icon, iconBtn, IconChip, Input, Note,
  PAGE, PageHeader, Select, Tabs,
} from '../ui';
import { useOpenFromLink } from '../utils/links';


const REPORT_TYPES = [
  { v: 'initial', label: 'Initial Evaluation' },
  { v: 'progress', label: 'Progress Report' },
  { v: 'final', label: 'Final Report' },
  { v: 'other', label: 'Other' },
];

export default function Report() {
  const { user } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const role = user?.role_name || 'Staff';
  const isPsych = role === 'Psychologist';
  const isStaffOrAdmin = ['Administrator', 'Staff'].includes(role);
  const [tab, setTab] = useState('results');
  const [entries, setEntries] = useState([]);
  const [files, setFiles] = useState([]);
  const [caseReferrals, setCaseReferrals] = useState([]);
  const [children, setChildren] = useState([]);
  const [q, setQ] = useState('');
  const [upload, setUpload] = useState(null); // upload drawer state (report or case referral)
  const [error, setError] = useState('');
  const [openChild, setOpenChild] = useState(null);

  const openReportUpload = () => { setError(''); setUpload({ kind: 'report', child: '', report_type: 'progress', coverage: '', fileObj: null }); };
  const openReferralUpload = () => { setError(''); setUpload({ kind: 'case_referral', child: '', coverage: '', fileObj: null }); };
  useOpenFromLink('upload', '1', isPsych ? openReportUpload : openReferralUpload, !!upload);

  const load = () => {
    api.get('/result-entries/').then((r) => setEntries(r.data)).catch(() => {});
    api.get('/report-files/').then((r) => setFiles(r.data)).catch(() => {});
    api.get('/case-referrals/').then((r) => setCaseReferrals(r.data)).catch(() => {});
    api.get('/children/').then((r) => setChildren(r.data.filter((c) => c.status === 'active'))).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const visibleEntries = useMemo(() => entries
    .filter((e) => (e.child_name || '').toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (b.date || '').localeCompare(a.date || '')), [entries, q]);
  const visibleFiles = useMemo(() => files
    .filter((f) => (f.child_name || '').toLowerCase().includes(q.toLowerCase())), [files, q]);
  const visibleCaseReferrals = useMemo(() => caseReferrals
    .filter((f) => (f.child_name || '').toLowerCase().includes(q.toLowerCase())), [caseReferrals, q]);

  const grouped = useMemo(() => {
    const map = new Map();
    for (const e of visibleEntries) {
      if (!map.has(e.child)) map.set(e.child, { child: e.child, child_name: e.child_name, entries: [] });
      map.get(e.child).entries.push(e);
    }
    return [...map.values()].sort((a, b) => (a.child_name || '').localeCompare(b.child_name || ''));
  }, [visibleEntries]);

  const download = async (f) => {
    try {
      const res = await api.get(`/report-files/${f.id}/download/`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a'); a.href = url; a.download = f.original_filename || 'report'; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error('Could not download the file.'); }
  };

  const exportCsv = () => {
    const rows = [['Child', 'Case', 'Instrument', 'Classification', 'Date', 'Entered by'],
      ...visibleEntries.map((e) => [e.child_name, caseRef(e.child), e.instrument_title || '', e.classification || '', e.date, e.entered_by_name || ''])];
    const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const a = document.createElement('a'); a.href = url; a.download = 'result-entries.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  const doUpload = async (e) => {
    e.preventDefault();
    setError('');
    if (!upload.child || !upload.fileObj) { setError('Choose a child and a file.'); return; }
    const fd = new FormData();
    fd.append('child', upload.child);
    fd.append('file', upload.fileObj);
    if (upload.kind === 'case_referral') {
      fd.append('description', upload.coverage || '');
    } else {
      fd.append('report_type', upload.report_type);
      fd.append('coverage', upload.coverage || '');
    }
    try {
      await api.post(upload.kind === 'case_referral' ? '/case-referrals/' : '/report-files/',
        fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success(upload.kind === 'case_referral' ? 'Case referral uploaded' : 'Report uploaded');
      setUpload(null); load();
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Upload failed'));
    }
  };

  const downloadCaseReferral = async (f) => {
    try {
      const res = await api.get(`/case-referrals/${f.id}/download/`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a'); a.href = url; a.download = f.original_filename || 'case-referral'; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error('Could not download the file.'); }
  };


  return (
    <div style={{ ...PAGE, position: 'relative' }} className="racco-print-area">
      <PageHeader
        title="Results &amp; Reports"
        subtitle="Manually entered results, uploaded psychological reports and case referrals"
      >
        {tab === 'results' && <Button variant="secondary" onClick={exportCsv} iconLeft={<Icon name="download" size={17} />}>Export CSV</Button>}
        <Button variant="secondary" onClick={() => window.print()} iconLeft={<Icon name="printer" size={17} />}>Print</Button>
        {isPsych && <Button variant="primary" onClick={openReportUpload} iconLeft={<Icon name="upload" size={18} />}>Upload report</Button>}
        {isStaffOrAdmin && <Button variant="primary" onClick={openReferralUpload} iconLeft={<Icon name="folder-heart" size={18} />}>Upload case referral</Button>}
      </PageHeader>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        {/* Tabs and the filter share one strip: the filter applies to whichever
            tab is open, and putting it anywhere else implied otherwise. */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', borderBottom: '1px solid var(--divider)' }} className="racco-no-print">
          <Tabs
            style={{ borderBottom: 'none', flex: 1, minWidth: 0 }}
            active={tab} onChange={setTab}
            tabs={[
              { id: 'results', label: 'Results', count: entries.length || undefined },
              { id: 'files', label: 'Reports', count: files.length || undefined },
              { id: 'case-referrals', label: 'Referrals', count: caseReferrals.length || undefined },
            ]}
          />
          <div style={{ padding: '8px 14px' }}>
            <Input
              size="sm" style={{ width: 210 }} fullWidth={false}
              placeholder="Filter by child…" value={q} onChange={(e) => setQ(e.target.value)}
              leading={<Icon name="search" size={16} />} aria-label="Filter by child"
            />
          </div>
        </div>

        {tab === 'results' ? (
          visibleEntries.length === 0 ? (
            <EmptyState icon={<Icon name="folder-search" size={24} />} title="No result entries yet" description="Psychologists record findings from the per-child report page." />
          ) : (
            <>
              {grouped.map((g) => {
                const open = openChild === g.child;
                const latest = g.entries[0]; // visibleEntries is already newest-first
                return (
                  <div key={g.child} style={{ borderBottom: '1px solid var(--divider)' }}>
                    <button
                      type="button" aria-expanded={open}
                      onClick={() => setOpenChild(open ? null : g.child)}
                      style={{ width: '100%', padding: '10px 15px', background: 'var(--ink-25)', display: 'flex', alignItems: 'center', gap: 10, border: 'none', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
                    >
                      <Avatar name={g.child_name} size={28} />
                      <span style={{ fontWeight: 800, fontSize: 13.5, color: 'var(--text-strong)' }}>{g.child_name}</span>
                      <span className="racco-mono" style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-faint)' }}>{caseRef(g.child)}</span>
                      <span style={{ flex: 1 }} />
                      <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>
                        {g.entries.length} {g.entries.length === 1 ? 'entry' : 'entries'}
                        {latest?.date ? ` · latest ${latest.date}` : ''}
                      </span>
                      <Icon name={open ? 'chevron-up' : 'chevron-down'} size={18} style={{ color: 'var(--text-faint)' }} />
                    </button>
                    {open && g.entries.map((e) => (
                      <button
                        key={e.id} type="button" onClick={() => navigate(`/report/child/${e.child}`)}
                        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 14, padding: '10px 15px 10px 53px', borderTop: '1px solid var(--divider-row)', border: 'none', borderTopWidth: 1, borderTopStyle: 'solid', borderTopColor: 'var(--divider-row)', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
                        onMouseEnter={(ev) => { ev.currentTarget.style.background = 'var(--blue-50)'; }}
                        onMouseLeave={(ev) => { ev.currentTarget.style.background = 'transparent'; }}
                      >
                        <span style={{ flex: 1, minWidth: 0 }}>
                          <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)' }}>{e.instrument_title || 'No instrument named'}</span>
                          <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-muted)' }}>entered by {e.entered_by_name || '—'}</span>
                        </span>
                        {e.classification && <Badge tone="brand" size="sm">{e.classification}</Badge>}
                        <span className="racco-mono" style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-body)', width: 86, textAlign: 'right', flex: 'none' }}>{e.date}</span>
                      </button>
                    ))}
                  </div>
                );
              })}
              <Note icon="info">
                Classifications are typed in by the psychologist from their own paper scoring. The system
                computes no scores and stores no scoring keys.
              </Note>
            </>
          )
        ) : tab === 'files' ? (
          visibleFiles.length === 0 ? (
            <EmptyState icon={<Icon name="file-text" size={24} />} title="No reports uploaded yet" description="Psychologists upload their reports in their own format." />
          ) : visibleFiles.map((f) => (
            <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '11px 15px', borderBottom: '1px solid var(--divider-row)', flexWrap: 'wrap' }}>
              <IconChip icon="file-text" tone="brand" size={34} />
              <span style={{ flex: 1, minWidth: 160 }}>
                <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.original_filename}</span>
                <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-muted)' }}>{f.child_name} · {caseRef(f.child)} · {f.author_name || 'unknown author'}</span>
              </span>
              <Badge tone="brand" size="sm">{REPORT_TYPES.find((t) => t.v === f.report_type)?.label || f.report_type}</Badge>
              <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)', width: 120, flex: 'none' }}>{f.coverage || '—'}</span>
              <span className="racco-mono" style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-body)', width: 86, textAlign: 'right', flex: 'none' }}>{(f.created_at || '').slice(0, 10)}</span>
              <button
                type="button" onClick={() => download(f)} title={`Download ${f.original_filename}`} aria-label={`Download ${f.original_filename}`}
                style={iconBtn('var(--text-body)')} className="racco-no-print"
              >
                <Icon name="download" size={16} />
              </button>
            </div>
          ))
        ) : (
          visibleCaseReferrals.length === 0 ? (
            <EmptyState icon={<Icon name="folder-heart" size={24} />} title="No case referrals yet" description="Social workers upload the official case referral at intake." />
          ) : (
            <>
              {visibleCaseReferrals.map((f) => (
                <div key={f.id} style={{ padding: '11px 15px', borderBottom: '1px solid var(--divider-row)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                    <Avatar name={f.child_name} size={32} />
                    <span style={{ width: 172, flex: 'none', minWidth: 0 }}>
                      <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.child_name}</span>
                      <span className="racco-mono" style={{ display: 'block', fontSize: 11.5, color: 'var(--text-faint)' }}>{caseRef(f.child)}</span>
                    </span>
                    <span style={{ flex: 1, minWidth: 160 }}>
                      <span style={{ display: 'block', fontWeight: 700, fontSize: 12.5, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.original_filename}</span>
                      <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.description || 'No description given'}</span>
                    </span>
                    <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)', flex: 'none' }}>{f.uploaded_by_name || '—'}</span>
                    <span className="racco-mono" style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-body)', width: 82, textAlign: 'right', flex: 'none' }}>{(f.created_at || '').slice(0, 10)}</span>
                    <button
                      type="button" onClick={() => downloadCaseReferral(f)} title={`Download ${f.original_filename}`} aria-label={`Download ${f.original_filename}`}
                      style={iconBtn('var(--text-body)')} className="racco-no-print"
                    >
                      <Icon name="download" size={16} />
                    </button>
                  </div>
                  {f.ai_summary && (
                    <div style={{ marginTop: 8, marginLeft: 46, padding: '8px 10px', borderRadius: 'var(--radius-sm)', background: 'var(--blue-50)', border: '1px solid var(--blue-100)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <Icon name="sparkles" size={12} style={{ color: 'var(--blue-600)' }} />
                        <span style={{ fontSize: 'var(--text-3xs)', fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--blue-700)' }}>
                          AI summary {f.ai_summary_confirmed ? '· confirmed' : '· draft (unconfirmed)'}
                        </span>
                      </div>
                      <p style={{ fontSize: 12.5, color: 'var(--text-body)', margin: 0, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{f.ai_summary}</p>
                    </div>
                  )}
                </div>
              ))}
              <Note icon="send">
                A referral records that a case left this office for someone else&rsquo;s desk. The child&rsquo;s
                record stays active here until the receiving office confirms, so nobody falls between two agencies.
              </Note>
            </>
          )
        )}
      </div>
      {upload && (
        <div onClick={() => setUpload(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
          <form onSubmit={doUpload} onClick={(e) => e.stopPropagation()} style={{ width: 440, maxWidth: '92%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column', animation: 'racco-slide-left var(--dur-slow) var(--ease-out)' }}>
            <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', background: 'var(--ink-50)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>
              {upload.kind === 'case_referral' ? 'Upload Case Referral' : 'Upload Psychological Report'}
            </div>
            <div className="racco-scroll" style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
              <FormField label="Child" required>
                <Select value={upload.child} onChange={(e) => setUpload({ ...upload, child: e.target.value })}>
                  <option value="">— Select child —</option>
                  {children.map((c) => <option key={c.id} value={c.id}>{c.fullname}</option>)}
                </Select>
              </FormField>
              <FormField label={upload.kind === 'case_referral' ? 'Case referral file (PDF / Word)' : 'Report file (PDF / Word)'} required hint={upload.kind === 'case_referral' ? "The child's official case referral document." : 'Your own report, in your own format.'}>
                <FileUpload file={upload.fileObj} accept=".pdf,.doc,.docx"
                  onChange={(f) => setUpload({ ...upload, fileObj: f })} />
              </FormField>
              {upload.kind !== 'case_referral' && (
                <FormField label="Report type">
                  <Select value={upload.report_type} onChange={(e) => setUpload({ ...upload, report_type: e.target.value })}>
                    {REPORT_TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}
                  </Select>
                </FormField>
              )}
              <FormField label={upload.kind === 'case_referral' ? 'Description' : 'Session / date coverage'}>
                <Input value={upload.coverage} onChange={(e) => setUpload({ ...upload, coverage: e.target.value })} placeholder={upload.kind === 'case_referral' ? 'e.g. Intake case referral' : 'e.g. Sessions 1-3, Jan-Mar 2026'} />
              </FormField>
            </div>
            <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
              <Button type="submit" variant="primary" fullWidth iconLeft={<Icon name="file-up" size={16} />}>Upload</Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
