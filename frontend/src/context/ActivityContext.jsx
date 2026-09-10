import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import api from '../api/client';
import { getAccess } from '../api/session';
import { useAuth } from './AuthContext';

const ActivityContext = createContext(null);

/* Which notifications a person has already looked at.
 *
 * Per account, and that is the whole point. This used to be one unkeyed slot,
 * `lastSeenActivityAt`, shared by every account that ever signed in on the
 * machine — and these are shared office workstations. So a psychologist
 * opening the bell marked the notifications seen for the administrator who
 * signed in after her: same browser, same key. The badge read zero while there
 * were unread items behind it, which is worse than a wrong number, because a
 * bell showing nothing is not something anyone thinks to check.
 *
 * The events themselves were never the problem — /api/activity/ is scoped by
 * role on the server. It was only ever the marker that was shared.
 *
 * Deliberately NOT cleared on sign-out, unlike the intake drafts next to it in
 * AuthContext: a draft holds case data and must not outlive the session, while
 * this is a timestamp, and dropping it would show every notification as unread
 * again at each sign-in.
 */
const seenKey = (userId) => `nacc-activity-seen:${userId}`;

function readSeen(userId) {
  if (!userId) return '';
  try {
    return localStorage.getItem(seenKey(userId)) || '';
  } catch {
    return '';   // private browsing, or storage disabled
  }
}

export function ActivityProvider({ children }) {
  const { user } = useAuth();
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [lastSeen, setLastSeen] = useState('');

  // Re-read whenever the account changes. Reading once at mount was the
  // second half of the same bug: even with a per-account key, signing out and
  // back in as somebody else on the same page kept the previous person's
  // marker in React state.
  useEffect(() => {
    // Sweep the shared key this replaced, so it does not sit in every
    // colleague's browser forever with nothing reading it.
    try { localStorage.removeItem('lastSeenActivityAt'); } catch { /* no storage */ }
    setLastSeen(readSeen(user?.id));
  }, [user?.id]);

  const refresh = useCallback(() => {
    if (!getAccess()) return;
    setLoading(true);
    api.get('/activity/')
      .then((r) => setEvents(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (user) refresh();
    else setEvents([]);
  }, [user, refresh]);

  const unreadCount = events.filter(
    (e) => !lastSeen || new Date(e.created_at) > new Date(lastSeen)
  ).length;

  const markSeen = useCallback(() => {
    if (!user?.id) return;
    // The newest event actually shown, not the clock. Marking "now" also
    // swallowed anything created between the last poll and the click — read
    // without ever being on screen.
    const newest = events.reduce(
      (max, e) => (max && max >= e.created_at ? max : e.created_at), '');
    const mark = newest || new Date().toISOString();
    try { localStorage.setItem(seenKey(user.id), mark); }
    catch { /* private browsing — the badge just will not persist */ }
    setLastSeen(mark);
  }, [user?.id, events]);

  return (
    <ActivityContext.Provider value={{ events, loading, refresh, unreadCount, markSeen }}>
      {children}
    </ActivityContext.Provider>
  );
}

export function useActivity() {
  return useContext(ActivityContext)
    || { events: [], loading: false, refresh: () => {}, unreadCount: 0, markSeen: () => {} };
}
