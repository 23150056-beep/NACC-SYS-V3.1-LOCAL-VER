import api, { baseURL } from './client';
import { getAccess } from './session';

/* The social worker's email about an accepted case (backend: the design note
 * docs/superpowers/specs/2026-10-21-email-notifications-design.md, "Round three").
 *
 * Accepting a child and booking the first session are two saves, and the
 * social worker gets ONE email for both. So the acceptance email waits for the
 * "When will you see them first?" dialog: a booking made there is mailed by the
 * server as a single email (the first session; see the design note for the
 * rest), and a dialog that ends WITHOUT one tells the server so with these
 * calls, and the acceptance goes then. If neither arrives (a
 * crash, a dropped connection) the server mails it on its own after ten
 * minutes, so a failure of either call is nobody's business on screen.
 */

/** The dialog ended with no booking: mail the acceptance now. Answers
 *  `{ mailed }` - true the first time, false after. The caller fires and
 *  forgets: it must not wait for this, toast about it or log it. */
export const mailAssignmentAnswer = (id) =>
  api.post(`/assignment-requests/${id}/mail-answer/`).then((r) => r.data);

/** The same call for a page that is being closed. axios's request is cancelled
 *  with the page, so this one is fetch with `keepalive`, which the browser
 *  finishes after the page is gone. It cannot refresh an expired token the way
 *  the axios client does; the server's ten-minute sweep covers that. */
export function mailAssignmentAnswerOnExit(id) {
  const token = getAccess();
  if (!token) return;
  try {
    fetch(`${baseURL.replace(/\/+$/, '')}/assignment-requests/${id}/mail-answer/`, {
      method: 'POST',
      keepalive: true,
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
  } catch { /* a page that is closing has nowhere to report it */ }
}
