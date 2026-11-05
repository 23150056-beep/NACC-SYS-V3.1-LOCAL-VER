import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useLayout } from '../context/LayoutContext';
import { useToast } from '../context/ToastContext';
import {
  Avatar, EmptyState, Icon, Input, PAGE, PageHeader, TD, TH, THEAD_ROW, TR,
} from '../ui';
import { paProgressLabel, formatStepMoment } from '../utils/preAssessment';

/* One row per child, and eight columns' worth of case state on it.
 *
 * This table carries three more columns than Records does, so it folds one
 * step earlier at every width. Whatever a fold drops is appended under the
 * child's name — a narrow window loses the column, never the fact.
 */
export default function Monitoring() {
  const navigate = useNavigate();
  const layout = useLayout();
  const toast = useToast();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState('');

  useEffect(() => {
    api.get('/reports/monitoring/').then((r) => setRows(r.data))
      .catch(() => toast.error('Could not load progress monitoring. Check your connection and refresh.'));
  }, [toast]);

  const visible = useMemo(() => rows
    .filter((r) => (r.child_name || '').toLowerCase().includes(q.toLowerCase())
      || (r.case_ref || '').toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (a.child_name || '').localeCompare(b.child_name || '', undefined, { sensitivity: 'base' })),
  [rows, q]);

  // Monitoring lists active children only, so there is no closed row to word.
  const caseType = (r) => r.case_type || '—';

  // "In progress · Instruments · edited Today, 9:30 AM": the tracker's last edit.
  const paEdited = (r) => {
    const at = r.pre_assessment_progress?.step_edited_at;
    return at ? `edited ${formatStepMoment(at)}` : '';
  };
  const paText = (r) => paProgressLabel(r.pre_assessment_progress);

  const subLine = (r) => [
    r.case_ref,
    !layout.monitorPsychCol ? (r.psychologist_name || 'no psychologist') : null,
    !layout.monitorPaCol ? paText(r) : null,
    !layout.monitorClassCol ? r.latest_classification : null,
    !layout.monitorLastCol && r.last_activity ? `last ${r.last_activity}` : null,
  ].filter(Boolean).join(' · ');

  const columns = [
    { key: 'child', label: 'Child' },
    { key: 'status', label: 'Case type', wrap: true },
    ...(layout.monitorPsychCol ? [{ key: 'psych', label: 'Psychologist' }] : []),
    ...(layout.monitorPaCol ? [{ key: 'pa', label: 'Pre-assessment' }] : []),
    ...(layout.monitorClassCol ? [{ key: 'class', label: 'Classification' }] : []),
    { key: 'remark', label: 'Latest remark', wide: true },
    ...(layout.monitorLastCol ? [{ key: 'last', label: 'Last activity' }] : []),
    { key: 'next', label: 'Next session' },
  ];

  return (
    <div style={{ ...PAGE, position: 'relative' }}>
      <PageHeader title="Progress Monitoring" subtitle="One row per child · open a row for the full progress report">
        <Input
          size="md" style={{ width: 300 }} fullWidth={false}
          placeholder="Search by child name or case ID…" value={q} onChange={(e) => setQ(e.target.value)}
          leading={<Icon name="search" size={17} />} aria-label="Search children"
        />
        <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
          Showing <strong style={{ color: 'var(--text-strong)' }}>{visible.length}</strong> of {rows.length} children
        </span>
      </PageHeader>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        {visible.length === 0 ? (
          <EmptyState icon={<Icon name="folder-search" size={24} />} title="No children to monitor" description="Try a different name or case ID." />
        ) : (
          <div className="racco-scroll" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-sans)' }}>
              <thead>
                <tr style={THEAD_ROW}>
                  {columns.map((h) => (
                    <th key={h.key} scope="col" style={{ ...TH, ...(h.wide ? { minWidth: 200, whiteSpace: 'normal' } : {}), ...(h.wrap ? { maxWidth: 180 } : {}) }}>{h.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const open = () => navigate(`/report/child/${r.child_id}`);
                  const borderline = (r.latest_classification || '').startsWith('Borderline');
                  return (
                    <tr
                      key={r.child_id} tabIndex={0} role="button" aria-label={`Open ${r.child_name}'s progress report`}
                      onClick={open}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
                      style={{ ...TR, cursor: 'pointer', transition: 'background var(--dur-fast) var(--ease-out)' }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--blue-50)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    >
                      <td style={{ padding: '6px 12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                          <Avatar name={r.child_name} size={26} />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 700, fontSize: 12.5, lineHeight: 1.25, color: 'var(--blue-700)', whiteSpace: 'nowrap' }}>{r.child_name}</div>
                            <div className="racco-mono" style={{ fontSize: 10.5, lineHeight: 1.3, color: 'var(--text-faint)' }}>{subLine(r)}</div>
                          </div>
                        </div>
                      </td>
                      {/* Wraps rather than running on: "Family Tracing &
                          Reunification" is long. */}
                      <td style={{ ...TD, maxWidth: 180, lineHeight: 1.4 }}>{caseType(r)}</td>
                      {layout.monitorPsychCol && <td style={{ ...TD, whiteSpace: 'nowrap' }}>{r.psychologist_name || '—'}</td>}
                      {layout.monitorPaCol && <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                        {paText(r)}
                        {paEdited(r) && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{paEdited(r)}</div>}
                      </td>}
                      {layout.monitorClassCol && (
                        <td style={{ ...TD, whiteSpace: 'nowrap', fontWeight: 600, color: borderline ? 'var(--warning-700)' : r.latest_classification ? 'var(--text-body)' : 'var(--text-faint)' }}>
                          {r.latest_classification || '—'}
                        </td>
                      )}
                      {/* Wraps rather than truncating: a remark is the one cell
                          on this row worth reading in full. */}
                      <td style={{ ...TD, minWidth: 200, maxWidth: 320, whiteSpace: 'normal', lineHeight: 1.45 }}>{r.latest_remark || '—'}</td>
                      {layout.monitorLastCol && <td style={{ ...TD, whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>{r.last_activity || '—'}</td>}
                      <td style={{ ...TD, whiteSpace: 'nowrap', fontWeight: 600 }}>{r.next_session || '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
