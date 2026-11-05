import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import api from '../api/client';
import {
  announceSignOut, answerAsks, borrowSession, clearTokens, forgetSignedOutUser, getAccess,
  getRefresh, heldSessionSignedOut, heldTail, heldUserId, onSessionMessage, setTokens,
  takeForUser, userIdOf,
} from '../api/session';
import { forgetRecentChildren } from '../utils/search';

const AuthContext = createContext(null);

// A tab with no session may borrow one from another tab of this browser
// (api/session.js) - except on the pages that are not for a signed-in person.
// /login is for signing in as oneself, perhaps as somebody other than the
// account open in the next tab, and a tab sent there because its session died
// must not come up as whoever else is signed in. A child fills in
// /survey/<token> with no account, and a staff token must not ride along on its
// requests. /signup is an applicant's page.
//
// Matched on the decoded, lower-cased path, because the router matches that
// way: /%6Cogin and /LOGIN reach the sign-in page too. decodeURIComponent, not
// decodeURI, because it is what the router decodes each segment with - and it
// also turns an encoded slash back into a slash, which the router keeps inside
// its segment, so this can refuse a path the router would not take for one of
// these pages but never the other way round. A path that cannot be decoded
// does not borrow.
function mayBorrowOn(pathname) {
  let p;
  try {
    p = decodeURIComponent(pathname).toLowerCase();
  } catch {
    return false;
  }
  return !/^\/(login|signup|survey)(\/|$)/.test(p);
}

// Whose session an email button asks for: `for=<user id>` on the address
// (accounts/email_notifications.py), as a plain number (`01` is `1`; only digits
// count, anything else is ignored as if the link had no `for`). Read ONCE, here,
// as this module is first evaluated, and taken off the address at the same
// moment (api/session.js takeForUser). That has to come before the router
// reads the location: App imports this file before anything renders, and the
// router is the first thing to look at the address. It is not a state
// initializer for the same reason - Strict Mode runs those twice, and the
// second run would find the address already tidied. So a bookmark or a copied
// address never carries it, and a colleague's tab is not restricted by
// somebody else's link.
const FOR_USER_AT_START = takeForUser();

// Whether this page load began on a session that was signed out while the tab
// was not running (discarded to save memory, say): whatever it still holds is
// dead - by its own refresh token or by its account - and nothing may use it,
// or answer for it, first. It is dropped here, with this tab's own Recent list
// (the drafts in localStorage went when the other tab left), and the tab then
// does not borrow in this page load either: it was somebody's tab, and it must
// not come up as whoever else is signed in. The form shows.
//
// Decided ONCE, as the module loads, for the reason FOR_USER_AT_START is: React's
// development mode (what run-local.bat serves) runs the start-up effect twice.
// A `dropped` kept inside the effect was forgotten by the second run, which
// found the tokens already gone and borrowed somebody else's session. Every
// storage access is guarded, because blocked site data must not crash the
// import: a tab that cannot be told is no worse off than before there was a list.
function dropSignedOutSessionAtStart() {
  let dead = false;
  try {
    dead = heldSessionSignedOut();
  } catch {
    return false;
  }
  if (!dead) return false;
  try { clearTokens(); } catch { /* storage blocked: there is nothing to clear */ }
  try { forgetRecentChildren(); } catch { /* ditto */ }
  return true;
}
const SESSION_DROPPED_AT_START = dropSignedOutSessionAtStart();

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  // Who is signed in, for the listeners below, which are installed once and
  // must read the CURRENT user when a message arrives, not the one at install.
  const userRef = useRef(null);
  useEffect(() => { userRef.current = user; }, [user]);

  useEffect(() => {
    // Strict Mode (development only) runs this twice, and cleans up the first
    // run before the second starts: `live` keeps that run from setting state
    // after the second has begun. Its borrow is not cancelled with it and may
    // still store the tokens it is given. That is harmless, though the two runs
    // need not be answered by the same session - two tabs of one account that
    // signed in separately can each answer one of them: the second run asks
    // after the first, so its answer is stored last, and it reads `sent` only
    // after that, so the session it asks /auth/me/ about is the one it holds.
    // The first run goes no further once it has been cleaned up.
    let live = true;
    (async () => {
      try {
        // A session signed out while this tab was not running was already
        // dropped as the module loaded (SESSION_DROPPED_AT_START), and then this
        // page load never borrows: the second run of Strict Mode's double start-up
        // would otherwise find no tokens and borrow.
        //
        // The provider sits outside the router, so the address is the page's own.
        // Set only when this tab borrowed a session for one person, whose
        // session /auth/me/ must then turn out to be.
        let borrowedFor = null;
        if (!SESSION_DROPPED_AT_START && !getAccess() && mayBorrowOn(window.location.pathname)) {
          const borrowed = await borrowSession({ forUser: FOR_USER_AT_START });
          if (!live) return;
          if (borrowed) borrowedFor = FOR_USER_AT_START;
        }
        if (!getAccess()) return;
        // The reply is applied only if this tab still holds the session it was
        // asked with: a sign-out that lands while it is on its way must not be
        // undone by it. The refresh token says which session; the access token
        // alone cannot, because the client swaps it for a new one when it has
        // expired (api/client.js), and that is the same session.
        const sent = getRefresh();
        const stillThatSession = () => Boolean(getAccess()) && getRefresh() === sent;
        try {
          const res = await api.get('/auth/me/');
          if (live && stillThatSession()) {
            // A link for one person must not leave this tab as somebody else:
            // the share was labelled with that person, but the server decides.
            if (borrowedFor !== null && String(res.data?.id) !== borrowedFor) clearTokens();
            else setUser(res.data);
          }
        } catch {
          if (stillThatSession()) clearTokens();
        }
      } catch {
        /* sessionStorage blocked: there is no session to restore */
      } finally {
        // Always, or the screen hangs on "Connecting".
        if (live) setLoading(false);
      }
    })();
    return () => { live = false; };
  }, []);

  // Hand this tab's session to a tab that asks for one. userRef is null until
  // /auth/me/ has answered, so a tab that is still loading says nothing. After
  // a sign-out it is the cleared tokens that keep a tab quiet, not userRef,
  // which only follows setUser a render later. The rest of who may answer is
  // decided in api/session.js, which also labels the share from the token.
  useEffect(() => answerAsks(() => userRef.current), []);

  // Both sign-in paths return the same {access, refresh, user} envelope, so
  // the session is stored identically however the user got here. Signing in
  // again ends the person's earlier sign-out: their entries come off the
  // signed-out list first, so the new session is not taken for the old one.
  const storeSession = (data) => {
    forgetSignedOutUser(userIdOf(data.refresh) || data.user?.id);
    setTokens(data.access, data.refresh);
    setUser(data.user);
    return data.user;
  };

  const login = async (email, password) => {
    const { data } = await api.post('/auth/login/', { email, password });
    return storeSession(data);
  };

  // Google Sign-In: `credential` is the ID token from Google Identity
  // Services. The server verifies it and decides whether it maps to an
  // existing staff or psychologist account — this never creates one.
  // `requestedRole` is only ever sent on the second call, after a first-time
  // user has told us what they do. It is a claim recorded against their
  // request — the server never treats it as a grant.
  const loginWithGoogle = async (credential, requestedRole = null) => {
    const body = requestedRole ? { credential, requested_role: requestedRole } : { credential };
    const { data } = await api.post('/auth/google/', body);
    return storeSession(data);
  };

  // Signing out in THIS tab: the full clean-up. Only the tab the person signed
  // out in runs it (logout, below).
  const signOutHere = useCallback(() => {
    clearTokens();
    // Clear any unsaved intake drafts (keyed per-user) so they never leak to
    // whichever account logs in next on this workstation. `nacc-draft:` is the
    // case study's unsaved text (components/caseStudy/drafts.js), which can
    // hold an adoptive parent's income and must not outlive the session.
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith('nacc-child-draft:') || k.startsWith('nacc-draft:'))
        .forEach((k) => localStorage.removeItem(k));
    } catch { /* private browsing / storage unavailable */ }
    // And the search box's "Recent" list, for the same reason.
    forgetRecentChildren();
    setUser(null);
  }, []);

  // Signing out signs out every tab holding this session: a tab that borrowed
  // it would otherwise carry on after the person thought they had left. The
  // account's id is the one the session's own token names (it falls back to the
  // loaded user's only if the token will not say). They do not announce it
  // again - the one that signed out already did.
  const logout = () => {
    announceSignOut(userRef.current?.id);
    signOutHere();
  };

  // What a tab does when ANOTHER tab tells it that its session is over: its
  // tokens, its own Recent list and its user go, and it stops waiting if it was
  // still loading. It does NOT sweep the drafts in localStorage. The tab that
  // signed out already did, and a message that reaches a tab late (it was
  // frozen) would delete what the next person to sign in has typed since.
  // user becomes null, and ProtectedRoute sends the tab to the sign-in page by
  // itself.
  const dropSession = useCallback(() => {
    clearTokens();
    forgetRecentChildren();
    setUser(null);
    setLoading(false);
  }, []);

  // What other tabs say. A sign-out matters to a tab holding the same session
  // (the refresh token's tail) or signed in as the same account (a tab that
  // signed in separately); both are read off the tab's own tokens, so they work
  // while its user is still loading. Two people can share a browser, and a tab
  // signed in as somebody else is left alone. A dead session matters only to a
  // tab holding that very refresh token.
  useEffect(() => onSessionMessage((msg) => {
    const sameSession = Boolean(msg.tail) && msg.tail === heldTail();
    if (msg.type === 'signed-out') {
      const sameAccount = Boolean(msg.userId) && String(msg.userId) === heldUserId();
      if (sameSession || sameAccount) dropSession();
    } else if (msg.type === 'ended') {
      if (sameSession) dropSession();
    }
  }), [dropSession]);

  // A page brought back from the back/forward cache is not started again, so
  // nothing above runs for it: it can hold a session that was signed out while
  // it was away. The signed-out list, in localStorage, says so. The same list is
  // read when the tab becomes visible again, in case a frozen tab missed the
  // message and was not restored from the cache either. Installed once and
  // removed with the provider.
  useEffect(() => {
    const onPageShow = (event) => {
      if (event.persisted && heldSessionSignedOut()) dropSession();
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible' && heldSessionSignedOut()) dropSession();
    };
    window.addEventListener('pageshow', onPageShow);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('pageshow', onPageShow);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [dropSession]);

  // Shallow-merges into the stored user (e.g. clearing must_change_password
  // after a successful change) without a round-trip to /auth/me/.
  const updateUser = (patch) => setUser((u) => (u ? { ...u, ...patch } : u));

  return (
    <AuthContext.Provider value={{ user, loading, login, loginWithGoogle, logout, updateUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
