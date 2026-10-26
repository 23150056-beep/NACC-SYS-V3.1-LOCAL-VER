import axios from 'axios';
import { clearTokens, endSession, getAccess, getRefresh, heldTail, setTokens, tail } from './session';

// Exported for the one call that cannot go through axios: a request sent as the
// page is being closed (api/assignment.js), which needs fetch's keepalive.
export const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

const api = axios.create({ baseURL });

// The hosted demo's server sleeps when nobody has used it for a while, and
// waking it takes up to a minute (migrate and the address steps run before it
// answers). So the pages someone signs in from call this on open: the server
// is waking while they type, and the sign-in request finds it up instead of
// being the thing that wakes it. /healthz/ sits beside /api, not under it, and
// reads the database too, which wakes that as well. Fire and forget: the
// answer is not wanted, a failure is nobody's business, and a page that asks
// twice (Strict Mode, or sign-in then sign-up) still sends one request.
const serverOrigin = baseURL.replace(/\/api\/?$/, '');
let wakeSent = false;

export function wakeServer() {
  if (wakeSent) return;
  wakeSent = true;
  try {
    fetch(`${serverOrigin}/healthz/`, { cache: 'no-store' }).catch(() => {});
  } catch { /* never in the way of signing in */ }
}

api.interceptors.request.use((config) => {
  const token = getAccess();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  // Which session this request was sent with: the end of the refresh token the
  // tab held at this moment. A refusal acts only on that session (below) - the
  // tab may have signed in as somebody else by the time the answer comes back.
  config._tail = heldTail();
  return config;
});

// The refresh in flight, with the refresh token it was sent with: every request
// that fails while it is out waits on the same one.
let refreshing = null;

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    const refresh = getRefresh();
    // A 401 raised because the account has a forced password change pending
    // (see accounts/authentication.py) is not an expired/invalid session —
    // refreshing the access token won't help, since the new token is blocked
    // the same way. Reject as-is so the change-password gate can show its own
    // error instead of triggering the logout/redirect below.
    const isPasswordChangeRequired = error.response?.status === 401
      && String(error.response?.data?.detail || '').includes('must change your password');
    if (isPasswordChangeRequired) return Promise.reject(error);
    // The password changed under this session, so every token it holds is
    // dead and refreshing mints another dead one. Straight to sign-in.
    // endSession, not clearTokens: other tabs may hold the same dead tokens, and
    // are told, so none of them hands the session on to a tab that asks
    // (api/session.js). But only if it is still the session the request was
    // sent with: a reply about the old session, arriving after the tab signed
    // in again, must not end the new one.
    if (error.response?.status === 401
        && String(error.response?.data?.detail || '').includes('password was changed')) {
      if (heldTail() === original._tail) {
        endSession();
        window.location.href = '/login';
      }
      return Promise.reject(error);
    }
    // Refreshed and retried only if the session the request was SENT with is
    // still the one the tab holds. A reply that arrives after the tab signed in
    // as somebody else is about the old session: refreshing the new one and
    // sending the request again would put A's save into B's account.
    if (error.response?.status === 401 && refresh && tail(refresh) === original._tail
        && !original._retry) {
      original._retry = true;
      // The refresh token this refresh is for, kept outside the try so the
      // catch can tell whether the session it was about is still the tab's.
      let sent = refresh;
      try {
        refreshing = refreshing || { sent: refresh, promise: axios.post(`${baseURL}/auth/refresh/`, { refresh }) };
        const inFlight = refreshing;
        sent = inFlight.sent;
        const { data } = await inFlight.promise;
        refreshing = null;
        // Another tab may have signed this session out while the refresh was
        // on its way. The server never revokes a token, so the answer is a
        // good one - and storing it would bring the session back to life in a
        // tab that was told to leave. Not stored, and not retried.
        if (getRefresh() !== sent) return Promise.reject(error);
        setTokens(data.access);
        original.headers.Authorization = `Bearer ${data.access}`;
        return api(original);
      } catch (e) {
        refreshing = null;
        // The session this was about is already gone - signed out, or signed in
        // again as somebody else - and a refusal of its token says nothing about
        // the one the tab holds now. Reject; touch nothing.
        if (getRefresh() !== sent) return Promise.reject(error);
        // Only the server refusing the refresh token says the session is over,
        // and then it is over for every tab holding it. No answer, or a 5xx
        // while the demo wakes, says nothing about the session: that ends this
        // tab's and tells nobody, or one hiccup would sign out every tab of a
        // session that is still good.
        if (e.response?.status === 401) endSession();
        else clearTokens();
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

export default api;
