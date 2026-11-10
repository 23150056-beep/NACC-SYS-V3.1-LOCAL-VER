import { useEffect, useState } from 'react';
import api from '../../api/client';

const pad = (n) => String(n).padStart(2, '0');
const localToday = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

/* Which psychologists the booking drawer may still offer for this child, length
   and day. The server answers with the booking rule itself, so a name it marks
   as not open really has no time that books. A stale answer is never used (the
   answer carries the key it was asked for), and a failed request leaves
   everyone selectable: the booking endpoint's refusal stays the authority. */
export function usePsychologistPicker({ child, duration, day, exclude, open }) {
  const [state, setState] = useState({ key: '', data: null });
  const [tick, setTick] = useState(0);
  // A day already behind us (moving an old appointment) is not a day the
  // server will judge; fall back to the whole booking window.
  const askedDay = day && day >= localToday() ? day : '';
  const key = `${child || ''}|${duration || 60}|${askedDay}|${exclude || ''}`;
  const asking = open;

  useEffect(() => {
    if (!asking) return undefined;
    let live = true;
    const q = new URLSearchParams({ duration: duration || 60 });
    if (child) q.set('child', child);
    if (askedDay) q.set('day', askedDay);
    if (exclude) q.set('exclude', exclude);
    api.get(`/availability/psychologists/?${q}`)
      .then((r) => { if (live) setState({ key, data: r.data }); })
      .catch(() => { if (live) setState({ key, data: null }); });
    return () => { live = false; };
    // `key` is every input; `tick` asks again for the same ones.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick, asking]);

  const current = asking && state.key === key ? state.data : null;
  const rows = new Map();
  if (current && !current.blocked) {
    current.psychologists.forEach((p) => { if (p.state !== 'unknown') rows.set(String(p.id), p); });
  }
  return {
    rows,                                   // empty = nobody is judged
    onDay: !!(current && current.day),
    refresh: () => setTick((t) => t + 1),
  };
}

/* The option text and whether it is greyed. Greyed in both modes: with a day
   chosen the server judges that day, with none it judges the whole booking
   window (today to the end of the month twelve months ahead, the month
   calendar's own range), so a greyed name has no time that books anywhere
   the drawer can reach. The chosen psychologist is never disabled: moving an
   appointment keeps its own, and taking the pick away silently would be
   worse than saying why it no longer fits. */
export function pickerOption(person, rows, selected) {
  const row = rows.get(String(person.id));
  const closed = !!row && row.state !== 'open';
  return {
    closed,
    disabled: closed && String(person.id) !== String(selected || ''),
    text: closed ? `${person.name} — ${row.label}` : person.name,
  };
}
