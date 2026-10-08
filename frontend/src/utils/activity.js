/* How an audit event is described and where clicking it goes.
 *
 * These lived in Topbar.jsx and were imported back out of it by the Dashboard,
 * which made a header component look like a library. The header is now
 * AppHeader and the activity feed appears in three places — the notification
 * popover, the right rail, and My Profile — so the shared vocabulary lives
 * here instead of inside whichever screen happened to need it first.
 */

export const ACTION_META = {
  created: { icon: 'plus', color: 'var(--success-500)', bg: 'var(--success-50)' },
  updated: { icon: 'pencil', color: 'var(--blue-700)', bg: 'var(--blue-50)' },
  archived: { icon: 'archive', color: 'var(--red-700)', bg: 'var(--red-50)' },
  login: { icon: 'log-in', color: 'var(--text-body)', bg: 'var(--ink-50)' },
  // A psychologist asked to take a child, and the answer (backend
  // children/assignment.py).
  requested: { icon: 'user-plus', color: 'var(--amber-700)', bg: 'var(--amber-50)' },
  accepted: { icon: 'user-check', color: 'var(--success-700)', bg: 'var(--success-50)' },
  declined: { icon: 'user-x', color: 'var(--red-700)', bg: 'var(--red-50)' },
  withdrawn: { icon: 'undo-2', color: 'var(--text-body)', bg: 'var(--ink-50)' },
  // The case study (backend case_study): started now, and finalized or
  // reopened once those events exist (phase P2).
  finalized: { icon: 'file-check', color: 'var(--success-700)', bg: 'var(--success-50)' },
  reopened: { icon: 'undo-2', color: 'var(--amber-700)', bg: 'var(--amber-50)' },
};

// Worded so they read right to the psychologist asked, to the social worker
// answered and in the ISA's audit trail alike: the actor is shown beneath.
const ASSIGNMENT_TEXT = {
  requested: 'Asked to take',
  accepted: 'Accepted the case of',
  declined: 'Declined the case of',
  withdrawn: 'Withdrew the request for',
};

// A case study event names the child, never any of its text. Keyed off the
// stored action: the start is a 'created' event, and Final and Reopen are
// 'finalized' and 'reopened' ones. Anything else reads as an edit.
const CASE_STUDY_TEXT = {
  created: 'Started a case study for',
  finalized: 'Finalized the case study for',
  reopened: 'Reopened the case study for',
};

export function eventText(e) {
  if (e.action === 'login') return 'Signed in';
  if (e.entity_type === 'CaseStudy') {
    return `${CASE_STUDY_TEXT[e.action] || 'Edited the case study for'} ${e.entity_label || 'a child'}`;
  }
  if (e.entity_type === 'Assignment') {
    return `${ASSIGNMENT_TEXT[e.action] || 'Assignment:'} ${e.entity_label || 'a child'}`;
  }
  const verb = e.action === 'created' ? 'Added' : e.action === 'updated' ? 'Edited' : 'Archived';
  const type = (e.entity_type || '').toLowerCase();
  return `${verb} ${type}${e.entity_label ? ` ${e.entity_label}` : ''}`.trim();
}

export function eventDestination(e, role) {
  const type = (e.entity_type || '').toLowerCase();
  // A psychologist's answer lives on their Dashboard: the child is not
  // theirs yet (or, after a transfer, no longer), so its page would 404.
  if (type === 'assignment') {
    if (role === 'Psychologist') return '/';
    return e.entity_id ? `/report/child/${e.entity_id}` : '/children';
  }
  if (type === 'child') return e.entity_id ? `/report/child/${e.entity_id}` : '/children';
  // The id is the child's; the page opens on its Case study tab.
  if (type === 'casestudy') return e.entity_id ? `/report/child/${e.entity_id}?tab=casestudy` : '/children';
  if (type === 'guardian') return '/children';
  if (type === 'appointment' || type === 'availabilityblock') return '/schedule';
  if (['instrumentcatalog', 'instrument', 'agencyformtemplate', 'questionnaire'].includes(type)) return '/instruments';
  if (e.category === 'user' || e.category === 'security' || type === 'user') {
    return role === 'Administrator' ? '/users' : '/';
  }
  return '/';
}
