import { createContext, useContext, useEffect, useState } from 'react';
import api from '../api/client';
import { clearTokens, getAccess, setTokens } from '../api/session';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getAccess();
    if (!token) { setLoading(false); return; }
    api.get('/auth/me/')
      .then((res) => setUser(res.data))
      .catch(() => clearTokens())
      .finally(() => setLoading(false));
  }, []);

  // Both sign-in paths return the same {access, refresh, user} envelope, so
  // the session is stored identically however the user got here.
  const storeSession = (data) => {
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

  const logout = () => {
    clearTokens();
    // Clear any unsaved intake drafts (keyed per-user) so they never leak to
    // whichever account logs in next on this workstation.
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith('nacc-child-draft:'))
        .forEach((k) => localStorage.removeItem(k));
    } catch { /* private browsing / storage unavailable */ }
    setUser(null);
  };

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
