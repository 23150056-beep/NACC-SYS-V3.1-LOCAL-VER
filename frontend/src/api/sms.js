import api from './client';

/* Your own mobile number, and whether the gateway is working.
 *
 * All four calls are bound to the signed-in account on the server and take no
 * id — an administrator cannot point somebody else's notifications at a
 * handset of their choosing, and the test message goes to the caller.
 */

export const getMyPhone = () => api.get('/auth/me/phone/').then((r) => r.data);

/** Texts a six-digit code to `phone`. Rejects a landline before sending. */
export const requestPhoneCode = (phone) =>
  api.post('/auth/me/phone/', { phone }).then((r) => r.data);

/** Confirms the code. This is what makes the number usable for notifications. */
export const confirmPhoneCode = (code) =>
  api.put('/auth/me/phone/', { code }).then((r) => r.data);

/** Removes the number and stops the messages. */
export const removeMyPhone = () => api.delete('/auth/me/phone/').then((r) => r.data);

// Administrators only. Sends one message to the caller's own number and
// returns what the gateway actually replied — every other send happens on a
// background thread, so this is the only place a refusal is visible without
// reading server logs. The email card next to it exists for the same reason.
export const testSmsDelivery = () => api.post('/sms-test/').then((r) => r.data);

/* Confirms the key and reports the balance without spending a message. The
   gateways sell credits in small bundles and hand out about five to try with,
   so "is the key right" must not cost one of them. */
export const checkSmsGateway = () => api.get('/sms-test/').then((r) => r.data);
