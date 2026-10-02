import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Button, Icon } from '../ui';
import PasswordChangeGate from './PasswordChangeGate';

/* What shows while the app asks the server who is signed in.

   It was a bare "Loading…" in the corner, and on the hosted demo that can
   last a minute: Render's free plan puts the API to sleep after ~15 minutes
   idle, and waking it re-runs the start-up steps before it answers. A
   minute of "Loading…" reads as broken (owner, 30 Sep 2026). So after a
   few seconds it says what is happening and that nothing is lost, and after
   a long while it offers a way to try again. A quick answer never sees any
   of it. */
function WaitingForServer() {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  if (secs < 3) return <div style={{ padding: 24, color: 'var(--text-muted)' }}>Loading…</div>;
  const stuck = secs >= 75;
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24, background: 'var(--bg-app)' }}>
      <div role="status" aria-live="polite" style={{ maxWidth: 420, width: '100%', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-card)', padding: 24, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, textAlign: 'center', animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
        <span style={{ display: 'inline-flex', color: stuck ? 'var(--amber-600)' : 'var(--blue-600)', animation: stuck ? 'none' : 'racco-spin 1.1s linear infinite' }}>
          <Icon name={stuck ? 'wifi-off' : 'loader-2'} size={28} />
        </span>
        <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>
          {stuck ? 'Still waiting for the server' : 'Connecting to the server…'}
        </div>
        <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.55, color: 'var(--text-body)' }}>
          {stuck
            ? 'It is taking longer than usual. Check your connection, then try again. You are still signed in.'
            : 'The server sleeps when nobody has used it for a while, and takes up to a minute to wake up. You are still signed in; this page carries on by itself.'}
        </p>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{secs} seconds</span>
        {stuck && (
          <Button variant="primary" onClick={() => window.location.reload()} iconLeft={<Icon name="rotate-ccw" size={16} />}>
            Try again
          </Button>
        )}
      </div>
    </div>
  );
}

export default function ProtectedRoute({ children, roles }) {
  const { user, loading } = useAuth();
  if (loading) return <WaitingForServer />;
  if (!user) return <Navigate to="/login" replace />;
  // Covers the page-refresh case: /auth/me/ still reports an outstanding
  // admin-issued temporary password, so every route renders the change gate
  // instead of its normal content until it's cleared.
  if (user.must_change_password) {
    return (
      <PasswordChangeGate
        subtitle="Your password was reset by an administrator. Set a new one to continue."
      />
    );
  }
  if (roles && !roles.includes(user.role_name)) return <Navigate to="/" replace />;
  return children;
}
