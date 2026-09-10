import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import api from '../api/client';
import { getAccess } from '../api/session';
import { useAuth } from './AuthContext';

/* /reports/dashboard/ answers two screens at once: the Dashboard's own body
 * and the right rail's census, day and care-gap counts. Fetched here so the
 * two share one request and cannot disagree — the rail showing "9 care gaps"
 * beside a list of four was the previous arrangement's failure mode.
 *
 * The range control on the Dashboard lives here too, because changing it has
 * to re-fetch the figures the rail is reading.
 */
const EMPTY = {
  census: { active: 0, inactive: 0, by_case_type: {}, by_case_status: {} },
  total_children: 0, unassessed: 0, pending_pre_assessments: 0,
  today_schedule: [], availability_today: [], intake_vs_termination: [],
  trend: [], per_psychologist: [], by_case_type: {}, care_gaps: [],
  counseling_per_psychologist: [],
};

const CensusContext = createContext(null);

export function CensusProvider({ children }) {
  const { user } = useAuth();
  const [stats, setStats] = useState(EMPTY);
  const [range, setRange] = useState('monthly');
  const [loading, setLoading] = useState(true);
  const [pendingAccess, setPendingAccess] = useState(0);
  const isAdmin = user?.role_name === 'Administrator';

  const refresh = useCallback(() => {
    if (!getAccess()) return;
    setLoading(true);
    api.get(`/reports/dashboard/?range=${range}`)
      .then((r) => setStats({ ...EMPTY, ...r.data }))
      .catch(() => setStats(EMPTY))
      .finally(() => setLoading(false));
  }, [range]);

  useEffect(() => { if (user) refresh(); }, [user, refresh]);

  /* How many people are waiting to be let in. It lives here rather than on
   * the User Management screen because the badge that carries it — on the
   * header's Admin tab and on the rail's User Management row — has to be
   * right on every OTHER screen. A queue you only see once you open the queue
   * is a queue that sits. */
  const refreshPendingAccess = useCallback(() => {
    if (!isAdmin) { setPendingAccess(0); return; }
    api.get('/users/', { params: { include_archived: 'true' } })
      .then((r) => setPendingAccess((r.data || []).filter((u) => u.status === 'pending').length))
      .catch(() => {});
  }, [isAdmin]);

  useEffect(() => { if (user) refreshPendingAccess(); }, [user, refreshPendingAccess]);

  const value = useMemo(
    () => ({ stats, range, setRange, loading, refresh, pendingAccess, refreshPendingAccess }),
    [stats, range, loading, refresh, pendingAccess, refreshPendingAccess],
  );
  return <CensusContext.Provider value={value}>{children}</CensusContext.Provider>;
}

export function useCensus() {
  return useContext(CensusContext) || {
    stats: EMPTY, range: 'monthly', setRange: () => {}, loading: false, refresh: () => {},
    pendingAccess: 0, refreshPendingAccess: () => {},
  };
}

export const EMPTY_CENSUS = EMPTY;
