/* The four things every screen that shows a child needs, in one place.
 *
 * `caseRef` had three identical copies (Children, Report, ChildProgressReport)
 * and `ageFrom`/`ageGroup` one each; the redesign puts an avatar and a case
 * reference on almost every row of every screen, which would have made it
 * five or six copies.
 */

/** `C-0104` — the case reference people read out on the phone. */
export function caseRef(id) {
  return `C-${String(id).padStart(4, '0')}`;
}

export function ageFrom(birth) {
  if (!birth) return null;
  const d = new Date(birth);
  if (Number.isNaN(d.getTime())) return null;
  const diff = Date.now() - d.getTime();
  return Math.max(0, Math.floor(diff / (365.25 * 24 * 3600 * 1000)));
}

// Adviser-optimized age groups: Child 1-12, Teen 13-17.
export function ageGroup(age) {
  if (age == null) return null;
  if (age <= 12) return 'Child';
  if (age <= 17) return 'Teen';
  return 'Adult';
}

/* Two letters for an avatar: first name, then SURNAME.
 *
 * Not "the first two words". Filipino and Spanish surnames here routinely
 * carry a particle — Delos Reyes, De la Cruz, San Juan — and taking the last
 * word alone turns "Angel Delos Reyes" into AR while taking the first two
 * turns it into AD. The surname starts at the particle, so it is DR either
 * way, which is what the person themselves would write.
 */
const PARTICLE = /^(delos|dela|della|del|de|los|las|van|von|san|santa|santo|st)$/i;

export function initialsOf(name = '') {
  const parts = String(name).split(/\s+/).filter(Boolean);
  if (!parts.length) return '';
  if (parts.length === 1) return parts[0][0].toUpperCase();
  let surname = parts.length - 1;
  while (surname > 1 && PARTICLE.test(parts[surname - 1])) surname -= 1;
  return (parts[0][0] + parts[surname][0]).toUpperCase();
}

/* What an appointment is FOR, in the words the office uses.
 *
 * Mirrors Appointment.purpose on the server. It lives beside the other
 * per-child vocabulary because four screens and the left rail all label the
 * same three values, and they were copied into three of them.
 */
export const PURPOSE_LABEL = {
  pre_assessment: 'Pre-Assessment',
  session: 'Session',
  follow_up: 'Follow-up',
};

/* How a schedule names a child (owner's decision, 24 Sep 2026).
 *
 * The server leaves `child_name` out for a social worker unless the child is
 * in their own records (backend scheduling/visibility.py), and sends the case
 * reference and the social worker whose record it is instead. This turns
 * either shape into the words on a chip: the name when there is one,
 * otherwise "C-0042 · Ref. E. Pascua". The reference is what keeps one
 * worker's many children from reading as a row of identical chips.
 */
export function shortName(full = '') {
  const parts = String(full).trim().split(/\s+/).filter(Boolean);
  if (parts.length < 2) return parts[0] || '';
  const first = parts[0];
  let i = parts.length - 1;
  while (i > 1 && PARTICLE.test(parts[i - 1])) i -= 1;
  return `${first[0]}. ${parts.slice(i).join(' ')}`;
}

export function scheduleName(a) {
  if (a.child_name) return a.child_name;
  const ref = a.case_ref || (a.child ? caseRef(a.child) : 'A child');
  // referred_by_name is the social worker whose record the child is
  // (scheduling/visibility.py); none yet means only the ISA holds it.
  return a.referred_by_name ? `${ref} · Ref. ${shortName(a.referred_by_name)}` : `${ref} · no social worker yet`;
}
