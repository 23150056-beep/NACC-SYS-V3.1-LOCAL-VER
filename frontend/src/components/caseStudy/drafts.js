/* Unsaved typing in the case study, kept in this browser.
 *
 * The report is written over weeks, in boxes of up to 20,000 characters, and a
 * closed tab or a dropped connection must not cost the afternoon. Each changed
 * box is kept under
 *
 *     nacc-draft:case-study:<user>:<child>:<key>
 *
 * with the version it started from and the time. This is not a save, so
 * nothing asks; it is offered back as "Unsaved text from 3:40 PM" and only
 * becomes the record when the writer saves it. Logging out clears every
 * `nacc-draft:` key (context/AuthContext.jsx), so a shared workstation never
 * hands one person's text to the next.
 *
 * localStorage can be absent or throw (private windows, blocked site data):
 * every access is wrapped, and the screen works the same without it.
 */
export const DRAFT_PREFIX = 'nacc-draft:';

const keyFor = (userId, childId, key) => `${DRAFT_PREFIX}case-study:${userId}:${childId}:${key}`;

export function readDraft(userId, childId, key) {
  try {
    const raw = localStorage.getItem(keyFor(userId, childId, key));
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (d && typeof d === 'object' && 'value' in d) return d;
  } catch { /* storage unavailable or the entry is corrupt */ }
  return null;
}

/** `value` is the working copy as typed; `base` the saved version it started from. */
export function writeDraft(userId, childId, key, { value, notApplicable, base }) {
  try {
    localStorage.setItem(keyFor(userId, childId, key), JSON.stringify({
      value, not_applicable: !!notApplicable, base_version: base, saved_at: new Date().toISOString(),
    }));
  } catch { /* storage full or unavailable: the text is still on screen */ }
}

export function clearDraft(userId, childId, key) {
  try { localStorage.removeItem(keyFor(userId, childId, key)); } catch { /* nothing to clear */ }
}
