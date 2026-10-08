/* One answer to "when was this?", for every screen that asks.
 *
 * There were three of these — in Topbar, Users and AccessRequests — copied
 * and then drifted apart. They disagreed on the things a reader actually
 * notices: one floored at "1 min ago" and one happily printed "0 min ago";
 * one wrote "2 days ago" and the others "2 d ago"; one said nothing at all
 * for something that happened seconds ago. The same event was described
 * differently depending on which screen you were looking at.
 *
 * The rules below are the union of what the three were reaching for:
 * a plain phrase while it is still recent, an exact date once "n days ago"
 * stops being easier to read than the date itself.
 */

const DATE = { day: 'numeric', month: 'short', year: 'numeric' };

/** "2:05 PM" — every time on screen is on the 12-hour clock.
 *
 * Built by hand rather than with toLocaleTimeString: the locale decides the
 * clock, so a browser set to a 24-hour region (en-GB, most of Europe) printed
 * 14:05 however the rest of the app wrote it, and even `hour12: true` there
 * comes back as "2:05 pm". Takes what the API sends — "14:05" or "14:05:00"
 * from a time field, an ISO timestamp from a datetime — or a Date.
 */
export function clock(value) {
  if (value == null || value === '') return '';
  let h;
  let m;
  const hhmm = typeof value === 'string' && /^(\d{1,2}):(\d{2})/.exec(value);
  if (hhmm) {
    h = Number(hhmm[1]);
    m = Number(hhmm[2]);
  } else {
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return '';
    h = d.getHours();
    m = d.getMinutes();
  }
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

/** "9:00 AM–12:00 PM". */
export function clockRange(start, end) {
  return `${clock(start)}–${clock(end)}`;
}

/** "8 AM–12 PM", "1–5 PM", "9:30–11 AM": a window as short as it reads, for
 *  a grid of them. Minutes only when not :00, and AM/PM once when both ends
 *  share it. Still the 12-hour clock; clockRange() is the long form. */
export function shortRange(start, end) {
  const part = (v) => {
    const m = /^(\d{1,2}):(\d{2})/.exec(String(v || ''));
    if (!m) return null;
    const h = Number(m[1]);
    const min = Number(m[2]);
    return { t: `${h % 12 || 12}${min ? `:${m[2]}` : ''}`, p: h < 12 ? 'AM' : 'PM' };
  };
  const a = part(start);
  const b = part(end);
  if (!a || !b) return clockRange(start, end);
  return a.p === b.p ? `${a.t}–${b.t} ${b.p}` : `${a.t} ${a.p}–${b.t} ${b.p}`;
}

/** "12 Aug 2026, 3:40 PM" — the full moment, for tooltips and fact rows. */
export function exactDate(iso) {
  if (!iso) return '';
  return `${shortDate(iso)}, ${clock(iso)}`;
}

/** "12 Aug 2026" — the day, without the time. */
export function shortDate(iso) {
  if (!iso) return '';
  // A date with no time ("2027-03-14") is a day, not a moment. new Date() reads
  // it as UTC midnight, which is the evening before in any zone west of UTC, so
  // it is built as a local day instead.
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso));
  const d = day ? new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3])) : new Date(iso);
  return d.toLocaleDateString(undefined, DATE);
}

/** True once a date with no time ("2027-03-14") is behind us. The day itself
 *  still counts: a license valid until the 14th is valid on the 14th. */
export function dayHasPassed(iso) {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!day) return false;
  const nextMidnight = new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3]) + 1);
  return new Date() >= nextMidnight;
}

/** "just now" · "5 min ago" · "3 hr ago" · "2 d ago" · "12 Aug 2026". */
export function timeAgo(iso) {
  if (!iso) return '—';
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 60) return 'just now';
  // Rounding alone produced "0 min ago" for anything under 30 seconds that
  // slipped past the check above (a clock skewed a little into the future).
  if (secs < 3600) return `${Math.max(1, Math.round(secs / 60))} min ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)} hr ago`;
  if (secs < 604800) return `${Math.round(secs / 86400)} d ago`;
  // Past a week, "9 d ago" makes the reader do arithmetic. Give them the date.
  return new Date(iso).toLocaleDateString(undefined, DATE);
}

/* Typing a time into a box of the 12-hour clock ("03:00", AM/PM beside it).
 *
 * The owner's rules, 30 Sep 2026: the colon is never typed, it is put in; the
 * hour cannot go past 12; after a two-digit hour (10, 11, 12) the next digit
 * starts the minutes; a space moves on after a one-digit hour, so "3 00" is
 * 03:00 and "12 30" is 12:30. A digit that cannot be part of the time - a 13th
 * hour, a 60th minute - is simply not taken. The whole box is read again on
 * every keystroke, so a backspace works on what is shown.
 */
export function typeTime(raw) {
  let hour = '';
  let minute = '';
  let inMinutes = false;
  for (const c of String(raw || '')) {
    if (c >= '0' && c <= '9') {
      if (!inMinutes && hour.length < 2) {
        if (hour === '') hour = c <= '1' ? c : `0${c}`;       // 2-9 can only be 02-09
        else if (hour === '0') { if (c !== '0') hour += c; }  // 01-09; 00 is no hour
        else if (c <= '2') hour += c;                         // 10, 11, 12
        continue;
      }
      inMinutes = true;                                       // hour full: this digit is a minute
      if (minute === '') { if (c <= '5') minute = c; }        // 00-59
      else if (minute.length === 1) minute += c;
    } else if (c === ' ' || c === ':' || c === '.') {
      if (!inMinutes && hour && hour !== '0') {
        hour = hour.padStart(2, '0');
        inMinutes = true;
      }
    }
  }
  return inMinutes ? `${hour}:${minute}` : hour;
}

/** What was typed, finished on leaving the box: "3" -> "03:00", "12:3" -> "12:03". */
export function finishTime(text) {
  const m = /^(\d{1,2})(?::(\d{0,2}))?$/.exec(text || '');
  if (!m || Number(m[1]) < 1) return text || '';
  return `${m[1].padStart(2, '0')}:${(m[2] || '').padStart(2, '0')}`;
}

/** "HH:MM" (24-hour) from the box and AM/PM, or '' while either is unfinished. */
export function timeValue(text, period) {
  const m = /^(\d{2}):(\d{2})$/.exec(text || '');
  if (!m || !period) return '';
  const h = Number(m[1]);
  if (h < 1 || h > 12 || Number(m[2]) > 59) return '';
  return `${String((h % 12) + (period === 'PM' ? 12 : 0)).padStart(2, '0')}:${m[2]}`;
}
