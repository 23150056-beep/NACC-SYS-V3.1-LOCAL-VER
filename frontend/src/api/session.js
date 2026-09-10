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
 * The trade, stated rather than discovered: sessionStorage is per-TAB. Opening
 * the app in a second tab means signing in there too. For a shared workstation
 * that is arguably the point — a colleague opening a new tab does not inherit
 * your session — but it is a real change in feel, not a free win.
 *
 * One module because api/client.js and context/AuthContext.jsx both touch
 * these keys, and two files disagreeing about where the token lives is a bug
 * that looks like a random logout.
 */
const ACCESS = 'access';
const REFRESH = 'refresh';

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
