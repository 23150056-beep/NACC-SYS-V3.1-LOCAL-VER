import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useLayout } from '../context/LayoutContext';
import { useCensus } from '../context/CensusContext';
import { useActivity } from '../context/ActivityContext';
import { Icon } from '../ui';
import { ACTION_META, eventDestination, eventText } from '../utils/activity';
import { PURPOSE_LABEL } from '../utils/child';
import { timeAgo } from '../utils/time';

/* Ambient context: the numbers, the day and the stream, in a third column.
 *
 * Contextual on purpose. It appears on the four screens where knowing "what
 * is the shape of the caseload right now" helps you read what is in front of
 * you, and it is absent from the table-heavy screens — Records, Monitoring,
 * Reports, Users — which need every pixel of width for their columns. Nothing
 * here is only here: the census is on the Dashboard, the day is on the
 * Calendar, the stream is behind the bell.
 */

const STATUS_TONE = {
  completed: { label: 'Done', tone: 'var(--success-500)', time: 'var(--text-faint)' },
  scheduled: { label: 'Next', tone: 'var(--blue-600)', time: 'var(--text-body)' },
  no_show: { label: 'No-show', tone: 'var(--warning-600)', time: 'var(--text-faint)' },
  cancelled: { label: 'Cancelled', tone: 'var(--text-faint)', time: 'var(--text-faint)' },
};

function RailCard({ icon, title, meta, children, footer }) {
  return (
    <div style={{ flex: 'none', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
      <div style={{ padding: '11px 13px', borderBottom: '1px solid var(--divider)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name={icon} size={17} style={{ color: 'var(--blue-600)' }} />
        <span style={{ flex: 1, fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 13, color: 'var(--text-strong)' }}>{title}</span>
        {meta}
      </div>
      {children}
      {footer}
    </div>
  );
}

/* Exported one by one as well as stacked.
 *
 * Below 1180px there is no third column, and the Dashboard lays these three
 * out along the bottom instead. Without that, a 1024px tablet lost today's
 * schedule and the activity stream from the app entirely — the rail is meant
 * to be contextual, not the only copy. */
export function RailCards() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { stats } = useCensus();
  const { events } = useActivity();

  const role = user?.role_name || 'Staff';
  const isPsych = role === 'Psychologist';
  const census = stats.census || {};
  const byStatus = census.by_case_status || {};
  const gaps = stats.care_gaps || [];
  const today = stats.today_schedule || [];
  const dueToday = gaps.filter((g) => g.severity === 'danger').length;

  /* Four numbers, and each one is a link to the screen that explains it —
   * a count you cannot act on is decoration. */
  const railStats = isPsych ? [
    { label: 'My active', value: census.active ?? 0, hint: 'children assigned', color: 'var(--success-700)', to: '/children' },
    { label: 'Counseling', value: byStatus.counseling ?? 0, hint: `${byStatus.pre_assessment ?? 0} pre-assessment`, color: 'var(--blue-600)', to: '/monitoring' },
    { label: 'Today', value: today.length, hint: 'appointments', color: 'var(--text-body)', to: '/schedule' },
    { label: 'Care gaps', value: gaps.length, hint: `${dueToday} need today`, color: 'var(--red-700)', to: '/' },
  ] : [
    { label: 'Active', value: census.active ?? 0, hint: 'children in care', color: 'var(--success-700)', to: '/children' },
    { label: 'Counseling', value: byStatus.counseling ?? 0, hint: `${byStatus.pre_assessment ?? 0} pre-assessment`, color: 'var(--blue-600)', to: '/monitoring' },
    { label: 'Archived', value: census.inactive ?? 0, hint: 'terminated cases', color: 'var(--text-body)', to: '/children' },
    { label: 'Care gaps', value: gaps.length, hint: `${dueToday} need today`, color: 'var(--red-700)', to: '/' },
  ];

  const availability = stats.availability_today || [];

  return (
    <>
      <RailCard icon="line-chart" title={isPsych ? 'My caseload' : 'Census at a glance'}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--divider)' }}>
          {railStats.map((s) => (
            <button
              key={s.label} type="button" onClick={() => navigate(s.to)}
              style={{ background: 'var(--surface)', padding: '11px 13px', display: 'flex', flexDirection: 'column', gap: 2, border: 'none', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ink-25)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--surface)'; }}
            >
              <span style={{ fontWeight: 800, fontSize: 'var(--text-3xs)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{s.label}</span>
              <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 24, lineHeight: 1.15, color: s.color, fontVariantNumeric: 'tabular-nums' }}>{s.value}</span>
              <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>{s.hint}</span>
            </button>
          ))}
        </div>
      </RailCard>

      <RailCard
        icon="calendar-check"
        title={isPsych ? 'My day' : 'Today'}
        meta={<span style={{ fontWeight: 600, fontSize: 11, color: 'var(--text-muted)' }}>{today.length} appt{today.length === 1 ? '' : 's'}</span>}
        footer={availability.length > 0 && (
          <div style={{ padding: '9px 13px', background: 'var(--ink-25)', display: 'flex', alignItems: 'flex-start', gap: 7 }}>
            <Icon name="clock" size={14} style={{ color: 'var(--success-500)', marginTop: 1 }} />
            <span style={{ fontWeight: 600, fontSize: 11, lineHeight: 1.5, color: 'var(--text-body)' }}>
              {availability.map((b) => `${b.psychologist} ${b.start}–${b.end}`).join(' · ')}
            </span>
          </div>
        )}
      >
        {today.length === 0 ? (
          <p style={{ padding: '16px 13px', fontSize: 12, color: 'var(--text-muted)' }}>Nothing booked today.</p>
        ) : today.map((a) => {
          const t = STATUS_TONE[a.status] || STATUS_TONE.scheduled;
          return (
            <button
              key={a.id} type="button" onClick={() => navigate('/schedule')}
              style={{ width: '100%', display: 'flex', gap: 11, padding: '9px 13px', borderBottom: '1px solid var(--divider-row)', border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--blue-50)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            >
              <span className="racco-mono" style={{ width: 44, flex: 'none', fontWeight: 600, fontSize: 12, color: t.time, paddingTop: 1 }}>{a.time}</span>
              <span style={{ flex: 1, minWidth: 0, borderLeft: `2px solid ${t.tone}`, paddingLeft: 10 }}>
                <span style={{ display: 'block', fontWeight: 700, fontSize: 12.5, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.child_name}</span>
                <span style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {PURPOSE_LABEL[a.purpose] || a.purpose}{!isPsych && a.psychologist ? ` · ${a.psychologist}` : ''}
                </span>
              </span>
              <span style={{ fontWeight: 700, fontSize: 9.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: t.tone, flex: 'none', paddingTop: 2 }}>{t.label}</span>
            </button>
          );
        })}
      </RailCard>

      <RailCard
        icon="history"
        title="Activity"
        meta={<span style={{ fontWeight: 700, fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--success-500)' }}>live</span>}
        footer={(
          <button
            type="button" onClick={() => navigate(role === 'Administrator' ? '/users' : '/')}
            style={{ width: '100%', padding: '9px 13px', textAlign: 'center', background: 'var(--ink-25)', border: 'none', borderTop: '1px solid var(--divider)', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, color: 'var(--blue-600)' }}
          >
            View full audit trail
          </button>
        )}
      >
        {events.length === 0 ? (
          <p style={{ padding: '16px 13px', fontSize: 12, color: 'var(--text-muted)' }}>Nothing has happened yet today.</p>
        ) : events.slice(0, 6).map((e, i) => {
          const meta = ACTION_META[e.action] || ACTION_META.created;
          return (
            <button
              key={e.id ?? i} type="button" onClick={() => navigate(eventDestination(e, role))}
              style={{ width: '100%', display: 'flex', gap: 10, padding: '9px 13px', borderBottom: '1px solid var(--divider-row)', border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
              onMouseEnter={(el) => { el.currentTarget.style.background = 'var(--blue-50)'; }}
              onMouseLeave={(el) => { el.currentTarget.style.background = 'transparent'; }}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24, borderRadius: 7, flex: 'none', background: meta.bg, color: meta.color }}>
                <Icon name={meta.icon} size={13} />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 600, fontSize: 12, lineHeight: 1.4, color: 'var(--text-strong)' }}>{eventText(e)}</span>
                <span style={{ display: 'block', fontSize: 10.5, color: 'var(--text-faint)', marginTop: 1 }}>{e.actor_label} · {timeAgo(e.created_at)}</span>
              </span>
            </button>
          );
        })}
      </RailCard>
    </>
  );
}

export default function RightRail() {
  const layout = useLayout();
  return (
    <aside
      className="racco-scroll"
      aria-label="At a glance"
      style={{ width: layout.rightRailWidth, flex: 'none', display: 'flex', flexDirection: 'column', gap: 12, overflowY: 'auto' }}
    >
      <RailCards />
    </aside>
  );
}
