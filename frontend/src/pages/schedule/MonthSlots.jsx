import { useEffect, useMemo, useRef, useState } from 'react';
import { addMonths, format } from 'date-fns';
import { Alert, Button, Icon } from '../../ui';
import { isoOf, monthOf } from './monthSlots';

/* The month a booking is chosen from.

   The booking drawer used to offer a native date box and five "next working
   day" chips worked out from the weekly pattern, so a fully booked day still
   got a chip and nobody could see further than about a week. This asks the
   server for the whole month at once (GET /availability/month/, which runs the
   booking endpoint's own rule for every day), draws it as a calendar, and the
   Time grid below reads the chosen day's times from the same answer. */

const pad = (n) => String(n).padStart(2, '0');
const dateOf = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
// Reasons that are about THIS month: paging on may find an open day. The rest
// (no referral, nothing posted, a length no window fits) are the same next month.
const NEXT_HELPS = new Set(['away', 'full', 'none']);
const CELL = {
  position: 'relative', minWidth: 0, height: 50, padding: '4px 1px',
  borderRadius: 'var(--radius-control)', fontFamily: 'var(--font-sans)',
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 1,
};

function cellLook(state, chosen) {
  if (chosen) return { background: 'var(--blue-600)', border: '1px solid var(--blue-600)', color: '#fff', cursor: 'pointer' };
  if (state === 'open') return { background: 'var(--blue-50)', border: '1px solid var(--blue-200)', color: 'var(--text-strong)', cursor: 'pointer' };
  if (state === 'leave') return { background: 'var(--amber-50)', border: '1px solid var(--amber-100)', color: 'var(--text-body)', cursor: 'not-allowed' };
  if (state === 'full') return { background: 'var(--ink-50)', border: '1px solid var(--border)', color: 'var(--text-muted)', cursor: 'not-allowed' };
  return { background: 'transparent', border: '1px solid transparent', color: 'var(--text-faint)', cursor: 'default' };
}

/* What the cell shows, as text. The accessible name starts with exactly this
   so a voice-control user who says what they see ("14 open") matches it. */
function visibleText(date, day, blocked) {
  const n = Number(date.slice(8));
  if (day?.state === 'open') return `${n} ${day.slots.length} open`;
  if (day?.state === 'full' && !blocked) return `${n} Full`;
  if (day?.state === 'leave') return `${n} Away`;
  return `${n}`;
}

/* Why a cell cannot be chosen, in words. The visible text already says what
   an open or full day is, so the accessible name only adds what it does not. */
function whyNot(day, blocked) {
  return {
    full: blocked ? 'a case referral is needed first' : 'full, no open times',
    leave: 'away on leave',
    off: 'not working',
    past: 'already passed',
  }[day?.state] || 'checking';
}

function describe(date, day, today, blocked) {
  const name = format(dateOf(date), 'EEEE, MMMM d');
  let tail = '';
  if (day?.state === 'full' && !blocked) tail = '';          // "Full" is the visible text
  else if (day?.state === 'leave') tail = 'on leave';
  else if (day?.state !== 'open') tail = whyNot(day, blocked);
  return `${visibleText(date, day, blocked)}, ${name}${date === today ? ', today' : ''}${tail ? `, ${tail}` : ''}`;
}

/* The hover text of a cell that cannot be chosen. */
function hoverText(date, day, today, blocked) {
  const why = whyNot(day, blocked);
  return `${format(dateOf(date), 'EEEE, MMMM d')}${date === today ? ' (today)' : ''}: ${why.charAt(0).toUpperCase()}${why.slice(1)}`;
}

export default function BookingMonth({ month, first, last, onMonth, data, busy, error, onRetry, date, onPick }) {
  // The server's date when it has answered: the agency's clock, not the browser's.
  const today = data?.today || isoOf(new Date());
  const blocked = !!data?.blocked;
  const [y, m] = month.split('-').map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const lead = new Date(y, m - 1, 1).getDay();            // Sunday first, like the Calendar screen
  const byDate = useMemo(() => new Map((data?.days || []).map((d) => [d.date, d])), [data]);
  const cells = useMemo(
    () => Array.from({ length: daysInMonth }, (_, i) => `${month}-${pad(i + 1)}`),
    [month, daysInMonth],
  );
  const firstOpen = (data?.days || []).find((d) => d.state === 'open')?.date;

  const [focusDate, setFocusDate] = useState(null);
  const refs = useRef(new Map());
  const pendingFocus = useRef(null);
  const tabDate = (focusDate && monthOf(focusDate) === month && focusDate)
    || (date && monthOf(date) === month && date)
    || firstOpen
    || (monthOf(today) === month ? today : cells[0]);

  // A key that crosses into another month pages first and focuses afterwards.
  useEffect(() => {
    const want = pendingFocus.current;
    if (want && monthOf(want) === month) {
      pendingFocus.current = null;
      setFocusDate(want);
      refs.current.get(want)?.focus();
    }
  }, [month]);

  const canPage = (delta) => {
    const target = monthOf(isoOf(addMonths(new Date(y, m - 1, 1), delta)));
    return target >= first && target <= last ? target : null;
  };
  const page = (delta) => { const t = canPage(delta); if (t) onMonth(t); };

  const move = (from, to) => {
    const target = isoOf(to);
    if (monthOf(target) === month) {
      setFocusDate(target);
      refs.current.get(target)?.focus();
      return;
    }
    const tm = monthOf(target);
    if (tm < first || tm > last) return;
    pendingFocus.current = target;
    onMonth(tm);
  };

  const onKeyDown = (e, iso) => {
    const d = dateOf(iso);
    const step = (n) => { const t = new Date(d); t.setDate(t.getDate() + n); return t; };
    let to = null;
    if (e.key === 'ArrowLeft') to = step(-1);
    else if (e.key === 'ArrowRight') to = step(1);
    else if (e.key === 'ArrowUp') to = step(-7);
    else if (e.key === 'ArrowDown') to = step(7);
    else if (e.key === 'Home') to = step(-d.getDay());
    else if (e.key === 'End') to = step(6 - d.getDay());
    else if (e.key === 'PageUp' || e.key === 'PageDown') {
      e.preventDefault();
      const delta = e.key === 'PageUp' ? -1 : 1;
      const t = canPage(delta);
      if (!t) return;
      const [ty, tmn] = t.split('-').map(Number);
      const last_ = new Date(ty, tmn, 0).getDate();
      pendingFocus.current = `${t}-${pad(Math.min(d.getDate(), last_))}`;
      onMonth(t);
      return;
    }
    if (!to) return;
    e.preventDefault();
    move(iso, to);
  };

  const nextAvailable = canPage(1);
  const noneOpen = data && !firstOpen;
  const heading = format(new Date(y, m - 1, 1), 'MMMM yyyy');
  const navBtn = (disabled) => ({
    width: 32, height: 32, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    borderRadius: 'var(--radius-control)', border: '1px solid var(--border)', background: 'var(--surface)',
    color: 'var(--text-strong)', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.45 : 1, padding: 0,
  });

  return (
    <div className="racco-month">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
        <button
          type="button" className="racco-btn" aria-label="Previous month" style={navBtn(!canPage(-1))}
          aria-disabled={!canPage(-1) || undefined} onClick={() => page(-1)}
        >
          <Icon name="chevron-left" size={16} />
        </button>
        <div aria-live="polite" style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 14.5, color: 'var(--text-strong)' }}>
          {heading}
        </div>
        <button
          type="button" className="racco-btn" aria-label="Next month" style={navBtn(!nextAvailable)}
          aria-disabled={!nextAvailable || undefined} onClick={() => page(1)}
        >
          <Icon name="chevron-right" size={16} />
        </button>
      </div>

      {noneOpen && (
        <Alert tone="warning" icon={<Icon name="calendar" size={17} />} style={{ marginBottom: 10 }}>
          <div>{data.reason || `No open time in ${heading}.`}</div>
          {nextAvailable && NEXT_HELPS.has(data.reason_kind) && (
            <div style={{ marginTop: 8 }}>
              <Button type="button" variant="secondary" size="sm" onClick={() => page(1)}>Next month</Button>
            </div>
          )}
        </Alert>
      )}
      <div className="racco-month-grid" role="group" aria-label={heading} aria-busy={busy || undefined}>
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((w) => (
          <div key={w} aria-hidden="true" style={{ textAlign: 'center', fontSize: 10.5, fontWeight: 700, color: 'var(--text-muted)', paddingBottom: 2 }}>{w}</div>
        ))}
        {Array.from({ length: lead }, (_, i) => <div key={`lead-${i}`} aria-hidden="true" />)}
        {cells.map((iso) => {
          const day = byDate.get(iso);
          const state = day?.state;
          const open = state === 'open';
          const chosen = open && date === iso;
          const n = day?.slots?.length || 0;
          return (
            <button
              key={iso} type="button"
              ref={(el) => { if (el) refs.current.set(iso, el); else refs.current.delete(iso); }}
              className="racco-month-cell racco-btn"
              tabIndex={iso === tabDate ? 0 : -1}
              aria-label={describe(iso, day, today, blocked)}
              title={state === 'open' ? undefined : hoverText(iso, day, today, blocked)}
              aria-disabled={!open || undefined}
              aria-pressed={open ? chosen : undefined}
              aria-current={iso === today ? 'date' : undefined}
              onFocus={() => setFocusDate(iso)}
              onKeyDown={(e) => onKeyDown(e, iso)}
              onClick={() => { if (open) onPick(iso); }}
              style={{ ...CELL, ...cellLook(state, chosen) }}
            >
              <span style={{
                fontSize: 13.5, fontWeight: 700, lineHeight: 1.1,
                textDecoration: iso === today ? 'underline' : 'none', textUnderlineOffset: 3,
              }}>{Number(iso.slice(8))}</span>
              {open && (
                <span className="racco-month-count" style={{ fontSize: 10, fontWeight: 700, lineHeight: 1.1, color: chosen ? '#fff' : 'var(--blue-700)' }}>
                  {' '}{n}<span className="racco-month-word"> open</span>
                </span>
              )}
              {state === 'full' && !blocked && <span style={{ fontSize: 10, fontWeight: 700, lineHeight: 1.1 }}>{' '}Full</span>}
              {state === 'leave' && <span style={{ fontSize: 10, fontWeight: 700, lineHeight: 1.1, color: 'var(--amber-700)' }}>{' '}Away</span>}
            </button>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 8, fontSize: 11.5, color: 'var(--text-muted)' }} aria-hidden="true">
        {[['var(--blue-50)', 'var(--blue-300)', 'Open'], ['var(--ink-50)', 'var(--ink-400)', 'Full'], ['var(--amber-50)', 'var(--amber-400)', 'Away']].map(([bg, bd, label]) => (
          <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 12, height: 12, borderRadius: 3, background: bg, border: `1px solid ${bd}` }} />{label}
          </span>
        ))}
      </div>

      {/* Always in the page, so the words put into it are announced. */}
      <div role="status" style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: busy ? 8 : 0, minHeight: busy ? undefined : 0 }}>
        {busy ? `Checking ${heading}…` : ''}
      </div>
      {error && (
        <Alert tone="danger" icon={<Icon name="alert-triangle" size={17} />} style={{ marginTop: 10 }}>
          {error}{' '}
          <button type="button" onClick={onRetry} style={{ background: 'none', border: 0, padding: 0, color: 'var(--blue-700)', fontWeight: 700, cursor: 'pointer', textDecoration: 'underline', font: 'inherit' }}>Try again</button>
        </Alert>
      )}
    </div>
  );
}
