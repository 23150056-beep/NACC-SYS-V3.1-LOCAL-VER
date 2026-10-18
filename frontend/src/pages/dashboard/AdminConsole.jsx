import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../../api/client';
import { testEmailDelivery } from '../../api/email';
import { checkSmsGateway } from '../../api/sms';
import { useAuth } from '../../context/AuthContext';
import { useCensus } from '../../context/CensusContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useLayout } from '../../context/LayoutContext';
import { Avatar, Button, Icon, IconChip, ROLE_META, Skeleton } from '../../ui';
import { clock, timeAgo } from '../../utils/time';
import ConsoleActions from './ConsoleActions';

/* The ISA's admin console, at the top of their Dashboard.
 *
 * The ISA is the agency's IT support, not the Head of Office, so the top of
 * the page answers three questions in this order: who is waiting on me, is
 * anything broken, and who has access to what. The figures, the team, the care
 * gaps and the census stay exactly as they were underneath.
 *
 * Everything on it comes from ONE request, GET /api/admin/overview/
 * (backend accounts/admin_overview.py), made when the console opens and again
 * on Refresh. The only other calls are the three Test buttons, each of which
 * asks for something an administrator can already ask for in Settings, and the
 * footer's actions (ConsoleActions.jsx), which are ones an administrator already
 * had on the Users, Records and Settings screens.
 * Children appear by case number and name only where a queue has to say which
 * record to open; no case note, report, case study or custodian detail is in
 * the response, so none can be on this screen.
 *
 * Only an administrator's Dashboard mounts this, so nobody else ever sends the
 * request. CardHead and cardStyle are copies of the ones in Dashboard.jsx,
 * which does not export them (and importing from it would be circular).
 */

/* ------------------------------ shared bits ------------------------------ */

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

function SubHead({ children, meta }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
      <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)' }}>{children}</span>
      {meta && <span style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>{meta}</span>}
    </div>
  );
}

const linkBtn = {
  border: 'none', background: 'transparent', padding: 0, cursor: 'pointer',
  fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, color: 'var(--blue-700)',
  display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap',
};

const plural = (n, one, many) => (n === 1 ? one : many);

// A line that says what the good case looks like, so an empty list reads as
// good news and not as a section that failed to load.
function Quiet({ children }) {
  return <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-muted)', padding: '2px 0' }}>{children}</div>;
}

// A day sent as 2026-10-15 is a day, not a moment: new Date() would read it as
// UTC midnight, the evening before in any zone west of Greenwich.
function parseDay(value) {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
  return day ? new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3])) : new Date(value);
}

const monthDay = (d) => d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const daysBetween = (a, b) => Math.round((startOfDay(b) - startOfDay(a)) / 86400000);

/** "Today 3:12 PM", "Yesterday 9:40 AM", "Tue 11:48 PM", "Oct 9, 2:15 PM". */
function whenLabel(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const ago = daysBetween(d, new Date());
  if (ago <= 0) return `Today ${clock(d)}`;
  if (ago === 1) return `Yesterday ${clock(d)}`;
  if (ago < 7) return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${clock(d)}`;
  return `${monthDay(d)}, ${clock(d)}`;
}

/** "today", "yesterday", "Oct 13". */
function dayWord(iso) {
  const d = new Date(iso);
  const ago = daysBetween(d, new Date());
  if (ago <= 0) return 'today';
  if (ago === 1) return 'yesterday';
  return monthDay(d);
}

// Ticks the component so "Last checked 3 min ago" keeps counting without
// anybody touching the page.
function useTick(ms) {
  const [, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((n) => n + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
}

/* ------------------------------ 1. header ------------------------------ */

function ConsoleHeader({ checkedAt, busy, failed, onRefresh }) {
  useTick(30000);
  // What a screen reader is told. The visible "Last checked N min ago" counts
  // up on its own, so it is not a live region (every minute would be spoken);
  // this changes only when a load starts or finishes. A refresh that failed is
  // spoken by the alert below, and a first load that failed by the error card.
  const spoken = busy ? 'Checking the console.'
    : checkedAt && !failed ? `The console was checked at ${clock(new Date(checkedAt))}.` : '';
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', padding: '4px 2px 0' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
        <Icon name="shield-check" size={15} style={{ color: 'var(--blue-600)' }} />
        <h2 className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)', color: 'var(--blue-700)' }}>Admin console</h2>
      </span>
      <span role="status" className="racco-sr-only">{spoken}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>
        {busy && !checkedAt ? 'Checking…' : checkedAt ? `Last checked ${timeAgo(checkedAt)}` : 'Not checked yet'}
        <span aria-hidden="true">·</span>
        {/* Not `disabled` while it loads: a disabled button loses the keyboard
            focus, and the person who pressed Enter on it is left at the top of
            the page. It stays focusable and says it is busy; the click is
            ignored (as the Test buttons do). */}
        <button type="button" onClick={busy ? undefined : onRefresh} aria-disabled={busy || undefined} aria-label="Refresh the console" style={{ ...linkBtn, cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.7 : 1 }}>
          <Icon name="refresh-cw" size={13} style={busy ? { animation: 'racco-spin 0.8s linear infinite' } : undefined} />
          Refresh
        </button>
      </span>
      {failed && checkedAt && (
        <span role="alert" style={{ flexBasis: '100%', fontWeight: 600, fontSize: 12, color: 'var(--red-700)' }}>
          Could not refresh, so this is what was loaded {timeAgo(checkedAt)}.
        </span>
      )}
    </div>
  );
}

/* ----------------------------- 2. needs you ----------------------------- */

// What each queue is called and drawn as. Singular for one, plural otherwise;
// the numbers, the hint under them and the rows all come from the server.
const QUEUES = {
  access_requests: { icon: 'user-check', label: ['Access request', 'Access requests'], open: 'Open the queue' },
  lockouts: { icon: 'lock', label: ['Sign-in lockout', 'Sign-in lockouts'] },
  temp_passwords: { icon: 'key-round', label: ['Temp password unchanged', 'Temp passwords unchanged'] },
  stuck_requests: { icon: 'hourglass', label: ['Psychologist request stuck', 'Psychologist requests stuck'], open: 'Open Records' },
  no_social_worker: { icon: 'user-x', label: ['No social worker yet', 'No social worker yet'], open: 'Open Records' },
  archived_holder: { icon: 'archive', label: ['Held by an archived account', 'Held by an archived account'], open: 'Open Records' },
  names_to_fix: { icon: 'type', label: ['Name to fix', 'Names to fix'] },
  duplicates: { icon: 'copy', label: ['Possible duplicate', 'Possible duplicates'], open: 'Open Records' },
};

// Two queues, in the order an ISA should work them. The first is people who
// cannot do their job until the ISA acts, so it is the only one drawn in amber;
// the second is records to tidy, neutral with a number, and a zero stays quiet.
//
// The rows are laid out here, four tiles each, by queue key, not by the
// server's `group`: that puts "stuck requests" with the records, and the
// picture the owner approved has it with the people, four tiles to a row.
const GROUPS = [
  { key: 'people', label: 'People waiting on you', urgent: true, keys: ['access_requests', 'lockouts', 'temp_passwords', 'stuck_requests'] },
  { key: 'records', label: 'Records to tidy', urgent: false, keys: ['no_social_worker', 'archived_holder', 'names_to_fix', 'duplicates'] },
];

function QueueLabel({ children, meta }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, padding: '7px 14px 6px', background: 'var(--ink-25)' }}>
      <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)' }}>{children}</span>
      <span style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>{meta}</span>
    </div>
  );
}

function Tile({ queue, urgent, tight, open, onToggle, panelId }) {
  const navigate = useNavigate();
  const [hover, setHover] = useState(false);
  const meta = QUEUES[queue.key];
  const live = queue.count > 0;
  const expandable = live && queue.items.length > 0;
  // Amber is for the people-blocked queue only. The tidy-up queue keeps a
  // neutral chip, and a zero is drawn faint so it reads as "nothing to do".
  const action = urgent && live;
  const lines = tight ? 2 : 1; // phones: reserve two lines so hints line up across a row
  const label = meta.label[queue.count === 1 ? 0 : 1];
  const interactive = expandable || (live && !!queue.link);
  const body = (
    <>
      <span style={{ display: 'flex', alignItems: 'center', gap: 10, marginRight: 4 }}>
        <IconChip icon={meta.icon} tone={action ? 'warning' : 'neutral'} size={30} style={live ? undefined : { color: 'var(--text-faint)' }} />
        <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 30, lineHeight: 1, color: live ? 'var(--text-strong)' : 'var(--text-faint)', fontVariantNumeric: 'tabular-nums' }}>{queue.count}</span>
        <span style={{ flex: 1 }} />
        {interactive && (
          <Icon name={open ? 'chevron-down' : 'chevron-right'} size={17} style={{ color: hover || open ? 'var(--blue-600)' : 'var(--ink-400)', transition: 'transform var(--dur-fast) var(--ease-out)' }} />
        )}
      </span>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <span style={{ fontWeight: 800, fontSize: 13, lineHeight: '17px', minHeight: 17 * lines, color: live ? 'var(--text-strong)' : 'var(--text-muted)' }}>{label}</span>
        <span style={{ fontSize: 11.5, lineHeight: '16px', minHeight: 16 * lines, color: 'var(--text-muted)', overflowWrap: 'break-word', wordBreak: 'normal' }}>{queue.hint}</span>
      </span>
    </>
  );
  // The amber bar is a border, not a box-shadow: an inline shadow would also
  // beat the focus ring (.racco-queue-tile:focus-visible), and a tile that
  // shows no focus cannot be used from the keyboard. The padding gives the
  // 3px back so the text does not move.
  const shell = {
    background: hover || open ? 'var(--ink-25)' : 'var(--surface)', border: 'none', textAlign: 'left',
    borderLeft: action ? '3px solid var(--amber-400)' : 'none',
    padding: action ? '12px 8px 12px 11px' : '12px 8px 12px 14px',
    display: 'flex', flexDirection: 'column', gap: 7, minWidth: 0,
    fontFamily: 'var(--font-sans)', width: '100%',
  };
  if (!interactive) {
    return <div style={shell}>{body}</div>;
  }
  return (
    <button
      type="button" className="racco-queue-tile" id={`console-tile-${queue.key}`}
      onClick={() => (expandable ? onToggle() : navigate(queue.link))}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      aria-expanded={expandable ? open : undefined}
      aria-controls={expandable ? panelId : undefined}
      aria-label={`${queue.count} ${label}. ${queue.hint}`}
      style={{ ...shell, cursor: 'pointer' }}
    >
      {body}
    </button>
  );
}

// The rows behind one tile, opened in place: directly under the row of tiles
// that holds it (two to a row on a narrow pane, so that is not always under the
// whole group), and so right after it in the tab order. Each row opens what it names; the
// queue's own link, where one screen handles the whole queue, sits beside the
// title. A row whose link is the queue's own (every access request opens the
// same list) has no button of its own: a button per row would promise to open
// that person and open the list.
function QueuePanel({ queue, id }) {
  const navigate = useNavigate();
  const meta = QUEUES[queue.key];
  const title = meta.label[queue.count === 1 ? 0 : 1];
  // A grid item across every column: the grid's 1px gap is the rule above it.
  return (
    <div id={id} style={{ gridColumn: '1 / -1', background: 'var(--ink-25)', padding: '8px 14px 10px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10, marginBottom: 4 }}>
        <span style={{ fontWeight: 800, fontSize: 12.5, color: 'var(--text-strong)' }}>{title}</span>
        {queue.link && (
          <button type="button" onClick={() => navigate(queue.link)} style={linkBtn}>
            {meta.open || 'Open'}<Icon name="chevron-right" size={13} />
          </button>
        )}
      </div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {queue.items.map((item, i) => (
          <li key={`${item.label}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '7px 0', borderTop: i ? '1px solid var(--divider-row)' : 'none' }}>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontWeight: 700, fontSize: 12.5, lineHeight: 1.35, color: 'var(--text-strong)', overflowWrap: 'anywhere' }}>{item.label}</span>
              {item.sub && <span style={{ display: 'block', fontSize: 11.5, lineHeight: 1.4, color: 'var(--text-muted)' }}>{item.sub}</span>}
            </span>
            {item.link && item.link !== queue.link && (
              // The sub-line is in the name because two rows can share a label
              // (the same address locked out twice), and "Open <address>" twice
              // over cannot be told apart by a screen reader.
              <button type="button" onClick={() => navigate(item.link)} aria-label={item.sub ? `Open ${item.label}, ${item.sub}` : `Open ${item.label}`} style={{ ...linkBtn, flex: 'none' }}>
                Open<Icon name="chevron-right" size={13} />
              </button>
            )}
          </li>
        ))}
      </ul>
      {queue.count > queue.items.length && (
        <div style={{ borderTop: '1px solid var(--divider-row)', paddingTop: 7, fontSize: 11.5, color: 'var(--text-muted)' }}>
          Showing the first {queue.items.length} of {queue.count}.
        </div>
      )}
    </div>
  );
}

function TileSkeletons({ cols }) {
  return (
    <div aria-hidden="true">
      {GROUPS.map((g, gi) => (
        <div key={g.key} style={{ borderTop: gi ? '1px solid var(--divider)' : 'none' }}>
          <QueueLabel meta="">{g.label}</QueueLabel>
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 1, background: 'var(--divider)', borderTop: '1px solid var(--divider)' }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} style={{ background: 'var(--surface)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 9 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Skeleton width={30} height={30} radius="var(--radius-control)" />
                  <Skeleton width={28} height={26} />
                </span>
                <Skeleton width="70%" height={13} />
                <Skeleton width="90%" height={11} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// A refusal (403) is an answer, not a fault: asking again gets the same one, so
// the message stands alone. Any other failure may pass, so it offers a retry.
function ConsoleError({ message, onRetry, busy, canRetry }) {
  return (
    <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '14px' }}>
      <Icon name="alert-triangle" size={17} style={{ color: 'var(--red-700)' }} />
      <span style={{ flex: 1, minWidth: 180, fontSize: 13, lineHeight: 1.5, color: 'var(--text-body)' }}>{message}</span>
      {canRetry && (
        <Button
          size="sm" variant="secondary" onClick={busy ? undefined : onRetry} aria-disabled={busy || undefined}
          style={busy ? { cursor: 'default', opacity: 0.6 } : undefined}
          iconLeft={<Icon name="refresh-cw" size={14} style={busy ? { animation: 'racco-spin 0.8s linear infinite' } : undefined} />}
        >
          Try again
        </Button>
      )}
    </div>
  );
}

function NeedsYouNow({ needs, error, canRetry, busy, onRetry, accessCount, assistant, onChanged, cols, narrow, tight }) {
  const [openKey, setOpenKey] = useState(null);
  // The list that is open is forgotten once its queue has nothing to show (it
  // emptied, or a refresh cut its rows). Left remembered, it hides while the
  // queue is empty and then opens by itself the next time the queue fills.
  useEffect(() => {
    if (!openKey || !needs) return;
    const queue = needs.find((q) => q.key === openKey);
    if (!queue || queue.count === 0 || queue.items.length === 0) setOpenKey(null);
  }, [needs, openKey]);
  // Escape closes the list that is open and puts the focus back on its tile.
  const closeOnEscape = (e) => {
    if (e.key !== 'Escape' || !openKey) return;
    e.preventDefault();
    const tile = document.getElementById(`console-tile-${openKey}`);
    setOpenKey(null);
    tile?.focus();
  };
  const all = needs || [];
  const waiting = all.reduce((sum, q) => sum + q.count, 0);
  const queues = all.filter((q) => q.count > 0).length;
  const meta = !needs ? '' : waiting === 0 ? 'All clear' : `${waiting} ${plural(waiting, 'item', 'items')} in ${queues} ${plural(queues, 'queue', 'queues')}`;

  return (
    <div style={cardStyle}>
      <CardHead icon="bell-ring" tone="warning" title="Needs you now" meta={meta} />
      {needs ? GROUPS.map((g, gi) => {
        const queuesHere = g.keys.map((k) => all.find((q) => q.key === k)).filter(Boolean);
        const items = queuesHere.reduce((sum, q) => sum + q.count, 0);
        const opened = queuesHere.find((q) => q.key === openKey && q.count > 0 && q.items.length > 0);
        // The panel goes in after the last tile of the row the opened tile is in.
        const openedRow = opened ? Math.floor(queuesHere.indexOf(opened) / cols) : -1;
        return (
          <div key={g.key} onKeyDown={closeOnEscape} style={{ borderTop: gi ? '1px solid var(--divider)' : 'none' }}>
            <QueueLabel meta={`${items} ${plural(items, 'item', 'items')}`}>{g.label}</QueueLabel>
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 1, background: 'var(--divider)', borderTop: '1px solid var(--divider)' }}>
              {queuesHere.flatMap((q, i) => {
                const tile = (
                  <Tile
                    key={q.key} queue={q} urgent={g.urgent} tight={tight}
                    open={opened?.key === q.key} panelId={`console-queue-${q.key}`}
                    onToggle={() => setOpenKey((k) => (k === q.key ? null : q.key))}
                  />
                );
                const endsOpenedRow = opened && Math.floor(i / cols) === openedRow
                  && ((i + 1) % cols === 0 || i === queuesHere.length - 1);
                return endsOpenedRow
                  ? [tile, <QueuePanel key={`panel-${opened.key}`} queue={opened} id={`console-queue-${opened.key}`} />]
                  : [tile];
              })}
            </div>
          </div>
        );
      }) : error ? (
        <ConsoleError message={error} onRetry={onRetry} busy={busy} canRetry={canRetry} />
      ) : (
        <>
          <TileSkeletons cols={cols} />
          <span role="status" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Loading the admin console…</span>
        </>
      )}
      {/* The admin actions sit directly under the queues they act on, as the
          card's footer band (ConsoleActions.jsx). Not while the first answer is
          awaited: the placeholder tiles are shorter than the real ones, so the
          band would slide tens of pixels down under the pointer as they arrived,
          and a press aimed at one button would land on another thing. It is
          drawn once there are tiles, and when the load failed - that is exactly
          when "Add user" and "Settings" are still wanted. */}
      {(needs || error) && (
        <ConsoleActions accessCount={accessCount} narrow={narrow} assistant={assistant} onChanged={onChanged} />
      )}
    </div>
  );
}

/* ----------------------------- 3a. system health ----------------------------- */

// A colour on its own is not a status, so each is also named in words beside
// the title, and the dot carries the same words for a screen reader.
const DOT = {
  ok: ['var(--success-500)', 'var(--success-50)', 'Healthy', 'var(--success-700)'],
  warn: ['var(--warning-500)', 'var(--warning-50)', 'Needs a look', 'var(--warning-700)'],
  bad: ['var(--red-500)', 'var(--red-50)', 'Broken', 'var(--red-700)'],
  off: ['var(--ink-400)', 'var(--ink-50)', 'Not set up', 'var(--text-muted)'],
};

function StatusDot({ status }) {
  const [c, halo] = DOT[status] || DOT.off;
  return <span aria-hidden="true" style={{ width: 9, height: 9, borderRadius: '50%', flex: 'none', marginTop: 5, background: c, boxShadow: `0 0 0 3px ${halo}` }} />;
}

// The three Test buttons. Each asks for something an administrator can already
// ask for in Settings, and none of the checks spends a text or an email except
// the email one, which really sends and so asks first.
const withTimeout = (promise, ms) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('timeout')), ms);
  promise.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
});

// The assistant check puts the runtime's own error in `detail`, and for a
// server that is not running that is a Python exception
// ("<urlopen error [Errno 111] Connection refused>"). A sentence the ISA can act
// on replaces it; a message from a hosted service ("HTTP 410 ...") is kept,
// because the number is what they would look up.
const RAW_ERROR = /<urlopen|\[Errno|Errno \d+|Traceback|Exception\b/;
function modelFault(d) {
  const said = d.detail || '';
  if (said && !RAW_ERROR.test(said)) return said;
  return d.provider === 'hosted'
    ? 'The hosted model did not answer. Check its address and key in the server settings.'
    : 'The model server did not answer. Check that it is running on the server computer.';
}

const TESTS = {
  sms: {
    seconds: 30,
    call: () => checkSmsGateway(),
    read: (d, status) => (d.ok
      ? { tone: 'ok', title: 'Passed', detail: d.detail }
      : { tone: status === 'off' ? 'neutral' : 'bad', title: status === 'off' ? 'Not set up' : 'Failed', detail: d.detail }),
  },
  email: {
    seconds: 45,
    call: () => testEmailDelivery(),
    read: (d, status) => (d.ok
      ? { tone: 'ok', title: 'Sent', detail: d.detail }
      : { tone: status === 'off' ? 'neutral' : 'bad', title: status === 'off' ? 'Not set up' : 'Failed', detail: d.detail }),
  },
  assistant: {
    seconds: 60,
    call: () => api.get('/assistant/model-health/').then((r) => r.data),
    read: (d) => {
      if (d.reachable) {
        return { tone: 'ok', title: 'Passed', detail: `The ${d.provider === 'hosted' ? 'hosted' : 'local'} model${d.model ? ` ${d.model}` : ''} answered.` };
      }
      if (d.provider === 'off') return { tone: 'neutral', title: 'Switched off', detail: d.detail || 'The assistant is switched off in Settings.' };
      return { tone: 'bad', title: 'Failed', detail: modelFault(d) };
    },
  },
};

const sentenceFor = (err, seconds) => {
  if (err?.message === 'timeout') return `No answer after ${seconds} seconds.`;
  if (err?.response?.status === 403) return 'Only the ISA can run this check.';
  const said = err?.response?.data?.detail;
  if (typeof said === 'string' && said) return said;
  // The title above already says the test could not run; this says why.
  if (err?.response) return `The server answered with an error (${err.response.status}).`;
  return 'Could not reach the server.';
};

const RESULT_TONE = {
  ok: { icon: 'check-circle', color: 'var(--success-700)' },
  neutral: { icon: 'info', color: 'var(--text-muted)' },
  bad: { icon: 'alert-triangle', color: 'var(--red-700)' },
};

// Not `disabled` while it runs: a disabled button loses the keyboard focus, and
// the person who pressed Enter on it is left at the top of the page. It stays
// focusable and says it is busy; the click is ignored.
function TestButton({ title, running, onRun }) {
  return (
    <button
      type="button" onClick={running ? undefined : onRun} aria-disabled={running || undefined}
      aria-label={running ? `Testing ${title}` : `Test ${title}`}
      style={{
        height: 26, padding: '0 11px', flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 5,
        border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-sm)',
        background: 'var(--surface)', color: 'var(--text-body)',
        fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, cursor: running ? 'default' : 'pointer',
        minWidth: 62, justifyContent: 'center', opacity: running ? 0.75 : 1,
      }}
    >
      {running && <Icon name="refresh-cw" size={12} style={{ animation: 'racco-spin 0.8s linear infinite' }} />}
      {running ? 'Testing' : 'Test'}
    </button>
  );
}

function HealthRow({ item, status, last, detail, extra, onTest }) {
  const { user } = useAuth();
  const confirm = useConfirm();
  const [result, setResult] = useState(null);
  const running = result?.phase === 'running';
  const test = item.test ? TESTS[item.test] : null;
  const [, , word, wordColor] = DOT[status] || DOT.off;

  const run = async () => {
    if (!test || running) return;
    if (item.test === 'email') {
      // It really sends, so it asks first and says where it goes.
      const yes = await confirm({
        title: `Send a test email to ${user?.email || 'your address'}?`,
        description: 'This sends a real email to your own address and shows what the mail service replied.',
        confirmLabel: 'Yes, send it',
      });
      if (!yes) return;
    }
    setResult({ phase: 'running' });
    onTest?.(item.key, false);
    try {
      const answer = await withTimeout(test.call(), test.seconds * 1000);
      const outcome = test.read(answer, item.status);
      setResult({ phase: 'done', at: new Date(), ...outcome });
      // A test that ran and failed is evidence the row is not healthy. One that
      // could not run (below) proves nothing either way.
      onTest?.(item.key, outcome.tone === 'bad');
    } catch (err) {
      setResult({ phase: 'done', at: new Date(), tone: 'bad', title: 'Could not run the test', detail: sentenceFor(err, test.seconds) });
    }
  };

  const tone = result?.phase === 'done' ? RESULT_TONE[result.tone] : null;
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', padding: '9px 14px', borderBottom: last ? 'none' : '1px solid var(--divider-row)' }}>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <StatusDot status={status} />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 800, fontSize: 13, lineHeight: 1.3, color: 'var(--text-strong)' }}>{item.title}</span>
            <span style={{ fontWeight: 700, fontSize: 11, color: wordColor }}>{word}</span>
          </span>
          <span style={{ display: 'block', fontSize: 12, lineHeight: 1.45, color: 'var(--text-muted)', marginTop: 1, overflowWrap: 'anywhere' }}>{detail}</span>
          {extra}
          <span aria-live="polite" style={{ display: 'block' }}>
            {tone && (
              <span style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 5, fontSize: 12, lineHeight: 1.45, color: 'var(--text-body)' }}>
                <Icon name={tone.icon} size={14} style={{ color: tone.color, marginTop: 2 }} />
                <span style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                  <b style={{ fontWeight: 800, color: tone.color }}>{result.title}</b>
                  <span style={{ color: 'var(--text-muted)' }}> at {clock(result.at)}</span>
                  {result.detail ? <> &mdash; {result.detail}</> : null}
                </span>
              </span>
            )}
          </span>
        </span>
        {test && <span style={{ flex: 'none', alignSelf: 'flex-start' }}><TestButton title={item.title} running={running} onRun={run} /></span>}
      </div>
    </div>
  );
}

function upSince(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return daysBetween(d, new Date()) <= 0 ? clock(d) : `${monthDay(d)}, ${clock(d)}`;
}

const knownCommit = (c) => !!c && c !== 'unknown';
const mono = { fontFamily: 'var(--font-mono, monospace)', fontSize: 11.5 };

function SystemHealth({ health, version }) {
  // Rows whose Test button has just reported a failure. The server's own word
  // for the row cannot see that (it never calls the gateway or the model), so
  // until the test is run again a failed row reads "Needs a look" and the card's
  // chip counts it, instead of "Healthy" and "All good" above a red result.
  const [failedTests, setFailedTests] = useState({});
  const noteTest = useCallback((key, failed) => {
    setFailedTests((now) => (!!now[key] === failed ? now : { ...now, [key]: failed }));
  }, []);
  const web = import.meta.env.VITE_WEB_COMMIT;
  const apiShort = version?.api_commit ? version.api_commit.slice(0, 7) : null;
  // Two services deploy independently off one push, so for a few minutes the
  // API can be on the new commit and the web still on the old one. Said only
  // when both are known and differ: an unknown commit proves nothing.
  const differ = knownCommit(apiShort) && knownCommit(web) && apiShort !== String(web).slice(0, 7);
  const rows = health.map((h) => {
    if (h.key !== 'version') {
      return { item: h, status: failedTests[h.key] && h.status === 'ok' ? 'warn' : h.status, detail: h.detail };
    }
    const since = version?.api_started_at ? upSince(version.api_started_at) : '';
    return {
      item: h,
      status: differ && h.status === 'ok' ? 'warn' : h.status,
      detail: (
        <>
          API <span style={mono}>{apiShort || 'unknown'}</span> &middot; Web <span style={mono}>{knownCommit(web) ? web : 'unknown'}</span>
          {since ? <> &middot; up since {since}</> : null}
        </>
      ),
      extra: differ ? (
        <span style={{ display: 'block', fontSize: 12, lineHeight: 1.45, fontWeight: 800, color: 'var(--warning-700)', marginTop: 1 }}>
          The two halves differ - one is still deploying
        </span>
      ) : null,
    };
  });
  const bad = rows.filter((r) => r.status === 'bad').length;
  const flagged = rows.filter((r) => r.status === 'warn' || r.status === 'bad').length;
  const chip = bad ? ['var(--red-50)', 'var(--red-700)'] : flagged ? ['var(--warning-50)', 'var(--warning-700)'] : ['var(--success-50)', 'var(--success-700)'];
  return (
    <div style={{ ...cardStyle, display: 'flex', flexDirection: 'column' }}>
      <CardHead icon="heart-pulse" tone={bad ? 'danger' : flagged ? 'warning' : 'success'} title="System health">
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 22, padding: '0 10px', borderRadius: 'var(--radius-pill)', background: chip[0], color: chip[1], fontWeight: 800, fontSize: 11.5 }}>
          <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
          {flagged ? `${flagged} to check` : 'All good'}
        </span>
      </CardHead>
      {/* The rows share whatever height People & access leaves them, so the
          card stays level with its neighbour without a blank band. */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        {rows.map((r, i) => (
          <HealthRow key={r.item.key} item={r.item} status={r.status} detail={r.detail} extra={r.extra} onTest={noteTest} last={i === rows.length - 1} />
        ))}
      </div>
    </div>
  );
}

/* ----------------------------- 3b. people & access ----------------------------- */

const SHOWN = 5;

function PersonLine({ person, tone, sub, right }) {
  const navigate = useNavigate();
  const avatarTone = tone || (person.role === 'Staff' ? 'amber' : 'brand');
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0', minWidth: 0 }}>
      <Avatar name={person.name} size={26} tone={avatarTone} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <button
          type="button" onClick={() => navigate(`/users?edit=${person.id}`)} title={person.name}
          style={{ display: 'block', maxWidth: '100%', border: 'none', background: 'transparent', padding: 0, textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12.5, lineHeight: 1.3, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {person.name}
        </button>
        {sub && <span style={{ display: 'block', fontSize: 11.5, lineHeight: 1.35, color: 'var(--text-muted)' }}>{sub}</span>}
      </span>
      {right}
    </div>
  );
}

function OpenLink({ person }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(`/users?edit=${person.id}`)} aria-label={`Open ${person.name}'s account`} style={{ ...linkBtn, flex: 'none' }}>
      Open<Icon name="chevron-right" size={13} />
    </button>
  );
}

// A list that shows its first few and opens in place for the rest.
function Folded({ rows, render, label }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, SHOWN);
  return (
    <>
      {shown.map(render)}
      {rows.length > SHOWN && (
        <button type="button" onClick={() => setAll((v) => !v)} aria-expanded={all} style={{ ...linkBtn, marginTop: 4 }}>
          {all ? 'Show fewer' : `Show all ${rows.length} ${label}`}
        </button>
      )}
    </>
  );
}

const ROLE_CHIP = {
  Administrator: ['ISA', 'ISA'],
  Staff: ['Social worker', 'Social workers'],
  Psychologist: ['Psychologist', 'Psychologists'],
};

function PeopleAccess({ people }) {
  const roles = Object.entries(people.roles || {});
  const total = roles.reduce((s, [, n]) => s + n, 0);
  const section = { padding: '11px 14px', borderTop: '1px solid var(--divider)' };
  const daysQuiet = (iso) => Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86400000));
  return (
    <div style={cardStyle}>
      <CardHead icon="users" tone="brand" title="People & access" meta={`${total} active ${plural(total, 'account', 'accounts')}`} />
      <div style={{ padding: '11px 14px', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {roles.map(([role, n]) => (
          <span key={role} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 11px', background: 'var(--ink-50)', border: '1px solid var(--border)', borderRadius: 'var(--radius-pill)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, lineHeight: 1, whiteSpace: 'nowrap', color: 'var(--text-body)' }}>
            <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: '50%', background: ROLE_META[role]?.color || 'var(--ink-400)', flex: 'none' }} />
            <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 13.5, color: 'var(--text-strong)' }}>{n}</span>
            {(ROLE_CHIP[role] || [role, role])[n === 1 ? 0 : 1]}
          </span>
        ))}
      </div>

      <div style={section}>
        <SubHead meta={`${people.signed_in_today.length} ${plural(people.signed_in_today.length, 'person', 'people')}`}>Signed in today</SubHead>
        {people.signed_in_today.length === 0 ? <Quiet>Nobody has signed in yet today.</Quiet> : (
          <Folded
            rows={people.signed_in_today} label="people"
            render={(p) => (
              <PersonLine
                key={p.id} person={p}
                right={<span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums', flex: 'none' }}>{clock(p.at)}</span>}
              />
            )}
          />
        )}
      </div>

      <div style={section}>
        <SubHead meta="no sign-in for 30+ days">Dormant</SubHead>
        {people.dormant.length === 0 ? <Quiet>Everyone has signed in within the last 30 days.</Quiet> : (
          <Folded
            rows={people.dormant} label="accounts"
            render={(p) => (
              <PersonLine
                key={p.id} person={p} tone="neutral"
                sub={p.last_sign_in
                  ? `Last sign-in ${monthDay(new Date(p.last_sign_in))} · ${daysQuiet(p.last_sign_in)} days ago`
                  : 'Has never signed in'}
                right={<OpenLink person={p} />}
              />
            )}
          />
        )}
      </div>

      <div style={section}>
        <SubHead meta={`${people.temp_passwords.length} ${plural(people.temp_passwords.length, 'account', 'accounts')}`}>Temporary password not changed</SubHead>
        {people.temp_passwords.length === 0 ? <Quiet>Everyone has chosen their own password.</Quiet> : (
          <Folded
            rows={people.temp_passwords} label="accounts"
            render={(p) => (
              <PersonLine
                key={p.id} person={p}
                sub={p.since ? `Issued ${dayWord(p.since)} · password not changed yet` : 'Issue date not recorded · password not changed yet'}
                right={<OpenLink person={p} />}
              />
            )}
          />
        )}
      </div>
    </div>
  );
}

/* ----------------------------- 4a. workload ----------------------------- */

const AMBER_FROM = 5; // the rail's rule for a psychologist: amber from 5 children

// Fill colours: a solid for children held, the same hue paler for requests
// still waiting on an answer. Amber when the caseload reaches AMBER_FROM.
const FILL = {
  blue: ['var(--blue-600)', 'var(--blue-200)'],
  amber: ['var(--amber-500)', 'var(--amber-200)'],
};

// `suffix` follows the name and is never cut: when a long name has to be
// shortened it is the name that gives way, not "(inactive)".
function LoadRow({ label, suffix, value, parts, scale, track, note }) {
  const total = parts.reduce((sum, x) => sum + x.n, 0);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }} title={note}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12.5, color: 'var(--text-body)' }}>
        <span style={{ minWidth: 0, display: 'flex', alignItems: 'baseline', whiteSpace: 'nowrap' }}>
          <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
          {suffix ? <span style={{ flex: 'none' }}>{suffix}</span> : null}
        </span>
        <span className="racco-mono" style={{ color: 'var(--text-strong)', flex: 'none' }}>{value}</span>
      </div>
      <div role="img" aria-label={note} style={{ height: 6, borderRadius: 'var(--radius-pill)', background: track || 'var(--divider)', overflow: 'hidden' }}>
        <div style={{ display: 'flex', height: '100%', width: `${Math.min(100, (total / scale) * 100)}%`, borderRadius: 'var(--radius-pill)', overflow: 'hidden' }}>
          {parts.map((x, i) => x.n > 0 && <div key={i} style={{ flex: `${x.n} 0 0`, background: x.color }} />)}
        </div>
      </div>
    </div>
  );
}

function KeyItem({ swatch, children }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span aria-hidden="true" style={{ width: 14, height: 6, borderRadius: 3, background: swatch, flex: 'none' }} />
      {children}
    </span>
  );
}

function Strip({ icon, tone, children }) {
  const warn = tone === 'warning';
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '9px 14px', background: warn ? 'var(--warning-50)' : 'var(--ink-25)', borderTop: `1px solid ${warn ? 'var(--warning-100)' : 'var(--divider)'}` }}>
      <Icon name={icon} size={15} style={{ color: warn ? 'var(--warning-600)' : 'var(--text-faint)', marginTop: 1, flex: 'none' }} />
      <span style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text-body)' }}>{children}</span>
    </div>
  );
}

const joinNames = (names) => (names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

function leaveRange(start, end) {
  const a = parseDay(start);
  const b = parseDay(end);
  return a.getTime() === b.getTime() ? monthDay(a) : `${monthDay(a)}–${monthDay(b)}`;
}

function Workload({ workload, noSocialWorker, twoCols }) {
  const noHours = new Set(workload.no_availability.map((p) => p.id));
  const held = workload.psychologists.reduce((sum, p) => sum + p.active, 0) + workload.unassigned;
  const biggest = Math.max(0, ...workload.psychologists.map((p) => p.active + p.pending), ...workload.social_workers.map((s) => s.active), workload.unassigned, noSocialWorker);
  // One scale for BOTH columns, so a bar of 9 and a bar of 17 are drawn at
  // their real proportion.
  const scale = Math.max(20, biggest);
  const leave = workload.on_leave;
  return (
    <div style={{ ...cardStyle, display: 'flex', flexDirection: 'column' }}>
      <CardHead icon="gauge" tone="brand" title="Workload" meta={`${held} ${plural(held, 'child', 'children')} in care`} />
      <div style={{ flex: 1, padding: '12px 14px', display: 'grid', gridTemplateColumns: twoCols ? 'minmax(0, 1fr) minmax(0, 1fr)' : 'minmax(0, 1fr)', gap: twoCols ? 18 : 16, alignContent: 'start' }}>
        <div>
          <SubHead>Psychologists</SubHead>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9, minWidth: 0 }}>
            {workload.psychologists.length === 0 && <Quiet>No active psychologists.</Quiet>}
            {workload.psychologists.map((p) => {
              const [solid, pale] = FILL[p.active >= AMBER_FROM || p.inactive_account ? 'amber' : 'blue'];
              return (
                <LoadRow
                  key={p.id}
                  label={p.name}
                  suffix={p.inactive_account ? '\u00a0(inactive)' : noHours.has(p.id)
                    ? <Icon name="calendar-x" size={12} style={{ color: 'var(--warning-700)', marginLeft: 5, verticalAlign: '-1px' }} />
                    : null}
                  value={p.pending ? <>{p.active}<span style={{ color: 'var(--text-muted)' }}> +{p.pending}</span></> : p.active}
                  parts={[{ n: p.active, color: solid }, { n: p.pending, color: pale }]}
                  track={p.inactive_account ? 'var(--amber-100)' : undefined}
                  scale={scale}
                  note={p.inactive_account
                    ? `${p.name}: ${p.active} ${plural(p.active, 'child', 'children')} with no active holder`
                    : `${p.name}: ${p.active} ${plural(p.active, 'child', 'children')}${p.pending ? `, ${p.pending} ${plural(p.pending, 'request', 'requests')} waiting for an answer` : ''}${noHours.has(p.id) ? ', no availability posted' : ''}`}
                />
              );
            })}
            {workload.unassigned > 0 && (
              <LoadRow
                label="No psychologist yet" value={workload.unassigned} scale={scale}
                parts={[{ n: workload.unassigned, color: FILL.amber[0] }]} track="var(--amber-100)"
                note={`${workload.unassigned} active ${plural(workload.unassigned, 'child', 'children')} with no psychologist`}
              />
            )}
          </div>
        </div>
        <div>
          <SubHead>Social workers</SubHead>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9, minWidth: 0 }}>
            {workload.social_workers.length === 0 && noSocialWorker === 0 && <Quiet>No social workers.</Quiet>}
            {workload.social_workers.map((s) => (
              <LoadRow
                key={s.id} label={s.name} suffix={s.inactive_account ? '\u00a0(inactive)' : null} value={s.active} scale={scale}
                parts={[{ n: s.active, color: s.inactive_account ? FILL.amber[0] : FILL.blue[0] }]}
                track={s.inactive_account ? 'var(--amber-100)' : undefined}
                note={s.inactive_account ? `${s.name}: ${s.active} ${plural(s.active, 'child', 'children')} with no active holder` : `${s.name}: ${s.active} ${plural(s.active, 'child', 'children')}`}
              />
            ))}
            {noSocialWorker > 0 && (
              <LoadRow
                label="No social worker yet" value={noSocialWorker} scale={scale}
                parts={[{ n: noSocialWorker, color: FILL.amber[0] }]} track="var(--amber-100)"
                note={`${noSocialWorker} active ${plural(noSocialWorker, 'child', 'children')} with no social worker`}
              />
            )}
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', padding: '8px 14px', borderTop: '1px solid var(--divider)', background: 'var(--ink-25)', fontSize: 11.5, lineHeight: 1.4, fontWeight: 600, color: 'var(--text-muted)' }}>
        <KeyItem swatch={FILL.blue[0]}>Children held</KeyItem>
        <KeyItem swatch={FILL.blue[1]}>Asked, not yet answered</KeyItem>
        <KeyItem swatch={FILL.amber[0]}>{AMBER_FROM}+ children or no holder</KeyItem>
      </div>
      {workload.no_availability.length > 0 && (
        <Strip icon="alert-triangle" tone="warning">
          <b style={{ fontWeight: 800 }}>{workload.no_availability.length} {plural(workload.no_availability.length, 'psychologist has', 'psychologists have')} no availability left</b>
          {' '}&mdash; can&rsquo;t be booked ({joinNames(workload.no_availability.map((p) => p.name))}).
        </Strip>
      )}
      {leave.length > 0 && (
        <Strip icon="calendar-off">
          On leave this week:{' '}
          {leave.map((l, i) => (
            <Fragment key={`${l.id}-${l.start}`}>
              {i > 0 && ', '}
              <b style={{ fontWeight: 800 }}>{l.name}</b> ({leaveRange(l.start, l.end)})
            </Fragment>
          ))}.
        </Strip>
      )}
      {workload.no_availability.length === 0 && leave.length === 0 && (
        <Strip icon="check-circle">Every psychologist has availability posted, and nobody is on leave this week.</Strip>
      )}
    </div>
  );
}

/* ----------------------------- 4b. security log ----------------------------- */

const EVENT_TONE = {
  warn: ['var(--warning-50)', 'var(--warning-700)'],
  brand: ['var(--blue-50)', 'var(--blue-600)'],
  ok: ['var(--success-50)', 'var(--success-700)'],
  neutral: ['var(--ink-50)', 'var(--text-body)'],
};

// [icon, tone] per kind of event the server names.
const EVENT_KIND = {
  lockout: ['lock', 'warn'],
  password: ['key-round', 'neutral'],
  reset: ['key-round', 'brand'],
  role: ['user-cog', 'brand'],
  approval: ['user-check', 'ok'],
  decline: ['user-x', 'neutral'],
  archive: ['user-x', 'warn'],
  reactivate: ['user-check', 'ok'],
  created: ['user-plus', 'ok'],
  google: ['badge-check', 'neutral'],
  request: ['inbox', 'brand'],
  export: ['file-down', 'neutral'],
  assistant_read: ['bot', 'neutral'],
  other: ['circle-dot', 'neutral'],
};

const LOG_SHOWN = 8;

function SecurityLog({ events, more }) {
  const { user } = useAuth();
  const [all, setAll] = useState(false);
  const shown = all ? events : events.slice(0, LOG_SHOWN);
  return (
    <div style={{ ...cardStyle, display: 'flex', flexDirection: 'column' }}>
      <CardHead icon="scroll-text" tone="neutral" title="Security log" meta="last 7 days" />
      <div style={{ flex: 1 }}>
        {events.length === 0 && (
          <div style={{ padding: '12px 14px' }}><Quiet>Nothing to report in the last 7 days.</Quiet></div>
        )}
        {shown.map((e, i) => {
          const [icon, tone] = EVENT_KIND[e.kind] || EVENT_KIND.other;
          const [bg, fg] = EVENT_TONE[tone];
          // Some sentences already begin with the person (an assistant read);
          // say who only where the sentence does not.
          const names = e.actor && e.text.startsWith(e.actor);
          const by = names || !e.actor || e.actor === 'System' ? '' : ` · by ${e.actor === user?.fullname ? 'you' : e.actor}`;
          return (
            <div key={`${e.at}-${i}`} style={{ display: 'flex', alignItems: 'flex-start', gap: 11, padding: '9px 14px', borderBottom: i < shown.length - 1 ? '1px solid var(--divider-row)' : 'none' }}>
              <span aria-hidden="true" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26, borderRadius: 7, flex: 'none', background: bg, color: fg }}>
                <Icon name={icon} size={14} />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 12.5, lineHeight: 1.4, color: 'var(--text-body)', overflowWrap: 'anywhere' }}>
                  {names ? <><b style={{ fontWeight: 800, color: 'var(--text-strong)' }}>{e.actor}</b>{e.text.slice(e.actor.length)}</> : e.text}
                </span>
                <span style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginTop: 1 }}>{whenLabel(e.at)}{by}</span>
              </span>
            </div>
          );
        })}
        {/* Kinds the log cuts short (a flood of exports, say) say how many
            lines they left out, so a quiet log is not mistaken for a complete one. */}
        {more.map((m) => (
          <div key={m.kind} style={{ padding: '8px 14px', borderTop: '1px solid var(--divider-row)', fontSize: 11.5, color: 'var(--text-muted)' }}>
            &hellip; {m.text}
          </div>
        ))}
      </div>
      {events.length > LOG_SHOWN && (
        <button
          type="button" onClick={() => setAll((v) => !v)} aria-expanded={all}
          style={{ width: '100%', padding: '9px 14px', textAlign: 'center', background: 'var(--ink-25)', border: 'none', borderTop: '1px solid var(--divider)', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, color: 'var(--blue-700)' }}
        >
          {all ? 'Show fewer' : `Show all ${events.length} events`}
        </button>
      )}
    </div>
  );
}

/* ------------------------------- loading ------------------------------- */

// What the four cards under "Needs you now" will be, so the page below them
// (the figures, the team, the care gaps) does not start out at the top of the
// screen and then jump a screenful down when the answer lands. The heights are
// what the cards measure with a normal day's data, not a promise.
function CardSkeleton({ rows, minHeight }) {
  return (
    <div style={{ ...cardStyle, minHeight }}>
      <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--divider)' }}>
        <Skeleton width={30} height={30} radius="var(--radius-control)" />
        <Skeleton width={130} height={14} />
      </div>
      <div style={{ padding: '14px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            <Skeleton width={`${34 + ((i * 11) % 26)}%`} height={12} />
            <Skeleton width={`${62 + ((i * 7) % 30)}%`} height={11} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------- the console ------------------------------- */

export default function AdminConsole() {
  const { paneWidth } = useLayout();
  const { pendingAccess } = useCensus();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  // Whether the last failure was a refusal, which asking again cannot change.
  const [refused, setRefused] = useState(false);
  const [busy, setBusy] = useState(true);
  const [checkedAt, setCheckedAt] = useState(null);
  // A reply that arrives after a newer request, or after the console is gone,
  // is dropped rather than written over what is on screen. Every call takes the
  // next number, and only the call that still holds the latest one may write.
  const latest = useRef(0);
  // One request at a time. Strict Mode mounts, unmounts and mounts again in
  // development, which would otherwise ask twice for the same answer.
  const inFlight = useRef(null);

  // A plain Refresh joins a request that is already out. A read asked for after
  // a write (`fresh`: the footer's transfer and assistant switch) must not: that
  // request left before the write and may carry the old picture, so it sends its
  // own, and the older one's reply is dropped when it arrives, whichever of the
  // two comes back first.
  const load = useCallback(async ({ fresh = false } = {}) => {
    const mine = ++latest.current;
    setBusy(true);
    try {
      if (fresh || !inFlight.current) {
        const sent = api.get('/admin/overview/').finally(() => {
          // Only the request that is still the current one clears the slot; an
          // older one settling late must not hide a newer one still out.
          if (inFlight.current === sent) inFlight.current = null;
        });
        inFlight.current = sent;
      }
      const res = await inFlight.current;
      if (mine !== latest.current) return;
      setData(res.data);
      setCheckedAt(Date.now());
      setError('');
      setRefused(false);
    } catch (err) {
      if (mine !== latest.current) return;
      const denied = err.response?.status === 403;
      setRefused(denied);
      setError(denied
        ? 'Only the ISA can open the admin console.'
        : 'The admin console could not be loaded. Check your connection and try again.');
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }, []);

  // The three ways to ask are named so no button hands its click event to
  // `load` as if it were the options.
  const refresh = useCallback(() => load(), [load]);
  const refreshAfterWrite = useCallback(() => load({ fresh: true }), [load]);

  useEffect(() => {
    load();
    return () => { latest.current += 1; };
  }, [load]);

  // Everything follows the pane the Dashboard actually has, not the window: the
  // left rail and the right rail both take width, and a media query cannot see
  // that. (Pane is 854 px in a 1440 window, 818 in 1100, 778 in 1366, 692 in
  // 1280 - the right rail comes in at 1180 - 772 in 820, 342 in 390.)
  //  - Four tiles to a row from 680, two down to 320, one below that. Each group
  //    holds four tiles, so every one of those fills its rows. A tile is still
  //    about 170 px wide at 680, enough for its label and a three-line hint.
  //  - The card pairs sit side by side from 740 and stack below it: narrower
  //    than that, two cards leave each one a title that wraps.
  const tileCols = paneWidth >= 680 ? 4 : paneWidth >= 320 ? 2 : 1;
  const tight = tileCols === 2 && paneWidth < 520;
  const sideBySide = paneWidth >= 740;
  const cardWidth = sideBySide ? (paneWidth - 12) / 2 : paneWidth;
  const pair = { display: 'grid', gridTemplateColumns: sideBySide ? 'repeat(2, minmax(0, 1fr))' : 'minmax(0, 1fr)', gap: 12, alignItems: 'stretch' };

  const accessCount = data?.needs.find((q) => q.key === 'access_requests')?.count ?? pendingAccess ?? 0;
  const noSocialWorker = data?.needs.find((q) => q.key === 'no_social_worker')?.count ?? 0;
  // The assistant's own row in System health: the footer's switch reads it.
  const assistant = data?.health.find((h) => h.key === 'assistant') || null;

  return (
    <section aria-label="Admin console" style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0, position: 'relative' }}>
      <ConsoleHeader checkedAt={checkedAt} busy={busy} failed={!!error && !!data} onRefresh={refresh} />
      <NeedsYouNow
        needs={data?.needs} error={data ? '' : error} canRetry={!refused} busy={busy} onRetry={refresh}
        accessCount={accessCount} assistant={assistant} onChanged={refreshAfterWrite}
        cols={tileCols} narrow={paneWidth < 560} tight={tight}
      />
      {data ? (
        <>
          <div style={pair}>
            <SystemHealth health={data.health} version={data.version} />
            <PeopleAccess people={data.people} />
          </div>
          <div style={pair}>
            <Workload workload={data.workload} noSocialWorker={noSocialWorker} twoCols={cardWidth >= 370} />
            <SecurityLog events={data.security_log} more={data.security_log_more || []} />
          </div>
        </>
      ) : !error && (
        <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={pair}>
            <CardSkeleton rows={5} minHeight={383} />
            <CardSkeleton rows={5} minHeight={383} />
          </div>
          <div style={pair}>
            <CardSkeleton rows={5} minHeight={383} />
            <CardSkeleton rows={5} minHeight={383} />
          </div>
        </div>
      )}
    </section>
  );
}
