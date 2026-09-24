import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useActivity } from '../context/ActivityContext';
import {
  Alert, Avatar, Badge, Button, ConfirmDialog, EmptyState, FilterPills, hoverLift, Icon, iconBtn,
  Input, PAGE, PageHeader, Segmented, Select, TD, TH, THEAD_ROW, TOOLBAR, TR,
} from '../ui';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import { useLayout } from '../context/LayoutContext';
import { TERMINATION_REASONS } from '../config/caseData';
import { loadAll } from '../utils/load';
import ChildForm, { EMPTY } from './children/ChildForm';
import ChildDrawer, { TerminateModal } from './children/ChildDrawer';
import { fmtDay, fmtTime, localDate } from './children/shared';
import { ageFrom, ageGroup, caseRef } from '../utils/child';

// Live "who else has this record open" chip — polls the presence heartbeat endpoint.
function usePresence(childId) {
  const [others, setOthers] = useState([]);
  useEffect(() => {
    if (!childId) { setOthers([]); return; }
    let alive = true;
    // Silent on purpose, and emphatically so: this repeats every ten
    // seconds for as long as the drawer is open. A message per failed beat
    // would bury the screen during any blip, and nobody is waiting on it -
    // the worst case is not being told a colleague is also looking.
    const beat = () => api.post(`/children/${childId}/presence/`)
      .then((r) => alive && setOthers(r.data.others || [])).catch(() => {});
    beat();
    const t = setInterval(beat, 10000);
    return () => { alive = false; clearInterval(t); };
  }, [childId]);
  return others;
}

/* The pre-assessment pipeline, as a dot. The column is one of eight and can
 * only afford a word, so the colour carries the urgency and the word carries
 * the state. Mirrors PA_STATUS_TONES, which drives the badge form elsewhere. */
const PA_DOT = {
  'No Consent Yet': 'var(--red-500)',
  'Not Yet Pre-Assessed': 'var(--ink-300)',
  'In Progress': 'var(--amber-500)',
  Answered: 'var(--blue-500)',
  Completed: 'var(--success-500)',
};

function csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// V2 status chip: `Active · Foster Care` / `Archived (Terminated)`.
function ScheduleChip({ appts = [] }) {
  if (appts.length === 0) return <span style={{ color: 'var(--text-faint)' }}>—</span>;
  const today = localDate(new Date());
  const next = appts[0]; // pre-sorted by start
  const isToday = next.start.slice(0, 10) === today;
  return (
    <Badge tone={isToday ? 'amber' : 'neutral'} size="sm" dot>
      {isToday ? `Today · ${fmtTime(next.start)}` : `${fmtDay(next.start)}`}
    </Badge>
  );
}

export default function Children() {
  const { user } = useAuth();
  const { refresh: refreshActivity } = useActivity();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const layout = useLayout();
  const canManage = ['Administrator', 'Staff'].includes(user?.role_name);
  const isAdmin = user?.role_name === 'Administrator';
  const isPsych = user?.role_name === 'Psychologist';
  const [children, setChildren] = useState([]);
  const [psychologists, setPsychologists] = useState([]);
  const [blocks, setBlocks] = useState([]);
  // childId -> [scheduled appointments in the next 7 days], sorted by start.
  const [apptsByChild, setApptsByChild] = useState({});
  const [searchParams, setSearchParams] = useSearchParams();
  const q = searchParams.get('q') || '';
  const [status, setStatus] = useState('active');
  const [sortMode, setSortMode] = useState('newest');
  const [reasonFilter, setReasonFilter] = useState(''); // Archived tab only (admin/staff)
  const [sel, setSel] = useState(null); // detail drawer record
  const [form, setForm] = useState(null); // add/edit drawer
  const [terminating, setTerminating] = useState(null); // terminate modal record
  // The child awaiting a reopen confirmation. This was a native browser
  // confirm, and the same 180-character string was written out at both call
  // sites - the row button and the drawer - which is two copies of one
  // sentence, each free to drift away from the other.
  const [reopening, setReopening] = useState(null);
  const [reopenBusy, setReopenBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState(null);
  const others = usePresence(form?.id || sel?.id);
  // The old standalone Archive page folded in here: admin/staff viewing the
  // Archived filter get the termination-detail columns + reopen; psychologists
  // keep the plain roster (they can't reopen, see decision 2026-07-18). Staff
  // reopen too since 24 Sep 2026 - the server's rule, children/views.py.
  const showArchiveColumns = canManage && status === 'inactive';

  useEffect(() => { if (status !== 'inactive') setReasonFilter(''); }, [status]);

  const load = useCallback(() => {
    const today = new Date();
    const weekAhead = new Date(today); weekAhead.setDate(today.getDate() + 7);
    return loadAll(toast, [
      // Include inactive (terminated) cases — the V2 roster shows them with chips.
      () => api.get('/children/?include_archived=true').then((r) => setChildren(r.data)),
      // Active psychologists + current caseload. Admin/staff only, at both
      // ends: the endpoint is IsAdminOrStaff, and the list is only rendered
      // behind canManage. Asking as a psychologist earned a guaranteed 403.
      canManage && (() => api.get('/psychologists/').then((r) => setPsychologists(r.data))),
      // Availability blocks power the assignment-time comparison panel — admin/staff only.
      canManage && (() => api.get('/availability/').then((r) => setBlocks(r.data))),
      // Upcoming (next 7 days) scheduled appointments → roster chips + drawer list.
      // Role-scoped server-side: psychologists get only their own caseload.
      () => api.get(`/appointments/?from=${localDate(today)}&to=${localDate(weekAhead)}`)
        .then((r) => {
          const map = {};
          (r.data || [])
            .filter((a) => a.status === 'scheduled')
            .sort((a, b) => a.start.localeCompare(b.start))
            .forEach((a) => { (map[a.child] ||= []).push(a); });
          setApptsByChild(map);
        })
        // Clear rather than keep: stale chips on a reloaded roster claim
        // appointments that may no longer exist. Rethrown so the screen
        // still reports the failure once, with everything else.
        .catch((e) => { setApptsByChild({}); throw e; }),
    ], 'Could not load the records. Check your connection and refresh.');
  }, [canManage, toast]);
  useEffect(() => { load(); }, [load]);

  const setQ = (v) => setSearchParams(v ? { q: v } : {}, { replace: true });

  const rows = useMemo(() => children.map((c) => ({
    ...c,
    age: ageFrom(c.birth_date),
    group: ageGroup(ageFrom(c.birth_date)),
    ref: caseRef(c.id),
  })), [children]);

  const counts = { all: rows.length, active: 0, inactive: 0 };
  rows.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1; });

  // Adviser: improve alphabetical sorting throughout the system.
  const visible = rows
    .filter((c) => c.fullname.toLowerCase().includes(q.toLowerCase()) || c.ref.toLowerCase().includes(q.toLowerCase()))
    .filter((c) => status === 'all' || c.status === status)
    .filter((c) => !showArchiveColumns || !reasonFilter || c.termination?.reason_category === reasonFilter)
    .sort((a, b) => sortMode === 'newest'
      ? b.id - a.id  // LIFO: newest record first
      : a.fullname.localeCompare(b.fullname, undefined, { sensitivity: 'base' }));

  const STATUS_FILTERS = [
    { key: 'active', label: 'Active' },
    { key: 'inactive', label: 'Archived' },
    { key: 'all', label: 'All' },
  ];
  const dotColor = { active: 'var(--success-500)', inactive: 'var(--text-faint)' };

  /* Columns fold against the CENTRE PANE, not the window. With both rails up a
   * 1600px window leaves this table about 1000px, and sizing it against 1600
   * is what used to push it into a horizontal scroll. */
  const columns = showArchiveColumns
    ? [
      { key: 'child', label: 'Child' }, { key: 'type', label: 'Case type' },
      { key: 'on', label: 'Terminated on' }, { key: 'reason', label: 'Reason' },
      { key: 'by', label: 'Terminated by' }, { key: 'note', label: 'Note' },
      { key: 'act', label: '' },
    ]
    : [
      { key: 'child', label: 'Child' },
      { key: 'type', label: 'Case type' },
      ...(layout.recordsCategoryCol ? [{ key: 'cat', label: 'Category' }] : []),
      { key: 'status', label: 'Case status' },
      ...(layout.recordsPsychCol ? [{ key: 'psych', label: 'Psychologist' }] : []),
      { key: 'pa', label: 'Pre-assessment' },
      { key: 'next', label: 'Next appointment' },
      { key: 'act', label: '', right: true },
    ];

  // Whatever the fold dropped is appended under the child's name, so a narrow
  // window loses the column and never the fact.
  const subLine = (c) => {
    if (showArchiveColumns) return [c.ref, c.age != null ? `${c.age}y` : null].filter(Boolean).join(' · ');
    return [
      c.ref,
      c.age != null ? `${c.age}y` : null,
      !layout.recordsCategoryCol ? c.case_category : null,
      !layout.recordsPsychCol ? (c.psychologist_name || 'no psychologist') : null,
    ].filter(Boolean).join(' · ');
  };

  /* Built in the browser from the rows already on screen — there is no export
   * endpoint, and adding one would put a second, differently-scoped copy of
   * the roster behind a URL. This can only ever contain what this account is
   * already looking at. */
  const exportCsv = () => {
    const head = showArchiveColumns
      ? ['Case ref', 'Child', 'Age', 'Case type', 'Terminated on', 'Reason', 'Terminated by', 'Note']
      : ['Case ref', 'Child', 'Age', 'Gender', 'Case type', 'Category', 'Case status', 'Psychologist', 'Pre-assessment'];
    const body = visible.map((c) => (showArchiveColumns
      ? [c.ref, c.fullname, c.age, c.case_type, c.termination?.date, c.termination?.reason_category, c.termination?.terminated_by, c.termination?.note]
      : [c.ref, c.fullname, c.age, c.gender, c.case_type, c.case_category, c.status === 'active' ? 'Active' : 'Archived', c.psychologist_name, c.pre_assessment_status]));
    const csv = [head, ...body].map((r) => r.map(csvCell).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `racco1-records-${localDate(new Date())}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const canTerminate = (c) => c.status === 'active'
    && (isAdmin || (isPsych && String(c.psychologist) === String(user?.id)));
  const canEditRecord = (c) => canManage
    || (isPsych && c.status === 'active' && String(c.psychologist) === String(user?.id));

  // Per-user draft key so an unsaved intake typed by one user never leaks
  // into another user's "Add Record" session on a shared workstation.
  const draftKey = `nacc-child-draft:${user?.id ?? 'anon'}`;

  const openCreate = () => {
    setError(''); setFieldErrors(null);
    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(draftKey) || 'null'); } catch { /* corrupt draft */ }
    const meaningful = draft && Object.entries(draft).some(([k, v]) => k !== 'assignee_sees_history' && v);
    setForm({ ...EMPTY, _draft: meaningful ? draft : null });
  };
  const openEdit = (c) => { setError(''); setFieldErrors(null); setForm({ ...EMPTY, ...c, psychologist: c.psychologist || '', _origPsychologist: c.psychologist || '' }); };

  /* /children?openCreate=1 opens the intake form straight away — it is what the
   * dashboard's "Add Record" action links to. The parameter is cleared once
   * used so a refresh, or a back-navigation, does not reopen the form over
   * whatever the person moved on to. */
  const autoOpenCreate = searchParams.get('openCreate') === '1';
  useEffect(() => {
    if (!autoOpenCreate || form) return;
    openCreate();
    const next = new URLSearchParams(searchParams);
    next.delete('openCreate');
    setSearchParams(next, { replace: true });
    // openCreate is stable enough for this one-shot; re-running on every render
    // would fight the user closing the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpenCreate, form]);

  const save = async (e) => {
    e.preventDefault();
    const name = form.id ? form.fullname
      : [form.first_name, form.middle_name, form.last_name].filter(Boolean).join(' ');
    const reassigning = form.id && String(form.psychologist || '') !== String(form._origPsychologist || '');
    const assignee = psychologists.find((p) => String(p.id) === String(form.psychologist))?.name;
    const ok = await confirm({
      description: form.id
        ? `This saves your changes to ${name}'s record.`
        : `This adds ${name} to Records. The child's name cannot be changed once the record is saved, so check the spelling.`,
      confirmLabel: form.id ? 'Yes, save changes' : 'Yes, add the record',
      details: form.id
        ? [['Reassigned to', reassigning ? (assignee || 'Unassigned') : null]]
        : [['Category', form.case_category], ['Case type', form.case_type],
           ['Date of birth', form.birth_date], ['Psychologist', assignee || 'Unassigned'],
           ['Case referral', form.referralFile?.name]],
    });
    if (!ok) return;
    setError('');
    setFieldErrors(null);
    const payload = { ...form, expected_updated_at: form.updated_at };
    delete payload.age; delete payload.group; delete payload.ref;
    delete payload.psychologist_name;
    delete payload._origPsychologist; delete payload.termination; delete payload.photo;
    delete payload.updated_at; delete payload._conflict; delete payload._draft;
    // A file, not a column. It is uploaded separately once the child exists,
    // because a CaseReferral needs a child id to belong to.
    const referralFile = form.referralFile || null;
    delete payload.referralFile;
    if (!payload.psychologist) payload.psychologist = null;
    if (!payload.birth_date) delete payload.birth_date;
    // Sent as null, not left out: the form clears the date a case no longer
    // asks for, and leaving it out of the request would keep it on the record.
    for (const f of ['date_of_admission', 'date_of_placement_to_custodian', 'date_found']) {
      if (!payload[f]) payload[f] = null;
    }
    if (form.id) delete payload.fullname;
    try {
      let saved;
      if (form.id) saved = (await api.put(`/children/${form.id}/`, payload)).data;
      else saved = (await api.post('/children/', payload)).data;
      try { localStorage.removeItem(draftKey); } catch { /* private browsing */ }

      /* The referral goes up straight after, against the id the child has just
         been given. Separately on purpose: a CaseReferral belongs to a child,
         so there is no id to attach it to until the record exists.

         A failure here must be loud. The child IS saved at this point, and a
         quiet failure leaves somebody with a record they cannot book against
         and no idea why — which is exactly the dead end this field was added
         to remove. */
      let referralFailed = false;
      if (referralFile && saved?.id) {
        const fd = new FormData();
        fd.append('child', saved.id);
        fd.append('file', referralFile);
        fd.append('description', 'Case referral');
        try {
          await api.post('/case-referrals/', fd);
        } catch {
          referralFailed = true;
        }
      }

      if (referralFailed) {
        toast.error('Record saved, but the case referral did not upload. '
          + 'Open the record and try again — sessions cannot be booked without it.');
      } else {
        toast.success(form.id ? 'Record updated' : 'Record added');
      }
      setForm(null);
      load();
      refreshActivity();
    } catch (err) {
      if (err.response?.status === 409) {
        const fresh = err.response.data.current;
        setError('');
        setForm((f) => ({ ...f, _conflict: fresh }));
        toast.error('Someone updated this record while you were editing.');
        return;
      }
      /* Field by field, beside the fields, rather than one line of JSON: the
         form opens the step holding the first field named. */
      const body = err.response?.data;
      const perField = body && typeof body === 'object' && !Array.isArray(body) ? body : null;
      setFieldErrors(perField);
      setError(perField
        ? (perField.detail || perField.non_field_errors?.join(' ') || 'Some answers need correcting — they are marked below.')
        : 'Save failed. Please try again.');
      toast.error('Could not save the record. Please check the marked fields.');
    }
  };

  const terminate = async (c, reason, note) => {
    try {
      await api.post(`/children/${c.id}/terminate/`, { reason_category: reason, note });
      toast.success(`${c.fullname}'s case is now inactive`);
      setTerminating(null);
      setSel(null);
      load();
      refreshActivity();
    } catch (err) {
      const d = err.response?.data;
      toast.error(d?.note || d?.reason_category || d?.detail || 'Could not terminate the case.');
    }
  };

  const reopen = async () => {
    const c = reopening;
    setReopenBusy(true);
    try {
      await api.post(`/children/${c.id}/reopen/`);
      toast.success(`${c.fullname}'s case is active again — previous records retained`);
      setReopening(null);
      setSel(null);
      // Reopened from the Add Record duplicate warning: the old record is the
      // one being kept, so the half-typed new one and its draft go.
      if (c.fromForm) {
        setForm(null);
        try { localStorage.removeItem(draftKey); } catch { /* private browsing */ }
      }
      load();
      refreshActivity();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not reopen the case.');
    } finally { setReopenBusy(false); }
  };

  // Duplicate-check warning shortcuts (Add Record form): reuse the existing
  // reopen() for archived matches, or just open the active match's drawer.
  // Through the same confirmation as every other reopen. It used to call
  // reopen() with the match as an argument reopen() never read, so it reopened
  // nothing and closed the form anyway.
  const onDupReopen = (m) => setReopening({ id: m.id, fullname: m.fullname, fromForm: true });
  const onDupOpenExisting = (m) => { setForm(null); const c = rows.find((r) => r.id === m.id); if (c) setSel(c); };

  return (
    <div style={{ ...PAGE, position: 'relative' }}>
      <PageHeader
        title="Records"
        subtitle={`${counts.all} children · ${counts.active || 0} active · ${counts.inactive || 0} archived`}
      >
        <Button variant="secondary" onClick={exportCsv} iconLeft={<Icon name="download" size={17} />}>Export CSV</Button>
        {canManage && (
          <Button variant="primary" onClick={openCreate} iconLeft={<Icon name="user-plus" size={18} />}>Add record</Button>
        )}
      </PageHeader>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        {/* Toolbar. Search, the counted status filters and the sort, all in
            the card's own header strip rather than floating above it — the
            filters and the rows they filter belong to the same object. */}
        <div style={TOOLBAR}>
          <Input
            size="sm" style={{ width: 250 }} fullWidth={false}
            placeholder="Name or case ref…" value={q} onChange={(e) => setQ(e.target.value)}
            leading={<Icon name="search" size={16} />} aria-label="Search records"
          />
          <FilterPills
            label="Filter children by status"
            value={status} onChange={setStatus}
            options={STATUS_FILTERS.map((f) => ({ ...f, dot: dotColor[f.key], count: counts[f.key] || 0 }))}
          />
          <span style={{ flex: 1 }} />
          {showArchiveColumns && (
            <div style={{ width: 210 }}>
              <Select size="sm" value={reasonFilter} onChange={(e) => setReasonFilter(e.target.value)} aria-label="Filter by termination reason">
                <option value="">All termination reasons</option>
                {TERMINATION_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
              </Select>
            </div>
          )}
          <Segmented
            label="Sort"
            value={sortMode} onChange={setSortMode}
            options={[{ value: 'newest', label: 'Newest' }, { value: 'az', label: 'A–Z' }]}
          />
        </div>

        {visible.length === 0 ? (
          <EmptyState icon={<Icon name="folder-search" size={24} />} title="No records found" description="Try a different name, case ID, or status filter." />
        ) : (
          <div className="racco-scroll" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={THEAD_ROW}>
                  {columns.map((h) => (
                    <th key={h.key} scope="col" style={{ ...TH, textAlign: h.right ? 'right' : 'left' }}>{h.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((c) => (
                  <tr
                    key={c.id} tabIndex={0} role="button" aria-label={`${c.fullname}, case ${c.ref}. Open details.`}
                    onClick={() => setSel(c)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSel(c); } }}
                    style={{ ...TR, cursor: 'pointer', transition: 'background var(--dur-fast) var(--ease-out)', opacity: c.status === 'inactive' ? 0.72 : 1 }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--blue-50)')} onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ padding: '6px 12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
                        <Avatar name={c.fullname} tone={c.status === 'inactive' ? 'neutral' : 'brand'} size={26} />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontWeight: 700, fontSize: 12.5, lineHeight: 1.25, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.fullname}</div>
                          {/* Whatever this width dropped is appended here, so
                              no field is ever actually lost — only moved. */}
                          <div className="racco-mono" style={{ fontSize: 10.5, lineHeight: 1.3, color: 'var(--text-faint)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{subLine(c)}</div>
                        </div>
                      </div>
                    </td>
                    {showArchiveColumns ? (
                      <>
                        <td style={{ ...TD, whiteSpace: 'nowrap' }}>{c.case_type || '—'}</td>
                        <td style={{ ...TD, whiteSpace: 'nowrap' }} className="racco-mono">{c.termination?.date || '—'}</td>
                        <td style={TD}>{c.termination?.reason_category
                          ? <Badge tone="neutral" size="sm" dot>{c.termination.reason_category}</Badge> : '—'}</td>
                        <td style={{ ...TD, whiteSpace: 'nowrap' }}>{c.termination?.terminated_by || '—'}</td>
                        <td style={{ ...TD, maxWidth: 260 }}>
                          <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-muted)' }} title={c.termination?.note || ''}>
                            {c.termination?.note || '—'}
                          </span>
                        </td>
                        <td style={{ padding: '6px 12px' }}>
                          <div style={{ display: 'flex', gap: 6 }}>
                            <button title="View full record" aria-label={`View ${c.fullname}'s record`} onClick={(e) => { e.stopPropagation(); navigate(`/report/child/${c.id}`); }} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--blue-600)')}><Icon name="eye" size={15} /></button>
                            {canManage && (
                              <button title="Reopen case" aria-label={`Reopen ${c.fullname}'s case`}
                                onClick={(e) => { e.stopPropagation(); setReopening(c); }}
                                {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--success-600)')}><Icon name="rotate-ccw" size={15} /></button>
                            )}
                          </div>
                        </td>
                      </>
                    ) : (
                      <>
                        <td style={{ ...TD, fontWeight: 600, whiteSpace: 'nowrap' }}>{c.case_type || '—'}</td>
                        {layout.recordsCategoryCol && <td style={{ ...TD, whiteSpace: 'nowrap' }}>{c.case_category || '—'}</td>}
                        {/* Just Active/Archived — Case type is its own column
                            now, and the chip repeating it read as the same
                            fact stated twice on one row. */}
                        <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                          <Badge tone={c.status === 'active' ? 'success' : 'neutral'} size="sm" dot>
                            {c.status === 'active' ? 'Active' : 'Archived'}
                          </Badge>
                        </td>
                        {layout.recordsPsychCol && (
                          <td style={{ ...TD, fontWeight: 600, whiteSpace: 'nowrap' }}>
                            {c.psychologist_name || (canManage && c.status === 'active' ? (
                              <button title={`Assign a psychologist to ${c.fullname}`} aria-label={`Assign psychologist to ${c.fullname}`}
                                onClick={(e) => { e.stopPropagation(); openEdit(c); }} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })}
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 'var(--radius-pill)', border: '1px dashed var(--blue-300)', background: 'var(--blue-50)', color: 'var(--blue-700)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 11.5, cursor: 'pointer' }}>
                                <Icon name="user-plus" size={13} /> Assign
                              </button>
                            ) : '—')}
                          </td>
                        )}
                        <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 700, fontSize: 11.5, color: 'var(--text-body)' }}>
                            <span style={{ width: 6, height: 6, borderRadius: '50%', background: PA_DOT[c.pre_assessment_status] || 'var(--ink-300)' }} />
                            {c.pre_assessment_status || '—'}
                          </span>
                        </td>
                        <td style={{ ...TD, whiteSpace: 'nowrap' }}>{c.status === 'active' ? <ScheduleChip appts={apptsByChild[c.id]} /> : <span style={{ color: 'var(--text-faint)' }}>—</span>}</td>
                        <td style={{ padding: '6px 10px', textAlign: 'right' }}>
                          <Icon name="more-horizontal" size={17} style={{ color: 'var(--text-faint)' }} />
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div style={{ padding: '9px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, background: 'var(--ink-25)', borderTop: '1px solid var(--divider)' }}>
          <span style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>
            Showing <strong style={{ color: 'var(--text-strong)' }}>{visible.length}</strong> of {rows.length} children
          </span>
          {canManage && psychologists.length > 0 && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)' }}>Caseload</span>
              {psychologists.map((p) => (
                <span key={p.id} title={`${p.name}: ${p.caseload} active case${p.caseload === 1 ? '' : 's'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 'var(--radius-pill)', background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 11.5 }}>
                  <span style={{ fontWeight: 600, color: 'var(--text-body)' }}>{p.name}</span>
                  <span className="racco-mono" style={{ fontWeight: 800, color: p.caseload >= 5 ? 'var(--red-700)' : 'var(--blue-600)' }}>{p.caseload}</span>
                </span>
              ))}
            </span>
          )}
        </div>
      </div>

      {sel && <ChildDrawer child={sel} upcoming={apptsByChild[sel.id] || []} canEdit={canEditRecord(sel)} canTerminate={canTerminate(sel)} canReopen={canManage} others={others} onEdit={() => { openEdit(sel); setSel(null); }} onTerminate={() => setTerminating(sel)} onReopen={() => setReopening(sel)} onClose={() => setSel(null)} />}
      {form && <ChildForm form={form} setForm={setForm} draftKey={draftKey} psychologists={psychologists} blocks={blocks} error={error} isPsych={isPsych} canReopen={canManage} others={others} fieldErrors={fieldErrors} onSubmit={save} onClose={() => setForm(null)} onReopen={onDupReopen} onOpenExisting={onDupOpenExisting} />}
      {terminating && <TerminateModal child={terminating} onConfirm={terminate} onClose={() => setTerminating(null)} />}
      {reopening && (
        <ConfirmDialog
          onClose={() => setReopening(null)}
          onConfirm={reopen}
          busy={reopenBusy}
          tone="brand"
          icon={<Icon name="rotate-ccw" size={19} />}
          title={`Reopen ${reopening.fullname}'s case?`}
          description="Records and termination history are kept — nothing is erased by reopening."
          confirmLabel="Reopen the case" cancelLabel="Leave it closed"
        >
          {/* The half people forget, and the half that needs doing next: the
              case comes back with nobody responsible for it. */}
          <Alert tone="warning" icon={<Icon name="user-x" size={18} />}>
            The psychologist assignment is cleared. Assign one fresh afterwards,
            or the case sits in nobody’s caseload.
          </Alert>
        </ConfirmDialog>
      )}
    </div>
  );
}

