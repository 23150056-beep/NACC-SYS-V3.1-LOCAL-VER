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
};

export function eventText(e) {
  if (e.action === 'login') return 'Signed in';
  const verb = e.action === 'created' ? 'Added' : e.action === 'updated' ? 'Edited' : 'Archived';
  const type = (e.entity_type || '').toLowerCase();
  return `${verb} ${type}${e.entity_label ? ` ${e.entity_label}` : ''}`.trim();
}

export function eventDestination(e, role) {
  const type = (e.entity_type || '').toLowerCase();
  if (type === 'child') return e.entity_id ? `/report/child/${e.entity_id}` : '/children';
  if (type === 'guardian') return '/children';
  if (type === 'appointment' || type === 'availabilityblock') return '/schedule';
  if (['instrumentcatalog', 'instrument', 'agencyformtemplate', 'questionnaire'].includes(type)) return '/instruments';
  if (e.category === 'user' || e.category === 'security' || type === 'user') {
    return role === 'Administrator' ? '/users' : '/';
  }
  return '/';
}
