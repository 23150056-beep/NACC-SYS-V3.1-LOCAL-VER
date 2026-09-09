import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useToast } from '../context/ToastContext';
import {
  Badge, Button, EmptyState, Icon, IconChip, PAGE, PageHeader, Segmented,
  TD, TH, THEAD_ROW, TOOLBAR, TR,
} from '../ui';
import {
  admitChild, getBoard, STAGE_ICONS, STATUS_META,
} from '../api/adoption';
import { caseRef, initialsOf } from '../utils/child';

/* The adoption process tracker.
 *
 * A second lifecycle attached to a child record: it picks the child up the
 * moment the psychologist marks the assessment complete and follows eight
 * statutory stages to the final decree.
 *
 * Everything on this screen is derived server-side and read here — no status
 * is stored, so a chip can never disagree with the docket behind it. The whole
 * screen is one request for the same reason: the stage counts, the cards, the
 * tiles and the worklist are views of one set of cases, and fetching them
 * separately is how "9 open" ends up above a board with eight cards.
 */

const cardStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden',
};

function StatusChip({ status }) {
  const m = STATUS_META[status] || STATUS_META.on_track;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', height: 20, padding: '0 9px', borderRadius: 'var(--radius-pill)', background: m.bg, color: m.color, fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 10.5, whiteSpace: 'nowrap' }}>
      {m.label}
    </span>
  );
}

/* The banner. Hidden entirely when nothing is waiting — no empty state, per
   the spec: an empty banner trains people to stop reading it. */
function HandoffBanner({ handoffs, onAdmit, busy }) {
  const [open, setOpen] = useState(false);
  if (!handoffs.length) return null;

  return (
    <div style={{ ...cardStyle, border: '1px solid var(--warning-100)' }}>
      <div style={{ padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--warning-50)', borderBottom: open ? '1px solid var(--warning-100)' : 'none' }}>
        <IconChip icon="inbox" tone="warning" style={{ background: 'var(--warning-100)' }} />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>
            {handoffs.length} {handoffs.length === 1 ? 'child was' : 'children were'} returned by the psychologist
          </span>
          <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
            The assessment is complete, so the record is back with staff. Admit them into the pipeline
            or route them to another case plan.
          </span>
        </span>
        <Button variant="secondary" size="sm" onClick={() => setOpen((o) => !o)}>
          {open ? 'Hide' : 'Review handoffs'}
        </Button>
      </div>
      {open && handoffs.map((h) => (
        <div key={h.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderBottom: '1px solid var(--divider-row)' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 11.5 }}>
            {initialsOf(h.child_name)}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{h.child_name}</span>
            <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
              {caseRef(h.child)} · released by {h.released_by_name || 'the psychologist'} · current plan {h.case_type || 'unset'}
            </span>
          </span>
          <Button variant="primary" size="sm" disabled={busy} onClick={() => onAdmit(h)}>
            Admit to pipeline
          </Button>
        </div>
      ))}
    </div>
  );
}

/* Four counts, and every one of them is a filter. The spec is explicit that a
   tile must never be a dead number. */
function KpiTiles({ kpis, onFilterStage, stageFilter }) {
  const tiles = [
    { key: 'open', label: 'Open cases', value: kpis.open_cases, hint: 'in the pipeline now', stage: null, icon: 'folder-open' },
    { key: 'cdclaa', label: 'Awaiting CDCLAA', value: kpis.at_cdclaa, hint: 'stage 2 · with the court', stage: 2, icon: 'gavel' },
    {
      key: 'trial',
      label: 'Trial custody',
      value: kpis.in_trial_custody,
      hint: kpis.trial_custody_overrun
        ? `${kpis.trial_custody_overrun} past the window`
        : 'all within the window',
      stage: 6,
      icon: 'home',
      alarm: kpis.trial_custody_overrun > 0,
    },
    { key: 'final', label: 'Finalized', value: kpis.finalized_this_year, hint: 'this calendar year', stage: null, icon: 'party-popper' },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12 }}>
      {tiles.map((t) => (
        <button
          key={t.key} type="button" onClick={() => onFilterStage(t.stage)}
          title={t.stage ? `Show only stage ${t.stage}` : 'Show every open case'}
          style={{
            ...cardStyle, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 7,
            cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)',
            borderColor: stageFilter === t.stage && t.stage ? 'var(--blue-300)' : 'var(--border)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 800, fontSize: 'var(--text-3xs)', letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{t.label}</span>
            <IconChip icon={t.icon} tone={t.alarm ? 'danger' : 'brand'} />
          </div>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 34, lineHeight: 1, color: 'var(--text-strong)', fontVariantNumeric: 'tabular-nums' }}>{t.value ?? 0}</span>
          <span style={{ fontSize: 12, color: t.alarm ? 'var(--red-700)' : 'var(--text-muted)' }}>{t.hint}</span>
        </button>
      ))}
    </div>
  );
}

/* The orientation device. It teaches the process to somebody new and doubles
   as the board's filter, which is why it is eight tiles and not a dropdown. */
function StageStrip({ stages, value, onChange }) {
  return (
    <div style={cardStyle}>
      <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--divider)' }}>
        <h3 style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 15, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
          The eight steps, end to end
        </h3>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
          Click a step to filter the board. Click it again to clear.
        </p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 1, background: 'var(--divider)' }}>
        {stages.map((s) => {
          const on = value === s.number;
          return (
            <button
              key={s.number} type="button"
              onClick={() => onChange(on ? null : s.number)}
              style={{
                background: on ? 'var(--blue-50)' : 'var(--surface)', border: 'none',
                padding: '11px 13px', display: 'flex', flexDirection: 'column', gap: 5,
                cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="racco-mono" style={{ fontWeight: 800, fontSize: 10.5, color: on ? 'var(--blue-700)' : 'var(--text-faint)' }}>
                  {String(s.number).padStart(2, '0')}
                </span>
                <Icon name={STAGE_ICONS[s.number] || 'circle'} size={15} style={{ color: on ? 'var(--blue-600)' : 'var(--text-muted)' }} />
                <span style={{ flex: 1 }} />
                <span className="racco-mono" style={{ fontWeight: 800, fontSize: 13, color: s.case_count ? 'var(--text-strong)' : 'var(--text-faint)' }}>
                  {s.case_count}
                </span>
              </span>
              <span style={{ fontWeight: 700, fontSize: 12, lineHeight: 1.3, color: 'var(--text-strong)' }}>{s.name}</span>
              <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>{s.owner_role}</span>
              {s.target_days ? (
                <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>target {s.target_days} days</span>
              ) : (
                <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>no statutory window</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CaseCard({ c, onOpen }) {
  const m = STATUS_META[c.status] || STATUS_META.on_track;
  const border = c.status === 'overdue' ? 'var(--red-300)'
    : c.status === 'at_risk' ? 'var(--warning-100)' : 'var(--border)';
  return (
    <button
      type="button" onClick={() => onOpen(c)}
      style={{
        width: '100%', background: 'var(--surface)', border: `1px solid ${border}`,
        borderLeft: `3px solid ${m.color}`, borderRadius: 'var(--radius-control)',
        padding: '10px 11px', display: 'flex', flexDirection: 'column', gap: 7,
        cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 10 }}>
          {initialsOf(c.child_name)}
        </span>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontWeight: 700, fontSize: 12.5, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.child_name}</span>
          <span className="racco-mono" style={{ display: 'block', fontSize: 10, color: 'var(--text-faint)' }}>{caseRef(c.child)}</span>
        </span>
      </span>
      {c.next_action && (
        <span style={{ fontSize: 11.5, lineHeight: 1.4, color: 'var(--text-body)' }}>
          <span style={{ color: 'var(--text-faint)' }}>Next: </span>{c.next_action}
        </span>
      )}
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <StatusChip status={c.status} />
        <span style={{ flex: 1 }} />
        <span className="racco-mono" style={{ fontSize: 10.5, color: 'var(--text-muted)' }}>
          {c.days_in_stage}d{c.stage_target_days ? ` / ${c.stage_target_days}` : ''}
        </span>
      </span>
      {/* The whole journey, not this stage — so finishing step 1 of 8 cannot
          read as nearly done. */}
      <span style={{ display: 'block', height: 4, borderRadius: 'var(--radius-pill)', background: 'var(--divider)', overflow: 'hidden' }}>
        <span style={{ display: 'block', height: '100%', width: `${c.progress_percent}%`, background: 'var(--blue-600)' }} />
      </span>
    </button>
  );
}

function NeedsAttention({ items, onOpen }) {
  return (
    <div style={cardStyle}>
      <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--divider)' }}>
        <IconChip icon="siren" tone={items.length ? 'danger' : 'success'} />
        <h3 style={{ flex: 1, fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 15, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
          Needs attention
        </h3>
        <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>ranked by severity</span>
      </div>
      {items.length === 0 ? (
        <p style={{ padding: '16px 14px', fontSize: 12.5, color: 'var(--text-muted)' }}>
          Nothing is overdue today.
        </p>
      ) : items.map((i, idx) => (
        <button
          key={`${i.case_id}-${i.kind}-${idx}`} type="button" onClick={() => onOpen(i.case_id)}
          style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderBottom: idx < items.length - 1 ? '1px solid var(--divider-row)' : 'none', border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--blue-50)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 11 }}>
            {initialsOf(i.child_name)}
          </span>
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)' }}>{i.child_name}</span>
            <span style={{ display: 'block', fontSize: 12, color: 'var(--text-body)' }}>{i.message}</span>
          </span>
          <Badge tone={i.severity <= 1 ? 'danger' : i.severity === 2 ? 'warning' : 'neutral'} size="sm">
            step {i.stage}
          </Badge>
        </button>
      ))}
    </div>
  );
}

export default function AdoptionTracker() {
  const navigate = useNavigate();
  const toast = useToast();
  const [data, setData] = useState(null);
  const [view, setView] = useState('board');
  const [stageFilter, setStageFilter] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getBoard().then(setData).catch(() => setData('error'));
  }, []);
  useEffect(() => { load(); }, [load]);

  const openCase = (id) => navigate(`/adoption/case/${id}`);

  const admit = async (handoff) => {
    setBusy(true);
    try {
      const created = await admitChild(handoff.child);
      toast.success(`${handoff.child_name} admitted to the adoption pipeline.`);
      load();
      openCase(created.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not admit this child.');
    } finally {
      setBusy(false);
    }
  };

  const cases = useMemo(() => {
    const rows = data && data !== 'error' ? data.cases : [];
    return stageFilter ? rows.filter((c) => c.stage === stageFilter) : rows;
  }, [data, stageFilter]);

  if (data === 'error') {
    return (
      <div style={PAGE}>
        <EmptyState
          icon={<Icon name="wifi-off" size={24} />}
          title="The adoption tracker could not be loaded"
          description="Nothing has been changed — this is a display problem, not missing cases."
          action={<Button variant="secondary" size="sm" onClick={load}>Try again</Button>}
        />
      </div>
    );
  }
  if (!data) {
    return <div style={PAGE}><div style={{ color: 'var(--text-muted)' }}>Loading the pipeline…</div></div>;
  }

  const stages = data.stages || [];

  return (
    <div style={PAGE}>
      <PageHeader
        title="Adoption Process Tracker"
        subtitle={`Domestic administrative adoption · RA 11642 · ${data.kpis.open_cases} ${data.kpis.open_cases === 1 ? 'child' : 'children'} in process`}
      >
        <Segmented
          label="View"
          value={view} onChange={setView}
          options={[{ value: 'board', label: 'Board' }, { value: 'list', label: 'List' }]}
        />
      </PageHeader>

      <HandoffBanner handoffs={data.handoffs || []} onAdmit={admit} busy={busy} />

      <KpiTiles kpis={data.kpis} stageFilter={stageFilter} onFilterStage={setStageFilter} />

      <StageStrip stages={stages} value={stageFilter} onChange={setStageFilter} />

      {view === 'board' ? (
        <div style={cardStyle}>
          <div style={TOOLBAR}>
            <span style={{ flex: 1, fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>
              {stageFilter
                ? `Showing step ${stageFilter} only — ${cases.length} ${cases.length === 1 ? 'case' : 'cases'}`
                : `${cases.length} open ${cases.length === 1 ? 'case' : 'cases'} across all steps`}
            </span>
            {stageFilter && (
              <Button variant="secondary" size="sm" onClick={() => setStageFilter(null)}>Clear filter</Button>
            )}
          </div>
          <div className="racco-scroll" style={{ display: 'flex', gap: 12, padding: 12, overflowX: 'auto', alignItems: 'flex-start' }}>
            {(stageFilter ? stages.filter((s) => s.number === stageFilter) : stages).map((s) => {
              const column = cases.filter((c) => c.stage === s.number);
              return (
                <div key={s.number} style={{ flex: '0 0 236px', width: 236, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '0 2px' }}>
                    <span className="racco-mono" style={{ fontWeight: 800, fontSize: 10.5, color: 'var(--text-faint)' }}>
                      {String(s.number).padStart(2, '0')}
                    </span>
                    <span style={{ flex: 1, fontWeight: 700, fontSize: 12, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
                    <span className="racco-mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>{column.length}</span>
                  </div>
                  {column.length === 0 ? (
                    <div style={{ border: '1px dashed var(--border-strong)', borderRadius: 'var(--radius-control)', padding: '18px 10px', textAlign: 'center', fontSize: 11.5, color: 'var(--text-faint)' }}>
                      No cases here
                    </div>
                  ) : column.map((c) => <CaseCard key={c.id} c={c} onOpen={() => openCase(c.id)} />)}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div style={cardStyle}>
          {cases.length === 0 ? (
            <EmptyState icon={<Icon name="folder-open" size={24} />} title="No cases in the pipeline"
              description="Children appear here once a psychologist completes their assessment and staff admit them." />
          ) : (
            <div className="racco-scroll" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={THEAD_ROW}>
                    {['Child', 'Current step', 'Next action', 'In step', 'Owner', 'Status'].map((h) => (
                      <th key={h} scope="col" style={TH}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {/* Worst first: the spec's default sort, so the cases that
                      need somebody surface without anybody sorting. */}
                  {[...cases].sort((a, b) => b.days_in_stage - a.days_in_stage).map((c) => (
                    <tr
                      key={c.id} tabIndex={0} role="button"
                      aria-label={`Open ${c.child_name}'s adoption case`}
                      onClick={() => openCase(c.id)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCase(c.id); } }}
                      style={{ ...TR, cursor: 'pointer' }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--blue-50)')}
                      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                    >
                      <td style={{ padding: '7px 12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 10 }}>
                            {initialsOf(c.child_name)}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontWeight: 700, fontSize: 12.5, color: 'var(--blue-700)' }}>{c.child_name}</div>
                            <div className="racco-mono" style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>{caseRef(c.child)}</div>
                          </div>
                        </div>
                      </td>
                      <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                        <span className="racco-mono" style={{ color: 'var(--text-faint)' }}>{String(c.stage).padStart(2, '0')}</span>{' '}{c.stage_name}
                      </td>
                      <td style={{ ...TD, maxWidth: 260 }}>{c.next_action || '—'}</td>
                      <td className="racco-mono" style={{ ...TD, whiteSpace: 'nowrap' }}>
                        {c.days_in_stage}d{c.stage_target_days ? ` / ${c.stage_target_days}` : ''}
                      </td>
                      <td style={{ ...TD, whiteSpace: 'nowrap' }}>{c.owner_name || '—'}</td>
                      <td style={{ ...TD, whiteSpace: 'nowrap' }}><StatusChip status={c.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <NeedsAttention items={data.needs_attention || []} onOpen={openCase} />
    </div>
  );
}
