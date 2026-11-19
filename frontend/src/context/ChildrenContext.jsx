import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import api from '../api/client';
import { getAccess } from '../api/session';
import { useAuth } from './AuthContext';

/* One copy of the child list for every screen that reads it.
 *
 * Records, the calendar, Results & Reports, the pre-assessment picker, My
 * Profile and the header search each asked the server for the whole list on
 * every visit, so each switch of screen began with an empty table. This keeps
 * the last answer and hands it over at once; each screen still asks again as
 * it opens (stale-while-revalidate), so what shows is never older than the
 * time it takes one request to answer.
 *
 * Memory only. Office computers are shared and the list holds names, so
 * nothing is written to localStorage or sessionStorage.
 *
 * The copy belongs to one account. It is compared with the signed-in user's id
 * when it is READ, not in an effect, so there is no render in which one
 * account's children are handed to another (a sign-out sets the user to null,
 * a different sign-in changes the id; both read as "nothing yet").
 *
 * It holds the list WITH closed cases (the server's include_archived=true).
 * The server's plain list is exactly that minus the inactive ones, so a screen
 * that wants only open cases filters here with `status !== 'inactive'`, or
 * `status === 'active'`, itself: filtering stays per screen.
 */
const URL = '/children/?include_archived=true';
const NONE = Object.freeze([]);

const ChildrenContext = createContext(null);

export function ChildrenProvider({ children: tree }) {
  const { user } = useAuth();
  const uid = user?.id ?? null;
  const [state, setState] = useState({ uid: null, list: null });

  const uidRef = useRef(uid);
  const prevUid = useRef(uid);
  const seq = useRef(0);
  const inflight = useRef(null);
  uidRef.current = uid;
  if (prevUid.current !== uid) {
    // A different account (or none): an answer still on its way was asked for
    // the old one and must not be applied.
    prevUid.current = uid;
    seq.current += 1;
    inflight.current = null;
  }

  // Drop the previous account's list from memory as soon as the account
  // changes. It could never be shown to anyone else (the read below checks
  // the owner), but an office computer is shared and there is no reason to
  // keep it.
  useEffect(() => {
    setState((s) => (s.uid !== null && s.uid !== uid ? { uid: null, list: null } : s));
  }, [uid]);

  const fetchNow = useCallback(() => {
    const mine = ++seq.current;
    const forUid = uidRef.current;
    const p = api.get(URL)
      .then((r) => {
        // Only the newest request writes, and only for the account that asked.
        if (mine === seq.current && uidRef.current === forUid) {
          setState({ uid: forUid, list: Array.isArray(r.data) ? r.data : [] });
        }
      })
      .finally(() => { if (inflight.current === p) inflight.current = null; });
    inflight.current = p;
    return p;
  }, []);

  // Asked as a screen opens: joins a request already out. Rejects on failure
  // (the cached list is kept) so the screen can say so as it always did.
  const revalidate = useCallback(() => {
    if (!uidRef.current || !getAccess()) return Promise.resolve();
    return inflight.current || fetchNow();
  }, [fetchNow]);

  // Asked after a write that changes the list: never joins a request that was
  // sent before the write, since that answer may predate it.
  const refresh = useCallback(() => {
    if (!uidRef.current || !getAccess()) return Promise.resolve();
    return fetchNow();
  }, [fetchNow]);

  const list = state.uid === uid && uid !== null ? state.list : null;
  const value = useMemo(
    () => ({ list, revalidate, refresh }),
    [list, revalidate, refresh],
  );
  return <ChildrenContext.Provider value={value}>{tree}</ChildrenContext.Provider>;
}

/* `children` is [] until `loaded` is true: read `loaded` before treating an
 * empty list as "no children". Outside a provider it behaves as never loaded
 * and its calls do nothing. */
export function useChildren() {
  const ctx = useContext(ChildrenContext);
  return useMemo(() => ({
    children: ctx?.list ?? NONE,
    loaded: ctx?.list != null,
    revalidate: ctx?.revalidate ?? (() => Promise.resolve()),
    refresh: ctx?.refresh ?? (() => Promise.resolve()),
  }), [ctx]);
}
