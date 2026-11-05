/* Where the session lives, decided once.
 *
 * It used to be localStorage, which survives closing the tab, closing the
 * browser and restarting the machine — so with a day-long refresh token and
 * automatic renewal, signing in once bought roughly 24 hours of access on that
 * workstation. On a shared office machine that is the wrong default: the next
 * person to open the browser is already somebody.
 *
 * sessionStorage ends when the browser context does. Close it and the session
 * is gone; reopening lands on the sign-in page. That applies to the Google
 * door exactly as it does to the password one — both stored their tokens in
 * the same place and always did.
 *
 * The trade, stated rather than discovered: sessionStorage is per-TAB, and a
 * button in an email always opens a NEW tab, so it met the sign-in form even
 * with the app open and signed in beside it. A tab that opens with no session
 * now BORROWS one from a tab of this site that is already signed in, in the
 * same browser, over a BroadcastChannel (below) - never on the sign-in, sign-up
 * and survey pages. The tokens are still only ever in sessionStorage — never
 * localStorage — so closing every tab still ends the session, and another
 * browser, another profile or a private window has nothing to borrow from.
 * Signing out signs out every tab holding that session. Someone at the desk who
 * opens a new tab while another is signed in is in, but they could already
 * click the open one. Why that is safe, and what it does not help:
 * docs/superpowers/specs/2026-10-26-shared-tab-session-design.md.
 *
 * One module because api/client.js and context/AuthContext.jsx both touch
 * these keys, and two files disagreeing about where the token lives is a bug
 * that looks like a random logout.
 */
const ACCESS = 'access';
const REFRESH = 'refresh';
// Sessions that were signed out, in localStorage: the one place every tab
// shares, including a tab that was not running when it happened (discarded to
// save memory, or sent to another site and brought back with Back). Each entry
// is {tail, userId, at}: only the end of the refresh token and the account's id
// - enough to recognise a session, not to use it - and an entry goes after 24
// hours, which is as long as a refresh token lives anyway.
const SIGNED_OUT = 'nacc-signed-out';
const SIGNED_OUT_KEEP_MS = 24 * 60 * 60 * 1000;
const SIGNED_OUT_MAX = 200;

export const getAccess = () => sessionStorage.getItem(ACCESS);
export const getRefresh = () => sessionStorage.getItem(REFRESH);

export function setTokens(access, refresh) {
  sessionStorage.setItem(ACCESS, access);
  if (refresh) sessionStorage.setItem(REFRESH, refresh);
}

export function clearTokens() {
  sessionStorage.removeItem(ACCESS);
  sessionStorage.removeItem(REFRESH);
  // Tokens used to live in localStorage. Anyone upgrading with a session still
  // sitting there would otherwise keep it until it expired on its own.
  try {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  } catch {
    /* private mode, or storage disabled — nothing to clean up either way */
  }
}

// What identifies a refresh token without putting the token itself into a
// message that every tab reads, or into a list that outlives the tab. 24
// characters of a signed token do not repeat.
export const tail = (token) => (typeof token === 'string' && token ? token.slice(-24) : '');

const present = (v) => v !== undefined && v !== null && v !== '';

// The tail of the refresh token THIS tab holds; '' for none, or when the
// browser will not let the tab read its storage.
export const heldTail = () => {
  try {
    return tail(getRefresh());
  } catch {
    return '';
  }
};

// Whose a token is. Every SimpleJWT token carries the account's id as
// `user_id` (SIMPLE_JWT sets no USER_ID_CLAIM, so it is the default); it is read
// out of the payload here and NOT verified - the server verifies the token on
// every use, and this id only decides which tab's session to look for, hand
// on or drop. It is always a string, '' when there is none: no token, one that
// is not three parts, a payload that is not JSON, or no usable `user_id`. It
// never throws. If USER_ID_CLAIM is ever changed, this must follow it.
export function userIdOf(token) {
  if (typeof token !== 'string') return '';
  const parts = token.split('.');
  if (parts.length !== 3 || !parts[1]) return '';
  try {
    // base64url -> base64, padded: atob wants the plain alphabet.
    let b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const id = JSON.parse(new TextDecoder().decode(bytes))?.user_id;
    if (typeof id === 'number' && Number.isSafeInteger(id)) return String(id);
    if (typeof id === 'string' && id) return id;
  } catch { /* not a token we can read */ }
  return '';
}

// Whose session THIS tab holds, from its refresh token: '' for none, or when
// the browser will not let the tab read its storage. Unlike the loaded user,
// it is known while /auth/me/ is still on its way, and it cannot lag a sign-in
// by a render.
export const heldUserId = () => {
  try {
    return userIdOf(getRefresh());
  } catch {
    return '';
  }
};

// The signed-out list, pruned on every read: anything older than 24 hours, and
// anything that is not an entry, goes - and the cleaned list is written back.
// Storage may be blocked or full, and then the list reads as empty: a tab that
// cannot be told is no worse off than before there was a list.
function signedOutList(now) {
  let all;
  try {
    all = JSON.parse(localStorage.getItem(SIGNED_OUT) || '[]');
  } catch {
    return [];
  }
  if (!Array.isArray(all)) return [];
  const kept = all.filter((e) => e && typeof e.tail === 'string' && e.tail
    && typeof e.at === 'number' && now - e.at < SIGNED_OUT_KEEP_MS);
  if (kept.length !== all.length) writeSignedOut(kept);
  return kept;
}

function writeSignedOut(list) {
  try {
    localStorage.setItem(SIGNED_OUT, JSON.stringify(list));
  } catch { /* blocked or full: the broadcast still reaches the tabs that are running */ }
}

// Write down that this session was signed out: its refresh token's tail, and
// whose it was. Newest last, and the oldest go first if the list ever fills.
export function rememberSignedOut(tailOfToken, userId) {
  if (!tailOfToken) return;
  const now = Date.now();
  const kept = signedOutList(now).filter((e) => e.tail !== tailOfToken);
  kept.push({ tail: tailOfToken, userId: present(userId) ? String(userId) : '', at: now });
  writeSignedOut(kept.slice(-SIGNED_OUT_MAX));
}

// Whether the session a refresh token belongs to was signed out: its tail is
// listed, or its account is. The account counts because signing out signs out
// every tab of that account in this browser, and a tab that signed in
// separately has a tail nobody wrote down. An entry with no account - one from
// before the account was recorded, or one the person's signing in again has
// blanked (forgetSignedOutUser) - matches by tail alone, never by account.
export function isSignedOut(refresh) {
  const t = tail(refresh);
  if (!t) return false;
  const u = userIdOf(refresh);
  return signedOutList(Date.now()).some((e) => e.tail === t
    || (u !== '' && present(e.userId) && e.userId === u));
}

// The session THIS tab holds, asked the same question; false for none.
export const heldSessionSignedOut = () => {
  try {
    const refresh = getRefresh();
    return Boolean(refresh) && isSignedOut(refresh);
  } catch {
    return false;
  }
};

// Signing in again lifts the ACCOUNT's entry, not the old sessions': the
// person's new session must not be taken for the one they signed out of, so
// their entries lose the account id. The tails STAY listed, so a tab still
// holding a session from before the sign-out (frozen, discarded, or sent away
// and brought back) stays dead after the person signs in again; removing the
// entries would have let it come back to life. Called by every sign-in through
// the form or Google, before the new tokens are stored.
export function forgetSignedOutUser(userId) {
  if (!present(userId)) return;
  const u = String(userId);
  const all = signedOutList(Date.now());
  if (!all.some((e) => e.userId === u)) return;
  writeSignedOut(all.map((e) => (e.userId === u ? { ...e, userId: '' } : e)));
}

// One channel for the whole page, opened once. Several listeners share it
// (borrowing, answering, the sign-out news), and a message is never delivered
// to the object that posted it, so a tab does not hear itself. Same-origin
// only: another site cannot join it. Null where there is no BroadcastChannel
// or it will not open, and then every function below does nothing, which is
// how the app behaved before tabs could share a session.
const channel = (() => {
  try {
    return new BroadcastChannel('nacc-session');
  } catch {
    return null;
  }
})();

const newId = () => {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  } catch { /* fall through to the plain id */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
};

// A user id compared as a number: `01` is `1`. Anything that is not all digits
// is left as it is.
const plainId = (v) => (/^\d+$/.test(String(v)) ? String(Number(v)) : String(v));

// An email button's `for=<recipient's user id>`, read out of a query string and
// taken off it. Returns the id (digits only, as a plain number: `01` is `1`;
// anything else is ignored, as if the link had no `for`) and the query string
// without any `for`. Every other parameter is kept exactly as written, so a
// link's other parameters survive being tidied. A query string with no `for` is
// handed back untouched.
export function splitForUser(search) {
  const raw = typeof search === 'string' ? search.replace(/^\?/, '') : '';
  const isFor = (pair) => {
    const eq = pair.indexOf('=');
    try {
      return decodeURIComponent((eq === -1 ? pair : pair.slice(0, eq)).replace(/\+/g, ' ')) === 'for';
    } catch {
      return false;
    }
  };
  const pairs = raw ? raw.split('&') : [];
  const asked = pairs.filter(isFor);
  if (!asked.length) return { forUser: null, search };
  let value = '';
  try {
    const eq = asked[0].indexOf('=');
    value = eq === -1 ? '' : decodeURIComponent(asked[0].slice(eq + 1).replace(/\+/g, ' '));
  } catch { /* an address that cannot be decoded names nobody */ }
  // Each `for` goes, and so do the empty pairs lying against it ("a=1&&for=2"
  // would otherwise leave "a=1&"). An empty pair anywhere else is left as it is.
  const gone = new Set();
  pairs.forEach((p, i) => {
    if (!isFor(p)) return;
    gone.add(i);
    for (let j = i - 1; j >= 0 && pairs[j] === ''; j -= 1) gone.add(j);
    for (let j = i + 1; j < pairs.length && pairs[j] === ''; j += 1) gone.add(j);
  });
  const kept = pairs.filter((_, i) => !gone.has(i));
  return {
    forUser: /^\d+$/.test(value) ? plainId(value) : null,
    search: kept.length ? `?${kept.join('&')}` : '',
  };
}

// Read `for` ONCE, as the app starts, and take it off the address with
// replaceState (path, other parameters, hash and the history entry's state are
// all kept). It has to happen before the router reads the location, and only
// once: a bookmark or a copied address then never carries it, and a colleague's
// tab is not restricted by somebody else's link. Not a React state initializer
// - Strict Mode runs those twice, and the second run would find it gone.
// Returns the id, or null when there was none.
export function takeForUser() {
  try {
    const { pathname, search, hash } = window.location;
    const { forUser, search: rest } = splitForUser(search);
    if (rest !== search) {
      try {
        window.history.replaceState(window.history.state, '', pathname + rest + hash);
      } catch { /* the address stays as it is; the id was still read */ }
    }
    return forUser;
  } catch {
    return null;
  }
}

// A tab with no session asks the others for one. Resolves true when it now
// holds tokens it was given, false when it has nothing and the form should show.
//
// `forUser` is whose session the tab wants: an email button names its
// recipient. It then takes the FIRST answer from that user, straight away, and
// ignores everyone else's - somebody else's session is not what the link was
// for - and with none inside `wait` ms it takes nothing.
//
// Without it, nothing says whose the tab should be. It waits `wait` ms for a
// first answer, then `settle` more for the rest, and borrows only if every
// answer names the same user; if they differ, two people are signed in in this
// browser and it takes nothing rather than guess. This does not hear every
// signed-in tab: one that is frozen, discarded or busy past `settle` says
// nothing in time, and a second account behind it goes unseen. That is
// accepted - it is the same as clicking the open tab.
//
// Not an answer: one that is malformed; one whose label disagrees with its own
// refresh token (whose it is comes from the token, so a mislabelled or lying
// share names nobody we can trust); one whose session or account is on the
// signed-out list; and one that a 'signed-out' or 'ended' message heard while
// waiting says is dead - including an answer already collected, which is
// dropped, since the person may have left in between.
export function borrowSession({ forUser = null, wait = 400, settle = 80 } = {}) {
  if (!channel) return Promise.resolve(false);
  return new Promise((resolve) => {
    const id = newId();
    const wanted = present(forUser) ? plainId(forUser) : null;
    let shares = [];
    // What the news heard during the wait says is gone: a message can arrive
    // before the answer it concerns.
    const goneTails = new Set();
    const goneUsers = new Set();
    let waitTimer = null;
    let settleTimer = null;
    let finished = false;

    const finish = (borrowed) => {
      if (finished) return;
      finished = true;
      clearTimeout(waitTimer);
      clearTimeout(settleTimer);
      channel.removeEventListener('message', onMessage);
      resolve(borrowed);
    };

    const usable = (s) => !goneTails.has(tail(s.refresh))
      && !goneUsers.has(s.userId)
      && !isSignedOut(s.refresh);

    const take = (share) => {
      try {
        setTokens(share.access, share.refresh);
        finish(true);
      } catch {
        finish(false); // sessionStorage blocked: nothing could be kept
      }
    };

    const decide = () => {
      // Looked at again now: a sign-out may have been written down since the
      // answer arrived, with its message still on the way.
      shares = shares.filter(usable);
      if (!shares.length) { finish(false); return; }
      const first = shares[0];
      if (!shares.every((s) => s.userId === first.userId)) { finish(false); return; }
      take(first);
    };

    function onMessage(event) {
      const d = event.data;
      if (!d) return;
      if (d.type === 'signed-out' || d.type === 'ended') {
        if (d.tail) goneTails.add(d.tail);
        if (d.type === 'signed-out' && present(d.userId)) goneUsers.add(String(d.userId));
        shares = shares.filter(usable);
        return;
      }
      if (d.type !== 'share' || d.id !== id) return;
      if (typeof d.access !== 'string' || !d.access) return;
      if (typeof d.refresh !== 'string' || !d.refresh) return;
      // The label has to be the token's own: from here on a share's user is the
      // one the refresh token names.
      const owner = userIdOf(d.refresh);
      if (!owner || !present(d.userId) || String(d.userId) !== owner) return;
      const share = { access: d.access, refresh: d.refresh, userId: owner };
      if (!usable(share)) return;
      if (wanted) {
        if (owner === wanted) take(share);
        return;
      }
      shares.push(share);
      if (settleTimer === null) {
        // The first answer is in; stop the clock on waiting for one, and give
        // the others a moment.
        clearTimeout(waitTimer);
        settleTimer = setTimeout(decide, settle);
      }
    }

    channel.addEventListener('message', onMessage);
    waitTimer = setTimeout(() => finish(false), wait);
    try {
      channel.postMessage({ type: 'ask', id });
    } catch {
      finish(false);
    }
  });
}

// The other half: a signed-in tab hands its session to a tab that asks. Only a
// tab that has loaded its user and holds both tokens answers, so a tab still
// loading, or one whose session is gone, says nothing - and neither does one
// whose user is in the middle of a forced password change, nor one whose
// session was signed out. `getUser` is read at the moment of asking, not when
// this is installed. The share is labelled with the user the refresh token it
// hands over names, never with the loaded user: the two can disagree for a
// render after a sign-in, and a share must not say it is somebody else's.
// Returns the way to stop.
export function answerAsks(getUser) {
  if (!channel) return () => {};
  const onMessage = (event) => {
    const d = event.data;
    if (!d || d.type !== 'ask' || typeof d.id !== 'string' || !d.id) return;
    try {
      const user = getUser();
      if (!user || !present(user.id) || user.must_change_password === true) return;
      const access = getAccess();
      const refresh = getRefresh();
      if (!access || !refresh || isSignedOut(refresh)) return;
      const userId = userIdOf(refresh);
      if (!userId) return;
      channel.postMessage({ type: 'share', id: d.id, access, refresh, userId });
    } catch {
      /* storage blocked, or the message could not be sent: nothing to share */
    }
  };
  channel.addEventListener('message', onMessage);
  return () => channel.removeEventListener('message', onMessage);
}

// Signing out signs out every tab holding this session, and any tab signed in
// as the same account: a tab that carried on would outlive the person leaving.
// The tail finds the first kind even while that tab's user is still loading;
// the account's id finds a tab that signed in separately. A tab signed in as
// somebody else is left alone. The id is the one the session's own refresh
// token names (`loadedUserId`, the loaded user's, stands in only when the token
// will not say). The session is also written to the signed-out list BEFORE
// anything is posted - that is what reaches a tab that is not running, and it
// does not depend on there being a channel at all.
export function announceSignOut(loadedUserId) {
  const t = heldTail();
  const u = heldUserId() || (present(loadedUserId) ? String(loadedUserId) : '');
  rememberSignedOut(t, u);
  if (!channel || (!t && !u)) return;
  try {
    channel.postMessage({ type: 'signed-out', userId: u || null, tail: t });
  } catch { /* the other tabs keep their session; this one still ends */ }
}

// The session in this tab is dead: the server refused its refresh token, or the
// password changed under it. Tell the others, so a tab holding the same token
// drops it instead of handing it on to a tab that asks (borrowSession hears it
// too, if it comes while a tab is waiting), then clear this one. Not for a
// failure that says nothing about the session - a network drop, a 5xx while the
// demo wakes - which ends only this tab (api/client.js).
export function endSession() {
  const dead = heldTail();
  if (dead && channel) {
    try {
      channel.postMessage({ type: 'ended', tail: dead });
    } catch { /* the others find out when the server refuses them too */ }
  }
  clearTokens();
}

// The news from other tabs: somebody signed out, or a session died. Only those
// two kinds reach the handler, which decides whether they concern this tab.
export function onSessionMessage(handler) {
  if (!channel) return () => {};
  const onMessage = (event) => {
    const d = event.data;
    if (d && (d.type === 'signed-out' || d.type === 'ended')) handler(d);
  };
  channel.addEventListener('message', onMessage);
  return () => channel.removeEventListener('message', onMessage);
}
