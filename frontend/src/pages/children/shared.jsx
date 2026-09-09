import { Badge } from '../../ui';

/* The handful of things the records page and the record drawer both need.
 *
 * Extracted when ChildDrawer moved out of Children.jsx — not because five
 * small helpers deserve a module, but because the alternative was importing
 * them back out of the page component, which makes the page look like a
 * library and hides which way the dependency runs.
 */

export function StatusChip({ child, size = 'sm' }) {
  if (child.status === 'inactive') return <Badge tone="neutral" size={size} dot>Archived (Terminated)</Badge>;
  return <Badge tone="success" size={size} dot>Active{child.case_type ? ` · ${child.case_type}` : ''}</Badge>;
}

// Re-exported, not redefined: the left rail and the header both label
// appointments too, and neither should be importing out of a page.
export { PURPOSE_LABEL } from '../../utils/child';
// Local YYYY-MM-DD (never toISOString — it shifts the date in UTC+8 evenings).
export const localDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
export const fmtDay = (iso) => new Date(iso).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

// Roster schedule chip: today's session (amber) or next booking within the
// 7-day window (neutral), else nothing.
