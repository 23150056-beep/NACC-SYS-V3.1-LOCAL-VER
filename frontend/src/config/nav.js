/* One navigation model, read by three things: the header's tabs, the left
 * rail, and the "everywhere else" overflow menu that appears when the rail is
 * gone. They used to be two separate lists and drifted — a destination added
 * to the rail was simply unreachable from the header.
 *
 * Role gating here is the same gating ProtectedRoute enforces on the route.
 * This list decides what is OFFERED; the route decides what is ALLOWED, and
 * the route is the one that matters.
 */
export const ALL_ROLES = ['Administrator', 'Psychologist', 'Staff'];

export const SCREENS = [
  { id: 'dashboard', to: '/', end: true, label: 'Dashboard', icon: 'layout-dashboard', section: 'Overview', roles: ALL_ROLES },

  { id: 'records', to: '/children', label: 'Records', icon: 'folder-heart', section: 'Casework', roles: ALL_ROLES },
  // Same route, role-scoped label: administrators govern the whole catalog,
  // psychologists only their own agency form templates (their instrument
  // titles live inside the Pre-Assessment wizard, step 4).
  {
    id: 'adoption', to: '/adoption', label: 'Adoption Tracker',
    icon: 'heart-handshake', roles: ['Administrator', 'Staff'],
  },
  {
    id: 'instruments', to: '/instruments', label: 'Instruments & Agency Forms',
    altLabel: { Psychologist: 'Pre-Assessment Instruments' },
    icon: 'clipboard-pen', roles: ['Administrator', 'Psychologist'],
  },

  { id: 'preassess', to: '/pre-assessment', label: 'Pre-Assessment', icon: 'clipboard-list', section: 'Clinical', roles: ['Psychologist'] },
  { id: 'monitor', to: '/monitoring', label: 'Progress Monitoring', icon: 'activity', roles: ALL_ROLES },
  { id: 'calendar', to: '/schedule', label: 'Calendar & booking', icon: 'calendar-days', roles: ALL_ROLES },
  { id: 'reports', to: '/reports', label: 'Results & Reports', icon: 'clipboard-check', roles: ALL_ROLES },

  { id: 'summary', to: '/reports/summary', label: 'Agency Summary', icon: 'bar-chart-3', section: 'Governance', roles: ['Administrator', 'Staff'] },
  // Credential Handoffs and the access queue are tabs inside this screen.
  { id: 'users', to: '/users', label: 'User Management', icon: 'user-cog', badge: 'pendingAccess', roles: ['Administrator'] },
  { id: 'settings', to: '/settings', label: 'Settings', icon: 'settings', roles: ['Administrator'] },
];

/* The header's icon tabs: the handful of places people actually go, in the
 * order they go there. Deliberately shorter than the rail — a tab strip that
 * lists everything is a rail with worse labels.
 *
 * Administrators get no Summary tab because Reports already carries them
 * there; the tab lights up for both. Staff, who have no clinical Reports work,
 * get Summary in its place.
 */
export const TOP_TABS = [
  { id: 'dashboard', label: 'Dashboard', roles: ALL_ROLES },
  { id: 'records', label: 'Records', roles: ALL_ROLES },
  { id: 'preassess', label: 'Pre-Assess', roles: ['Psychologist'] },
  { id: 'adoption', label: 'Adoption', roles: ['Administrator', 'Staff'] },
  { id: 'monitor', label: 'Monitor', roles: ALL_ROLES },
  { id: 'calendar', label: 'Calendar', roles: ALL_ROLES },
  { id: 'reports', label: 'Reports', roles: ALL_ROLES },
  { id: 'summary', label: 'Summary', roles: ['Staff'] },
  { id: 'users', label: 'Admin', roles: ['Administrator'] },
];

/* Which screen a URL belongs to. Longest prefix wins, so /reports/summary is
 * the summary and not a sub-page of reports. */
const MATCHERS = [
  ['/adoption/case', 'adoption'],
  ['/adoption', 'adoption'],
  ['/reports/summary', 'summary'],
  ['/report/child', 'chart'],     // a child's chart — opened FROM Records, but its own screen
  ['/children', 'records'],
  ['/instruments', 'instruments'],
  ['/pre-assessment', 'preassess'],
  ['/monitoring', 'monitor'],
  ['/schedule', 'calendar'],
  ['/reports', 'reports'],
  ['/users', 'users'],
  ['/settings', 'settings'],
];

export function screenIdFor(pathname) {
  if (pathname === '/') return 'dashboard';
  const hit = MATCHERS.find(([prefix]) => pathname === prefix || pathname.startsWith(prefix + '/'));
  return hit ? hit[1] : null;
}

/* Screens this role may be offered, with the role's own label applied. */
export function screensFor(role) {
  return SCREENS
    .filter((s) => s.roles.includes(role))
    .map((s) => (s.altLabel && s.altLabel[role] ? { ...s, label: s.altLabel[role] } : s));
}

/* The rail's rows: section headings interleaved with the screens under them.
 *
 * A heading belongs to the first screen of its group, and that screen is often
 * one this role cannot see — "Clinical" hangs off Pre-Assessment, which only
 * psychologists have. So the heading is carried forward to whichever screen in
 * the group IS visible, and dropped entirely if none is. Attaching it to the
 * screen instead put administrators' Progress Monitoring under "Casework".
 */
export function railRowsFor(role) {
  const rows = [];
  let carried = null;
  for (const s of SCREENS) {
    if (s.section) carried = s.section;
    if (!s.roles.includes(role)) continue;
    if (carried) { rows.push({ kind: 'section', label: carried }); carried = null; }
    rows.push({ kind: 'item', screen: s.altLabel && s.altLabel[role] ? { ...s, label: s.altLabel[role] } : s });
  }
  return rows;
}

export function topTabsFor(role) {
  const byId = Object.fromEntries(SCREENS.map((s) => [s.id, s]));
  return TOP_TABS
    .filter((t) => t.roles.includes(role) && byId[t.id]?.roles.includes(role))
    .map((t) => ({ ...t, to: byId[t.id].to, end: byId[t.id].end, icon: byId[t.id].icon, badge: byId[t.id].badge }));
}

/* A tab is lit for its own screen and for the screens that live under it but
 * have no tab of their own. */
export function tabIsActive(tabId, screenId, role) {
  if (tabId === screenId) return true;
  // A child's chart has no tab of its own; Records is where you came from.
  if (tabId === 'records' && screenId === 'chart') return true;
  if (tabId === 'reports' && screenId === 'summary' && role === 'Administrator') return true;
  return false;
}

/* The rail highlights the same way, so opening a child from Records does not
 * make the rail look as though you left Casework. */
export function railIsActive(itemId, screenId) {
  return itemId === screenId || (itemId === 'records' && screenId === 'chart');
}
