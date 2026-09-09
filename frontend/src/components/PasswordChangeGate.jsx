import { useState } from 'react';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { Button, FormField, PasswordInput, Alert, Icon } from '../ui';

// Full-screen "set a new password" card, styled like the Login page's card
// views. Used in two places: right after a login whose response carries
// must_change_password (Login.jsx, current password prefilled from what the
// user just typed), and as a route-level gate on page refresh when /auth/me/
// reports must_change_password still set (ProtectedRoute.jsx, blank form).
// The server enforces the lockout independently (accounts/authentication.py);
// this is just the compliant path out of it.
export default function PasswordChangeGate({ prefillCurrent = '', title = 'Set a new password', subtitle, onDone }) {
  const { updateUser } = useAuth();
  const toast = useToast();
  const [current, setCurrent] = useState(prefillCurrent);
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (next.length < 8) { setError('New password must be at least 8 characters.'); return; }
    if (next !== confirm) { setError('Passwords do not match.'); return; }
    setBusy(true);
    try {
      await api.post('/auth/change-password/', { current_password: current, new_password: next });
      updateUser({ must_change_password: false });
      toast.success('Password updated.');
      if (onDone) onDone();
    } catch (err) {
      const data = err.response?.data || {};
      const msg = data.current_password || data.new_password || data.non_field_errors || data.detail
        || 'Could not update the password. Please try again.';
      setError(Array.isArray(msg) ? msg[0] : msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="racco-sky-wash" style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24, overflowY: 'auto' }}>
      <div style={{ width: 460, maxWidth: '100%', background: 'var(--surface)', borderRadius: 'var(--radius-2xl)', boxShadow: 'var(--shadow-xl)', padding: '40px 38px' }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 800, color: 'var(--text-strong)' }}>{title}</h1>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>
          {subtitle || 'Your account has a temporary password. Choose a new one to continue.'}
        </p>
        <form onSubmit={submit} style={{ marginTop: 22, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
          <FormField label="Current Password">
            <PasswordInput value={current} onChange={(e) => setCurrent(e.target.value.replace(/\s/g, ''))} placeholder="••••••••" leading={<Icon name="lock" size={16} />} required autoFocus={!prefillCurrent} />
          </FormField>
          <FormField label="New Password">
            <PasswordInput value={next} onChange={(e) => setNext(e.target.value.replace(/\s/g, ''))} placeholder="••••••••" leading={<Icon name="lock-keyhole" size={16} />} required autoFocus={!!prefillCurrent} />
          </FormField>
          <FormField label="Confirm New Password">
            <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value.replace(/\s/g, ''))} placeholder="••••••••" leading={<Icon name="lock-keyhole" size={16} />} required />
          </FormField>
          <Button type="submit" variant="primary" size="lg" fullWidth disabled={busy} iconRight={busy ? null : <Icon name="check" size={18} />}>
            {busy ? 'Updating…' : 'Set New Password'}
          </Button>
        </form>
      </div>
    </div>
  );
}
