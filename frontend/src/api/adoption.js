import api from './client';

/* The adoption tracker's API surface.
 *
 * `board` is deliberately one call. The stage strip, the cards, the KPI tiles,
 * the handoff banner and the worklist are all views of the same set of cases —
 * fetching them separately is how a screen ends up showing "9 open" above a
 * board with eight cards on it.
 */
export const getBoard = () => api.get('/adoption/board/').then((r) => r.data);

export const getCase = (id) => api.get(`/adoption/cases/${id}/`).then((r) => r.data);

export const admitChild = (child, note = '') =>
  api.post('/adoption/admit/', { child, note }).then((r) => r.data);

export const advanceCase = (id, note = '') =>
  api.post(`/adoption/cases/${id}/advance/`, { note }).then((r) => r.data);

export const revertCase = (id, note) =>
  api.post(`/adoption/cases/${id}/revert/`, { note }).then((r) => r.data);

export const closeCase = (id, reason, note = '') =>
  api.post(`/adoption/cases/${id}/close/`, { reason, note }).then((r) => r.data);

export const matchFamily = (id, pap) =>
  api.post(`/adoption/cases/${id}/match/`, { pap }).then((r) => r.data);

export const listFamilies = (eligibleOnly = false) =>
  api.get('/adoption/paps/', { params: eligibleOnly ? { eligible: '1' } : {} })
    .then((r) => r.data);

/* The docket. `submit` carries a file when there is one, so it goes as
 * multipart; verify and waive are plain posts. */
export const submitRequirement = (id, file = null, dueDate = null) => {
  const fd = new FormData();
  if (file) fd.append('document', file);
  if (dueDate) fd.append('due_date', dueDate);
  return api.post(`/adoption/requirements/${id}/submit/`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data);
};

export const verifyRequirement = (id) =>
  api.post(`/adoption/requirements/${id}/verify/`).then((r) => r.data);

export const waiveRequirement = (id, reason) =>
  api.post(`/adoption/requirements/${id}/waive/`, { reason }).then((r) => r.data);

/* One derived status, one appearance — the same six values the server computes
 * in adoption/status.py. Nothing here re-derives; it only names and colours. */
export const STATUS_META = {
  overdue: { label: 'Overdue', tone: 'danger', color: 'var(--red-700)', bg: 'var(--red-50)', border: 'var(--red-200)' },
  at_risk: { label: 'At risk', tone: 'warning', color: 'var(--warning-700)', bg: 'var(--warning-50)', border: 'var(--warning-100)' },
  waiting: { label: 'Waiting', tone: 'brand', color: 'var(--blue-700)', bg: 'var(--blue-50)', border: 'var(--blue-100)' },
  on_track: { label: 'On track', tone: 'success', color: 'var(--success-700)', bg: 'var(--success-50)', border: 'var(--success-100)' },
  on_hold: { label: 'On hold', tone: 'neutral', color: 'var(--text-muted)', bg: 'var(--ink-50)', border: 'var(--border)' },
  complete: { label: 'Complete', tone: 'success', color: 'var(--success-700)', bg: 'var(--success-50)', border: 'var(--success-100)' },
};

/* An icon per stage, in the order the process runs. Read from the stage
 * number rather than its name so renaming a stage in the database — which the
 * spec expects offices to do — does not blank the strip. */
export const STAGE_ICONS = {
  1: 'clipboard-check',
  2: 'gavel',
  3: 'folder-open',
  4: 'users-round',
  5: 'stamp',
  6: 'home',
  7: 'scale',
  8: 'party-popper',
};

export const REQUIREMENT_STATE_META = {
  pending: { label: 'Pending', icon: 'circle-dashed', color: 'var(--text-faint)' },
  submitted: { label: 'Submitted', icon: 'clock', color: 'var(--warning-700)' },
  verified: { label: 'Verified', icon: 'check-circle-2', color: 'var(--success-700)' },
  waived: { label: 'Waived', icon: 'minus-circle', color: 'var(--text-muted)' },
};
