import api from './client';

// Sends a test message to the signed-in administrator's own address and
// returns what the mail service actually replied. Every other send in this
// system is fire-and-forget, so this is the only place a failure is visible
// without reading server logs.
export const testEmailDelivery = () =>
  api.post('/email-test/').then((r) => r.data);

/* Your own email switches, one per email NACC SYS may send to your role.
 *
 * Both calls are bound to the signed-in account on the server and take no id,
 * like the mobile number's: nobody can read or change a colleague's switches.
 * The answer is {address, sending, kinds: [{key, label, description,
 * enabled}]}; an administrator's `kinds` is empty. A kind with no stored
 * choice is on, so the first answer for an account that never opened the card
 * is everything on.
 */
export const getMyEmailNotifications = () =>
  api.get('/auth/me/email-notifications/').then((r) => r.data);

/** Sends only the switches that changed, `{ case_request: false }`, and gets
 *  the whole list back. A 400 is `{ enabled: "<sentence>" }` and writes nothing. */
export const saveMyEmailNotifications = (enabled) =>
  api.put('/auth/me/email-notifications/', { enabled }).then((r) => r.data);
