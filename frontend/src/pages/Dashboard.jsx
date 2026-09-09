import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useCensus } from '../context/CensusContext';
import { useLayout } from '../context/LayoutContext';
import { Icon, IconChip, MiniBar, PAGE, Segmented } from '../ui';
import { RailCards } from '../components/RightRail';
import { caseRef, initialsOf } from '../utils/child';

/* One prioritised stream, not eleven equal tiles.
 *
 * The old dashboard laid every source of information out at the same size, so
 * "four children need a decision today" sat beside a mini calendar with the
 * same visual weight. This reads top to bottom in the order the day actually
 * runs: what needs a decision, then the shape of the caseload, then the
 * pipeline, then the team.
 *
 * The day's schedule, the live census figures and the activity stream are not
 * here — they are the right rail, which stays put while this column scrolls.
 * When the window is too narrow for a third column they come back in along the
 * bottom, so nothing is ever simply missing.
 */

const RANGES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'yearly', label: 'Annual' },
];

/* Each deterministic care-gap rule, in the words the person acting on it would
 * use, plus where that action happens. The rules themselves live in
 * backend/clinical/care_gaps.py — this only names them. */
const GAP_META = {
  consent_missing: { chip: 'No consent', action: 'Attach', to: '/pre-assessment', tone: 'danger' },
  pre_assessment_overdue: { chip: 'Stalled', action: 'Start', to: '/pre-assessment', tone: 'warning' },
  report_missing: { chip: 'Report due', action: 'Upload', to: '/reports', tone: 'warning' },
  follow_up_overdue: { chip: 'Overdue', action: 'Book', to: '/schedule', tone: 'danger' },
  no_upcoming_appointment: { chip: 'Unbooked', action: 'Book', to: '/schedule', tone: 'info' },
  self_report_concern: { chip: 'Unread words', action: 'Read', to: null, tone: 'danger' },
};
const GAP_CHIP = {
  danger: ['var(--red-50)', 'var(--red-700)'],
  warning: ['var(--warning-50)', 'var(--warning-700)'],
  info: ['var(--blue-50)', 'var(--blue-700)'],
};

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

const cardStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden',
};

function CardHead({ icon, tone, title, meta, children }) {
  return (
    <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--divider)' }}>
      {icon && <IconChip icon={icon} tone={tone} />}
      <h3 style={{ flex: 1, fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 15, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>{title}</h3>
      {meta && <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>{meta}</span>}
      {children}
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const layout = useLayout();
  const { stats, range, setRange, pendingAccess } = useCensus();

  const role = user?.role_name || 'Staff';
  const isPsych = role === 'Psychologist';
  const name = user?.fullname || user?.username || '';
  const firstName = name.split(' ')[0] || 'there';

  const census = stats.census || {};
  const gaps = stats.care_gaps || [];
  const caseMix = Object.entries(census.by_case_type || {}).sort((a, b) => b[1] - a[1]);
  const mixMax = caseMix.length ? caseMix[0][1] : 1;
  const trend = stats.intake_vs_termination || [];
  const trendMax = Math.max(1, ...trend.map((b) => Math.max(b.intake, b.terminations)));
  const perPsych = stats.per_psychologist || [];
  const perPsychMax = Math.max(1, ...perPsych.map((p) => p.count ?? 0));
  // The `|| []` stays INSIDE the callback: outside it, a missing key builds a
  // fresh array every render and the memo never holds.
  const caseload = useMemo(
    () => Object.fromEntries(
      (stats.counseling_per_psychologist || []).map((c) => [c.name, c.count]),
    ),
    [stats.counseling_per_psychologist],
  );

  const dueToday = gaps.filter((g) => g.severity === 'danger').length;
  const shownGaps = gaps.slice(0, 4);

  /* Quick actions. Every one of these OPENS something — the form, the booking
   * drawer, the upload drawer, the queue.
   *
   * That is the whole rule, and it is what keeps the row from being redundant.
   * A button that only navigates is a link, and every destination in this app
   * is already two clicks away in the rail on the left and the tab strip
   * above — so a row of links here would be a third copy of the same
   * navigation, wearing verbs. "Agency summary" and "Enter a result" were
   * exactly that and are gone; the rest now carry the deep link that finishes
   * the job (see utils/links.js).
   *
   * Nothing here opens the assistant either: the bubble that does is on this
   * same screen, bottom right, 60px across with a live dot on it. */
  const actions = isPsych ? [
    { label: 'Start pre-assessment', icon: 'clipboard-list', to: '/pre-assessment', primary: true },
    { label: 'Add my availability', icon: 'clock', to: '/schedule?availability=1' },
    { label: 'Upload report', icon: 'upload', to: '/reports?upload=1' },
  ] : [
    { label: 'Add record', icon: 'user-plus', to: '/children?openCreate=1', primary: true },
    { label: 'Book appointment', icon: 'calendar-plus', to: '/schedule?book=1' },
    { label: 'Upload case referral', icon: 'upload', to: '/reports?upload=1' },
    // Only when somebody is actually waiting. A permanent "Review access"
    // leading to an empty queue is a button that cries wolf, and the count is
    // the only part of it worth reading.
    ...(role === 'Administrator' && pendingAccess > 0
      ? [{ label: `Review access (${pendingAccess})`, icon: 'user-check', to: '/users?tab=requests' }]
      : []),
  ];

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  });

  return (
    <div style={PAGE}>
      {/* Greeting + the things you came here to do. */}
      <div style={{ ...cardStyle, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 11 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <h2 style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 16, lineHeight: 1.2, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
            {greeting()}, {firstName}
          </h2>
          <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>{today}</span>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {actions.map((a) => (
            <button
              key={a.label} type="button" onClick={() => navigate(a.to)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 8, height: 38, padding: '0 15px',
                border: `1px solid ${a.primary ? 'var(--blue-600)' : 'var(--border-strong)'}`,
                borderRadius: 'var(--radius-control)',
                background: a.primary ? 'var(--blue-600)' : 'var(--surface)',
                color: a.primary ? '#fff' : 'var(--text-body)',
                fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 13.5,
                cursor: 'pointer', whiteSpace: 'nowrap',
                boxShadow: a.primary ? 'var(--shadow-brand)' : 'none',
              }}
            >
              <Icon name={a.icon} size={18} />{a.label}
            </button>
          ))}
        </div>
      </div>

      {/* Care gaps. First, and bordered in red, because it is the only block
          on the page that is asking for a decision today. */}
      {gaps.length > 0 && (
        <div style={{ ...cardStyle, border: '1px solid var(--red-200)' }}>
          <div style={{ padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--red-50)', borderBottom: '1px solid var(--red-100)' }}>
            <IconChip icon="siren" tone="danger" style={{ background: 'var(--red-100)' }} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>
                {dueToday > 0
                  ? `${dueToday} case${dueToday === 1 ? '' : 's'} need${dueToday === 1 ? 's' : ''} a decision today`
                  : `${gaps.length} case${gaps.length === 1 ? '' : 's'} need${gaps.length === 1 ? 's' : ''} following up`}
              </span>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
                Deterministic care-gap rules over dates and case state &mdash; no model involved.
              </span>
            </span>
            {gaps.length > shownGaps.length && (
              <button
                type="button" onClick={() => navigate('/monitoring')}
                style={{ height: 32, padding: '0 13px', border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12.5, color: 'var(--text-body)', cursor: 'pointer', flex: 'none' }}
              >
                View all {gaps.length}
              </button>
            )}
          </div>
          {shownGaps.map((g, i) => {
            const meta = GAP_META[g.type] || { chip: 'Follow up', action: 'Open', to: null, tone: g.severity };
            const [chipBg, chipFg] = GAP_CHIP[meta.tone] || GAP_CHIP.info;
            return (
              <div
                key={`${g.child_id}-${g.type}-${i}`}
                style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderBottom: i < shownGaps.length - 1 ? '1px solid var(--divider-row)' : 'none' }}
              >
                <button
                  type="button" onClick={() => navigate(`/report/child/${g.child_id}`)}
                  style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 12, border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', padding: 0, fontFamily: 'var(--font-sans)' }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 11.5 }}>
                    {initialsOf(g.child_name)}
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: 'block', fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{g.child_name}</span>
                    <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text-body)' }}>{g.message}</span>
                  </span>
                </button>
                <span style={{ display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 10px', borderRadius: 'var(--radius-pill)', background: chipBg, color: chipFg, fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 11, flex: 'none' }}>
                  {meta.chip}
                </span>
                <span className="racco-mono" style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-faint)', flex: 'none', width: 56, textAlign: 'right' }}>
                  {caseRef(g.child_id)}
                </span>
                <button
                  type="button"
                  onClick={() => navigate(meta.to || `/report/child/${g.child_id}`)}
                  style={{ height: 30, padding: '0 12px', border: '1px solid var(--blue-200)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', color: 'var(--blue-700)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, cursor: 'pointer', flex: 'none' }}
                >
                  {meta.action}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Census. */}
      <div style={cardStyle}>
        <div style={{ padding: '12px 14px 0', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <div>
            <div className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)', marginBottom: 3 }}>Census</div>
            <h3 style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 16, lineHeight: 1.2, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>Intake vs. termination</h3>
          </div>
          <Segmented options={RANGES} value={range} onChange={setRange} label="Census range" />
        </div>
        <div style={{ padding: 14, display: 'flex', gap: 18, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 320px', minWidth: 0 }}>
            {trend.length === 0 ? (
              <p style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '40px 0' }}>No intake or termination recorded in this range.</p>
            ) : (
              <>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 150, borderBottom: '1px solid var(--border)', padding: '0 4px' }}>
                  {trend.map((b) => (
                    <div key={b.bucket} style={{ flex: 1, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', gap: 4, height: '100%' }}>
                      <div title={`${b.intake} intake`} style={{ width: 18, background: 'var(--blue-600)', borderRadius: '4px 4px 0 0', height: `${Math.round((b.intake / trendMax) * 100)}%` }} />
                      <div title={`${b.terminations} terminations`} style={{ width: 18, background: 'var(--amber-500)', borderRadius: '4px 4px 0 0', height: `${Math.round((b.terminations / trendMax) * 100)}%` }} />
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 10, padding: '5px 4px 0' }}>
                  {trend.map((b) => (
                    <span key={b.bucket} style={{ flex: 1, textAlign: 'center', fontWeight: 600, fontSize: 11, color: 'var(--text-muted)' }}>{b.bucket}</span>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 16, paddingTop: 10 }}>
                  {[['Intake', 'var(--blue-600)'], ['Terminations', 'var(--amber-500)']].map(([label, c]) => (
                    <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 11.5, color: 'var(--text-body)' }}>
                      <span style={{ width: 10, height: 10, borderRadius: 3, background: c }} />{label}
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
          <div style={{ flex: '0 1 210px', minWidth: 180, paddingLeft: 18, borderLeft: '1px solid var(--divider)', display: 'flex', flexDirection: 'column', gap: 9 }}>
            <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)' }}>Active case mix</span>
            {caseMix.length === 0
              ? <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No active cases.</span>
              : caseMix.map(([label, n]) => (
                <MiniBar key={label} label={label} value={n} pct={`${Math.round((n / mixMax) * 100)}%`} />
              ))}
          </div>
        </div>
      </div>

      {/* Pipeline. */}
      <div style={cardStyle}>
        <CardHead icon="hourglass" tone="warning" title="Pre-assessments waiting on someone" meta={`${stats.pending_pre_assessments ?? 0} open`} />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 1, background: 'var(--divider)' }}>
          {[
            { label: 'Not yet started', n: stats.unassessed ?? 0, hint: 'Active children with no completed pre-assessment', fg: 'var(--red-700)' },
            { label: 'In progress', n: stats.pending_pre_assessments ?? 0, hint: 'Started, waiting on interview or instrument titles', fg: 'var(--warning-700)' },
            { label: 'In counseling', n: (census.by_case_status || {}).counseling ?? 0, hint: 'Pre-assessment closed; the case moved on', fg: 'var(--blue-700)' },
          ].map((b) => (
            <div key={b.label} style={{ background: 'var(--surface)', padding: '13px 14px', display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 'var(--text-3xs)', letterSpacing: '0.08em', textTransform: 'uppercase', color: b.fg }}>{b.label}</span>
              <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 26, lineHeight: 1, color: 'var(--text-strong)', fontVariantNumeric: 'tabular-nums' }}>{b.n}</span>
              <span style={{ fontSize: 12, lineHeight: 1.45, color: 'var(--text-muted)' }}>{b.hint}</span>
            </div>
          ))}
        </div>
      </div>

      {/* The team. A psychologist has no business reading their colleagues'
          throughput, so this is the one block they do not get. */}
      {!isPsych && perPsych.length > 0 && (
        <div style={cardStyle}>
          <CardHead title="Clinical team this range" meta="completed sessions" />
          <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {perPsych.map((p) => (
              <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, borderRadius: '50%', flex: 'none', background: 'var(--ink-50)', color: 'var(--text-body)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 11 }}>
                  {initialsOf(p.name)}
                </span>
                <span style={{ width: 170, flex: 'none', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</span>
                <span style={{ flex: 1, minWidth: 40, height: 8, borderRadius: 'var(--radius-pill)', background: 'var(--divider)', overflow: 'hidden' }}>
                  <span style={{ display: 'block', height: '100%', borderRadius: 'var(--radius-pill)', background: 'var(--blue-600)', width: `${Math.round(((p.count ?? 0) / perPsychMax) * 100)}%` }} />
                </span>
                <span className="racco-mono" style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-strong)', width: 28, textAlign: 'right', flex: 'none' }}>{p.count ?? 0}</span>
                <span style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)', width: 96, textAlign: 'right', flex: 'none' }}>
                  {caseload[p.name] != null ? `${caseload[p.name]} active` : '—'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* No third column at this width, so the rail's three cards come back in
          here. They are the same components, not a second copy. */}
      {!layout.rightRailOn && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12, alignItems: 'start' }}>
          <RailCards />
        </div>
      )}
    </div>
  );
}
