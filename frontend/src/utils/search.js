import { caseRef } from './child';
import { shortDate } from './time';

/* How the search box in the header matches a child (components/GlobalSearch).
 *
 * It used to compare the whole typed text with the name and the case number,
 * and nothing else - but intake rarely starts from a full name. Somebody has
 * "the one whose lola is Rosario", "the boy from Bantay", "born 2015". All of
 * that is already in the list the search downloads, so it is matched here:
 *
 * - every word typed must be found somewhere, in any order ("Cruz Ana");
 * - accents and the tilde do not count (Penamora finds Peñamora);
 * - "Ma." in a name is also read as Maria, the way the agency writes it;
 * - besides the name and case number: the custodian, the barangay,
 *   municipality and province, the birth date (2015, 2015-03-02 or
 *   03/02/2015), the case type, and the psychologist and social worker.
 *
 * A match outside the name says why (`reason`), or a list of twenty foster
 * children answering "foster" reads as a fault. Medical notes, the referral
 * reason and contact numbers are never searched: the box is for finding a
 * child, not for reading their file.
 */

/** Lower case, accents and the tilde taken off: "Peñamora" -> "penamora". */
export function fold(text) {
  return String(text ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

/** The words typed, folded; a trailing dot ("Ma.") is not part of the word. */
export function searchWords(query) {
  return fold(query).split(/[\s,]+/).map((w) => w.replace(/\.+$/, '')).filter(Boolean);
}

// "Ma. Cristina" is Maria Cristina; read both ways.
const withMaria = (name) => name.replace(/\bma\.?(?=\s|$)/g, 'maria ma');

// A word somewhere in the text starts with it: "ros" finds "Rosario Lim" but
// "ana" does not find "Juliana", which would only be noise in a reason.
const startsAWord = (text, word) => text.split(/[\s,.()/-]+/).some((t) => t.startsWith(word));

function birthForms(iso) {
  if (!iso) return '';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  return `${iso} ${m}/${d}/${y} ${Number(m)}/${Number(d)}/${y}`;
}

/* The places a word may be found, best first. `name` and `ref` are the child
 * themselves and need no reason; the rest say where the word was found. */
function fieldsOf(c) {
  const place = [c.barangay, c.municipality, c.province].filter(Boolean).join(', ');
  return [
    { key: 'name', text: withMaria(fold([c.fullname, c.first_name, c.middle_name, c.last_name].filter(Boolean).join(' '))), loose: true },
    { key: 'ref', text: fold(caseRef(c.id)), loose: true },
    { key: 'custodian', text: fold(c.custodian_name), reason: c.custodian_name && `Custodian: ${c.custodian_name}` },
    { key: 'place', text: fold(place), reason: place && `Lives in ${place}` },
    { key: 'born', text: birthForms(c.birth_date), loose: true, reason: c.birth_date && `Born ${shortDate(`${c.birth_date}T00:00:00`)}` },
    { key: 'case', text: fold([c.case_type, c.case_category, c.type_of_adoption].filter(Boolean).join(' ')), reason: [c.case_type, c.case_category].filter(Boolean).join(' · ') },
    { key: 'psych', text: fold(c.psychologist_name), reason: c.psychologist_name && `Psychologist: ${c.psychologist_name}` },
    { key: 'sw', text: fold(c.social_worker_name), reason: c.social_worker_name && `Social worker: ${c.social_worker_name}` },
  ];
}

/**
 * Children matching what was typed, best first, each as
 * `{ child, reason, field, score }`. `reason` is null when the name or case
 * number matched; otherwise it says where the match was, and `field` which
 * field that was ('custodian', 'place', 'born', 'case', 'psych', 'sw').
 */
export function matchChildren(children, query) {
  const words = searchWords(query);
  if (!words.length) return [];
  const out = [];
  for (const c of children) {
    const fields = fieldsOf(c);
    let reason = null;
    let field = null;
    let score = 0;
    let ok = true;
    for (const w of words) {
      // A word of one or two letters is only looked for in the name and the
      // case number: "an" found in every "Santos" custodian is not a search.
      const hit = fields.find((f) => f.text && (f.loose
        ? (f.key === 'name' || f.key === 'ref' || w.length >= 3) && f.text.includes(w)
        : w.length >= 3 && startsAWord(f.text, w)));
      if (!hit) { ok = false; break; }
      if (hit.key === 'name') {
        score += startsAWord(hit.text, w) ? 12 : 6;
      } else if (hit.key === 'ref') {
        score += 10;
      } else {
        score += 2;
        if (!reason && hit.reason) { reason = hit.reason; field = hit.key; }
      }
    }
    if (!ok) continue;
    // The name beginning with the first word typed is what people mean.
    if (fields[0].text.startsWith(words[0])) score += 4;
    out.push({ child: c, reason, field, score });
  }
  return out.sort((a, b) => (b.score - a.score)
    || String(a.child.fullname).localeCompare(String(b.child.fullname)));
}

/** Staff accounts (the ISA's search) by name or email, words in any order. */
export function matchStaff(staff, query) {
  const words = searchWords(query);
  if (!words.length) return [];
  return staff.filter((u) => {
    const text = fold(`${u.fullname || ''} ${u.email || ''}`);
    return words.every((w) => text.includes(w));
  });
}

/** An action or screen whose label or keywords start with every word typed. */
export function matchesCommand(item, query) {
  const words = searchWords(query);
  if (!words.length) return false;
  const text = fold(`${item.label} ${item.keywords || ''}`);
  return words.every((w) => startsAWord(text, w));
}

/* The children this account opened lately, for the search box to offer
 * before anything is typed.
 *
 * Ids only, never names: the names come from the list the server sends now,
 * so a child moved to another worker or psychologist simply stops appearing.
 * Kept in sessionStorage, where the sign-in is, so it ends with the tab - and
 * AuthContext's logout clears it, because an office computer is shared and
 * the next person should not see whose records were opened. */
const RECENT_PREFIX = 'nacc-recent:';
const RECENT_KEPT = 8;

export function recentChildIds(userId) {
  if (!userId) return [];
  try {
    const ids = JSON.parse(sessionStorage.getItem(RECENT_PREFIX + userId) || '[]');
    return Array.isArray(ids) ? ids.map(String) : [];
  } catch { return []; }
}

export function rememberChild(userId, childId) {
  if (!userId || !childId) return recentChildIds(userId);
  const ids = [String(childId), ...recentChildIds(userId).filter((id) => id !== String(childId))].slice(0, RECENT_KEPT);
  try { sessionStorage.setItem(RECENT_PREFIX + userId, JSON.stringify(ids)); } catch { /* storage unavailable */ }
  return ids;
}

export function forgetRecentChildren() {
  try {
    Object.keys(sessionStorage)
      .filter((k) => k.startsWith(RECENT_PREFIX))
      .forEach((k) => sessionStorage.removeItem(k));
  } catch { /* storage unavailable */ }
}
