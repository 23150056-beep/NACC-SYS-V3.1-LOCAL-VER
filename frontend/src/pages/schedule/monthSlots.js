import { useEffect, useState } from 'react';
import { addMonths } from 'date-fns';
import api from '../../api/client';
import { firstError } from '../../utils/errors';

const pad = (n) => String(n).padStart(2, '0');
// Local date, never toISOString(): that is UTC and gives yesterday east of Greenwich.
export const isoOf = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const monthOf = (iso) => iso.slice(0, 7);

const DAY_ABBR = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export const spokenDays = (weekdays) => {
  if (!weekdays.length) return '';
  const run = weekdays.every((d, i) => i === 0 || d === weekdays[i - 1] + 1);
  return run && weekdays.length > 2
    ? `${DAY_ABBR[weekdays[0]]}–${DAY_ABBR[weekdays[weekdays.length - 1]]}`
    : weekdays.map((d) => DAY_ABBR[d]).join(', ');
};

/* The server's window: this month up to twelve months ahead. */
export function monthBounds(today) {
  // The server's date when it has told us (the agency's clock), else the browser's.
  const now = today ? new Date(Number(today.slice(0, 4)), Number(today.slice(5, 7)) - 1, 1) : new Date();
  return { first: monthOf(isoOf(now)), last: monthOf(isoOf(addMonths(now, 12))) };
}

/* Asks for the month and keeps which month is on show. The month follows the
   chosen day (so moving an appointment opens on its month) until somebody pages
   to another one. A stale answer is never shown: the answer carries the key it
   was asked for and a different key reads as "still loading". */
export function useMonthSlots({ psychologist, child, duration, exclude, date, open }) {
  const [picked, setPicked] = useState(null);
  const [state, setState] = useState({ key: '', data: null, error: '' });
  const [tick, setTick] = useState(0);
  const [serverToday, setServerToday] = useState('');
  const { first, last } = monthBounds(serverToday);
  let month = picked || (date ? monthOf(date) : first);
  if (month < first) month = first;
  if (month > last) month = last;
  const key = `${psychologist}|${child || ''}|${duration}|${exclude || ''}|${month}`;
  const asking = open && !!psychologist;

  useEffect(() => { if (!open) setPicked(null); }, [open]);
  // Remember the month the drawer opened on. Without it, a day that turns out
  // not to be open is cleared and the month falls back to the current one.
  useEffect(() => { if (open && date && !picked) setPicked(monthOf(date)); }, [open, date, picked]);

  useEffect(() => {
    if (!asking) return undefined;
    let live = true;
    const q = new URLSearchParams({ psychologist, month, duration: duration || 60 });
    if (child) q.set('child', child);
    // A move must not count the appointment against itself, or the time it
    // holds vanishes from the calendar offering to move it.
    if (exclude) q.set('exclude', exclude);
    api.get(`/availability/month/?${q}`)
      .then((r) => {
        if (!live) return;
        setServerToday(r.data.today || '');
        setState({ key, data: r.data, error: '' });
      })
      .catch((err) => {
        if (!live) return;
        // A refused month carries the agency's date: re-aim at the month it
        // will take instead of showing an error for a clock disagreement.
        const today = err.response?.status === 400 && err.response.data?.today;
        if (today && today.slice(0, 7) !== monthOf(serverToday || isoOf(new Date()))) {
          setServerToday(today); setPicked(null); return;
        }
        setState({ key, data: null, error: firstError(err.response?.data, 'Could not load this month.') });
      });
    return () => { live = false; };
    // `key` is every input; `tick` asks again for the same ones.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick, asking]);

  const current = asking && state.key === key ? state : null;
  return {
    month, setMonth: setPicked, first, last,
    data: current?.data || null,
    error: current?.error || '',
    busy: asking && !current,
    refresh: () => setTick((t) => t + 1),
  };
}
