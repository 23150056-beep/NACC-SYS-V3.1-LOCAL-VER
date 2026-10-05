import { caseRef } from '../utils/child';

/* Each deterministic care-gap rule, in the words the person acting on it would
 * use, plus where that action happens. The rules themselves live in
 * backend/clinical/care_gaps.py — this only names them.
 *
 * The table is shared by the Dashboard's care-gap card and the assistant
 * panel's list_care_gaps answer, so the two cannot send the same gap to
 * different places. `to` is a path, a function of the child id, or null for
 * the child's own page. */
export const GAP_META = {
  // A psychologist's, and the ISA's (and the two booking rules, which a social
  // worker has too).
  consent_missing: { chip: 'No consent', action: 'Attach', to: '/pre-assessment', tone: 'danger' },
  pre_assessment_overdue: { chip: 'Stalled', action: 'Start', to: '/pre-assessment', tone: 'warning' },
  report_missing: { chip: 'Report due', action: 'Upload', to: '/reports', tone: 'warning' },
  follow_up_overdue: { chip: 'Overdue', action: 'Book', to: '/schedule', tone: 'danger' },
  no_upcoming_appointment: { chip: 'Unbooked', action: 'Book', to: '/schedule', tone: 'info' },
  self_report_concern: { chip: 'Unread words', action: 'Read', to: null, tone: 'danger' },
  // A social worker's (backend/clinical/care_gaps.py compute_staff_alerts)
  no_case_referral: { chip: 'No referral', action: 'Upload', to: (id) => `/reports?upload=1&child=${id}`, tone: 'danger' },
  no_psychologist: { chip: 'Unassigned', action: 'Assign', to: (id) => `/children?q=${caseRef(id)}`, tone: 'warning' },
  no_signed_consent: { chip: 'No consent', action: 'Open', to: null, tone: 'warning' },
};

// Gaps whose action is a Psychologist-only screen (/pre-assessment). The ISA
// reads the same gap but cannot work in that screen, so for the ISA the
// action is the child's own page, where the consent and the pre-assessment
// can be seen.
const PSYCHOLOGIST_ONLY = new Set(['consent_missing', 'pre_assessment_overdue']);

/* `role` is the viewer's role_name. */
export function gapMeta(type, severity, role) {
  const meta = GAP_META[type] || { chip: 'Follow up', action: 'Open', to: null, tone: severity };
  if (role === 'Administrator' && PSYCHOLOGIST_ONLY.has(type)) {
    return { ...meta, action: 'Open', to: null };
  }
  return meta;
}

/* Where a gap's action goes: its own screen, else the child's page. */
export function gapTarget(g, role) {
  const { to } = gapMeta(g.type, g.severity, role);
  const path = typeof to === 'function' ? to(g.child_id) : to;
  return path || `/report/child/${g.child_id}`;
}
