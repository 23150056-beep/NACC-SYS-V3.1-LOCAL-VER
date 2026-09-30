import { Badge, Icon } from '../../ui';
import { clockRange, shortRange } from '../../utils/time';
import { localDate } from './shared';

/* "Availability — check before you assign": who to ask, read at a glance.
 *
 * It used to be a card per psychologist with every window as a chip -
 * "Mon 08:00-12:00 · Mon 13:00-17:00 · Tue 08:00-12:00 ..." wrapping over two
 * lines - so comparing two people's Wednesdays meant reading both paragraphs
 * (owner, 30 Sep 2026: "a bit messy ... I need to identify the schedules
 * without looking around"). Now it is a week: one row per psychologist, one
 * column per day, the same day always in the same place, their caseload
 * beside their name, and today's column marked.
 *
 * Each row is still a button that picks the psychologist, as the cards were.
 */
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const byStart = (a, b) => String(a.start_time).localeCompare(String(b.start_time));

function dayLabel(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

export default function PsychologistPicker({ psychologists, blocks, value, onPick, pendingId = null, holderId = null }) {
  const now = new Date();
  const today = (now.getDay() + 6) % 7;          // AvailabilityBlock counts Monday as 0
  const todayIso = localDate(now);
  const open = blocks.filter((b) => b.active !== false);
  const weekly = open.filter((b) => b.date == null && b.weekday != null);
  // Saturday and Sunday only when somebody works them: two empty columns on
  // every row are two more places to look for nothing.
  const days = [0, 1, 2, 3, 4].concat([5, 6].filter((d) => weekly.some((b) => b.weekday === d)));
  const cols = `minmax(150px, 1.5fr) 64px repeat(${days.length}, minmax(76px, 1fr))`;

  return (
    <div role="group" aria-labelledby="assign-psychologist-label" style={{ marginTop: 10, border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 12, background: 'var(--ink-50)', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div className="racco-eyebrow" style={{ fontSize: 10 }}>Availability — check before you assign</div>
        <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>Weekly hours. Click a psychologist to pick them.</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 'fit-content' }}>
          {/* Column heads, on the same grid as the rows below. */}
          <div aria-hidden="true" style={{ display: 'grid', gridTemplateColumns: cols, gap: 4, padding: '0 11px', fontSize: 10.5, fontWeight: 800, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
            <span>Psychologist</span>
            <span style={{ textAlign: 'center' }}>Cases</span>
            {days.map((d) => (
              <span key={d} style={{ textAlign: 'center', color: d === today ? 'var(--blue-700)' : undefined }}>
                {DAYS[d]}{d === today && <span style={{ display: 'block', fontSize: 9, letterSpacing: '0.04em' }}>Today</span>}
              </span>
            ))}
          </div>

          {psychologists.map((p) => {
            const mine = open.filter((b) => String(b.psychologist) === String(p.id));
            const week = weekly.filter((b) => String(b.psychologist) === String(p.id));
            const extra = mine.filter((b) => b.date != null && b.date >= todayIso)
              .sort((a, b) => String(a.date).localeCompare(String(b.date)) || byStart(a, b));
            const on = String(value) === String(p.id);
            const busy = p.caseload >= 5;
            return (
              <button
                type="button" key={p.id} aria-pressed={on} onClick={() => onPick(p.id)}
                style={{ display: 'grid', gridTemplateColumns: cols, gap: 4, alignItems: 'stretch', width: '100%', textAlign: 'left', padding: '9px 11px', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontFamily: 'var(--font-sans)', border: `1px solid ${on ? 'var(--blue-500)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : 'var(--surface)', boxShadow: on ? '0 0 0 1px var(--blue-500)' : 'none', transition: 'var(--transition-base)' }}
              >
                <span style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 3, minWidth: 0 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    {on && <Icon name="check-circle-2" size={15} style={{ color: 'var(--blue-600)', flex: 'none' }} />}
                    <span style={{ fontWeight: 700, fontSize: 13, color: on ? 'var(--blue-700)' : 'var(--text-strong)', overflowWrap: 'anywhere' }}>{p.name}</span>
                  </span>
                  {(String(pendingId) === String(p.id) || String(holderId) === String(p.id)) && (
                    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                      {String(pendingId) === String(p.id) && <Badge tone="amber" size="sm">Asked</Badge>}
                      {String(holderId) === String(p.id) && <Badge tone="success" size="sm">Has the case</Badge>}
                    </span>
                  )}
                </span>

                <span title={`${p.caseload} active case${p.caseload === 1 ? '' : 's'}${busy ? ' - a full caseload' : ''}`}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', borderRadius: 'var(--radius-sm)', background: busy ? 'var(--amber-50)' : 'var(--ink-50)', color: busy ? 'var(--amber-700)' : 'var(--text-strong)' }}>
                  <span style={{ fontWeight: 800, fontSize: 16, lineHeight: 1.1 }}>{p.caseload}</span>
                  <span style={{ fontSize: 10, fontWeight: 600, color: busy ? 'var(--amber-700)' : 'var(--text-muted)' }}>case{p.caseload === 1 ? '' : 's'}</span>
                </span>

                {mine.length === 0 ? (
                  <span style={{ gridColumn: `3 / span ${days.length}`, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 9px', borderRadius: 'var(--radius-sm)', background: 'var(--amber-50)', fontSize: 12, color: 'var(--amber-700)', fontWeight: 600 }}>
                    <Icon name="alert-triangle" size={13} /> No hours set yet — sessions can&apos;t be booked with them
                  </span>
                ) : days.map((d) => {
                  const that = week.filter((b) => b.weekday === d).sort(byStart);
                  return that.length ? (
                    <span key={d} title={that.map((b) => clockRange(b.start_time, b.end_time)).join(', ')}
                      style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: 1, padding: '5px 4px', borderRadius: 'var(--radius-sm)', background: 'var(--success-50)', color: 'var(--success-700)', fontSize: 11.5, fontWeight: 700, lineHeight: 1.35, textAlign: 'center', outline: d === today ? '1px solid var(--blue-200)' : 'none' }}>
                      {that.map((b) => <span key={b.id} style={{ whiteSpace: 'nowrap' }}>{shortRange(b.start_time, b.end_time)}</span>)}
                    </span>
                  ) : (
                    <span key={d} aria-label={`${DAYS[d]}: off`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 'var(--radius-sm)', color: 'var(--text-faint)', fontSize: 12, outline: d === today ? '1px solid var(--blue-200)' : 'none' }}>—</span>
                  );
                })}

                {/* One-off dates don't belong to a weekday column. */}
                {extra.length > 0 && (
                  <span style={{ gridColumn: '1 / -1', fontSize: 11.5, color: 'var(--text-body)', marginTop: 2 }}>
                    <strong style={{ fontWeight: 700 }}>Extra dates:</strong>{' '}
                    {extra.map((b) => `${dayLabel(b.date)}, ${shortRange(b.start_time, b.end_time)}`).join(' · ')}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
