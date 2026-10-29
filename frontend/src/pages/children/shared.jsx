import { Badge } from '../../ui';
import { clock } from '../../utils/time';

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
export const fmtTime = (iso) => clock(iso);
export const fmtDay = (iso) => new Date(iso).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

/* What each pre-assessment word means, read from Child.pre_assessment_status()
 * (children/models.py) and not from the words themselves: "Answered" is a
 * finished pre-assessment on a case that has NOT reached the Counseling stage;
 * "Completed" is the same with the case in Counseling. The agency's words stay
 * as they are (owner, 28 Oct 2026) - these only say what they stand for. */
export const PA_HINT = {
  'No Consent Yet': 'No signed consent is on file yet.',
  'Not Yet Pre-Assessed': 'Consent is signed; no pre-assessment has been started.',
  'In Progress': 'A pre-assessment has been started and is not finished.',
  Answered: 'The pre-assessment is finished. The case has not reached Counseling yet.',
  Completed: 'The pre-assessment is finished and the case is in Counseling.',
};

// How long a psychologist may sit on a request before it counts as unanswered:
// ASSIGNMENT_ANSWER_DAYS in backend/clinical/care_gaps.py, which lists the
// child as a care gap from that moment. The row offers someone else to the
// same people from the same moment.
export const ASSIGNMENT_ANSWER_MS = 7 * 24 * 60 * 60 * 1000;

/* What a social worker or the ISA could do about this record, from facts the
 * list already carries. The wording and the 7-day line are the Dashboard's care
 * gaps for a SW (clinical/care_gaps.py compute_staff_alerts: no referral, no
 * psychologist, no signed consent), so the two never disagree about a child.
 * A pending request inside the week is shown too, as "Waiting", but as a plain
 * note: the Dashboard does not call it a gap until the week is up. What the row
 * cannot know (self-report answers, bookings beyond the week) is not guessed.
 * Most urgent first. */
export function needsAttention(c, now = Date.now()) {
  if (c.status !== 'active') return [];
  const out = [];
  if (!c.has_case_referral) {
    out.push({ key: 'referral', tone: 'danger', label: 'No case referral', title: 'No session can be booked until a case referral is on file.' });
  }
  if (!c.psychologist) {
    const pending = c.pending_assignment;
    const declined = c.declined_assignment;
    const silent = pending?.created_at && now - new Date(pending.created_at).getTime() >= ASSIGNMENT_ANSWER_MS;
    if (pending && silent) {
      out.push({ key: 'psych', tone: 'amber', label: `No answer from ${pending.psychologist_name}`, title: 'Asked 7 or more days ago and not answered. Ask someone else.' });
    } else if (pending) {
      out.push({ key: 'psych', tone: 'neutral', label: `Waiting for ${pending.psychologist_name} to answer`, title: 'The child joins their records once they accept.' });
    } else if (declined) {
      out.push({ key: 'psych', tone: 'amber', label: 'Declined - ask another', title: `${declined.psychologist_name} declined: ${declined.reason}` });
    } else {
      out.push({ key: 'psych', tone: 'amber', label: 'No psychologist', title: 'Nobody is assigned, and nobody is being asked.' });
    }
  }
  if (c.pre_assessment_status === 'No Consent Yet') {
    out.push({ key: 'consent', tone: 'amber', label: 'No signed consent', title: 'No signed consent is on file.' });
  }
  return out;
}

// Roster schedule chip: today's session (amber) or next booking within the
// 7-day window (neutral), else nothing.
