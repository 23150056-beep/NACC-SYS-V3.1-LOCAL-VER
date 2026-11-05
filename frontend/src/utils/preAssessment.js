import { clock } from './time';

/* The pre-assessment wizard's steps and the status the rest of the app shows
 * for them. `index` is the wizard's internal step number in PreAssessment.jsx
 * (1 consent, 2 interview, 3 instruments, 4 problems, 4.5 review); the server
 * keeps the key (clinical PreAssessment.current_step). */
export const PA_STEPS = [
  { key: 'consent', label: 'Consent', index: 1 },
  { key: 'interview', label: 'Clinical interview', index: 2 },
  { key: 'instruments', label: 'Instruments', index: 3 },
  { key: 'problems', label: 'Presenting problems', index: 4 },
  { key: 'review', label: 'Review', index: 4.5 },
];

export const stepKeyFor = (index) => PA_STEPS.find((s) => s.index === index)?.key || null;
export const stepIndexFor = (key) => PA_STEPS.find((s) => s.key === key)?.index || null;

/* progress is Child.pre_assessment_progress from the server. */
export const paProgressLabel = (progress) => progress?.label || 'Not started';

const TONES = { not_started: 'neutral', started: 'amber', in_progress: 'brand', completed: 'success' };
export const paProgressTone = (progress) => TONES[progress?.state] || 'neutral';

export const paProgressDetail = (progress) => {
  if (progress?.state !== 'in_progress' && progress?.state !== 'started') return '';
  const parts = [];
  // The server counts the Child step as 1 (the wizard's "of 6"); the tracker's
  // five steps start at Consent, so convert.
  if (progress.step_number > 1) parts.push(`Step ${progress.step_number - 1} of ${PA_STEPS.length}`);
  if (progress.reassessment) parts.push('re-assessment');
  return parts.join(' · ');
};

/* The tracker: when each step was started, last edited and done. The server
 * sends five entries { step, label, started_at, last_edited_at, done_at,
 * seconds } (clinical/step_times.py). Times are the 12-hour clock via clock(),
 * never toLocaleTimeString. */
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const dayKey = (d) => d.getFullYear() * 10000 + d.getMonth() * 100 + d.getDate();

/** "Today, 9:30 AM" · "Yesterday, 9:30 AM" · "Nov 3, 9:30 AM" (+ year when not this year). */
export function formatStepMoment(iso, now = new Date()) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const time = clock(d);
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (dayKey(d) === dayKey(now)) return `Today, ${time}`;
  if (dayKey(d) === dayKey(yesterday)) return `Yesterday, ${time}`;
  const year = d.getFullYear() === now.getFullYear() ? '' : `, ${d.getFullYear()}`;
  return `${MONTHS[d.getMonth()]} ${d.getDate()}${year}, ${time}`;
}

/** "45 sec" · "12 min" · "1 hr 5 min" · "2 days 3 hr". */
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '';
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total} sec`;
  const mins = Math.round(total / 60);
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  const restMin = mins % 60;
  if (hours < 24) return restMin ? `${hours} hr ${restMin} min` : `${hours} hr`;
  const days = Math.floor(hours / 24);
  const restHr = hours % 24;
  const dayWord = `${days} ${days === 1 ? 'day' : 'days'}`;
  return restHr ? `${dayWord} ${restHr} hr` : dayWord;
}

/** The single most relevant short line for one step entry. */
export function stepSummary(entry, now = new Date()) {
  if (!entry) return 'Not started';
  if (entry.done_at) {
    const spent = entry.seconds != null ? ` · ${formatDuration(entry.seconds)}` : '';
    return `Done ${formatStepMoment(entry.done_at, now)}${spent}`;
  }
  if (entry.last_edited_at) return `Edited ${formatStepMoment(entry.last_edited_at, now)}`;
  if (entry.started_at) return `Started ${formatStepMoment(entry.started_at, now)}`;
  return 'Not started';
}

/** Short moment for a narrow place: "10:06 AM" today, "Nov 3" any other day. */
export function shortMoment(iso, now = new Date()) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  if (dayKey(d) === dayKey(now)) return clock(d);
  const year = d.getFullYear() === now.getFullYear() ? '' : ` ${d.getFullYear()}`;
  return `${MONTHS[d.getMonth()]} ${d.getDate()}${year}`;
}

/** The short line under a wizard rail step: "Done 9:14 AM · 9 min",
 *  "Edited 10:06 AM", "Started 9:58 AM", "Not started". */
export function stepRailLine(entry, now = new Date()) {
  if (!entry) return 'Not started';
  if (entry.done_at) {
    const spent = entry.seconds != null ? ` · ${formatDuration(entry.seconds)}` : '';
    return `Done ${shortMoment(entry.done_at, now)}${spent}`;
  }
  if (entry.last_edited_at) return `Edited ${shortMoment(entry.last_edited_at, now)}`;
  if (entry.started_at) return `Started ${shortMoment(entry.started_at, now)}`;
  return 'Not started';
}

/** The entry for one step key out of a list of entries. */
export const stepEntry = (entries, key) => (entries || []).find((e) => e.step === key) || null;
