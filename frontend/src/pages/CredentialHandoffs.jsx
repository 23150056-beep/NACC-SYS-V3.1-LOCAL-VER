import { useEffect, useState } from 'react';
import api from '../api/client';
import { useToast } from '../context/ToastContext';
import { Avatar, Badge, Button, EmptyState, Icon, RoleBadge, TOOLBAR } from '../ui';

// Admin-only list of everyone still on a temporary password (must_change_password),
// i.e. every account whose credentials still need to be handed over physically.
// Passwords are generated fresh at the moment of handoff (via the existing
// reset-password endpoint) and live ONLY in this component's state until the
// page is refreshed — the server never stores a plaintext password.
// Rendered as a tab inside User Management (Users.jsx), not its own route.
export default function CredentialHandoffs() {
  const toast = useToast();
  const [users, setUsers] = useState([]);
  const [generated, setGenerated] = useState({}); // { userId: tempPassword }
  // Whether the server queued an email for each generated password. The
  // endpoint has always returned this; this screen used to drop it, so an
  // administrator generating from here could not tell whether the person had
  // been emailed or was waiting to be told in person.
  const [mailed, setMailed] = useState({});       // { userId: bool }
  const [busy, setBusy] = useState(false);

  const load = () => api.get('/users/').then((r) => setUsers(r.data.filter((u) => u.must_change_password)));
  useEffect(() => { load(); }, []);

  const generate = async (u) => {
    try {
      const { data } = await api.post(`/users/${u.id}/reset-password/`);
      setGenerated((g) => ({ ...g, [u.id]: data.temp_password }));
      setMailed((m) => ({ ...m, [u.id]: Boolean(data.email_queued) }));
      return true;
    } catch (err) {
      toast.error(err.response?.data?.detail || `Could not generate a password for ${u.fullname || u.email}.`);
      return false;
    }
  };

  const generateAll = async () => {
    setBusy(true);
    // Sequential on purpose: predictable order, no burst of parallel writes.
    for (const u of users) await generate(u);
    setBusy(false);
  };

  const copyPassword = (u) => {
    navigator.clipboard.writeText(generated[u.id]);
    toast.success(`Password for ${u.fullname || u.email} copied.`);
  };

  const slips = users.filter((u) => generated[u.id]);

  return (
    <>
      <div className="racco-no-print">
        <div style={{ padding: '12px 15px', display: 'flex', gap: 11, background: 'var(--warning-50)', borderBottom: '1px solid var(--warning-100)' }}>
          <Icon name="key-round" size={19} style={{ color: 'var(--warning-700)', flex: 'none', marginTop: 1 }} />
          <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-body)' }}>
            <strong style={{ color: 'var(--text-strong)' }}>A handoff is an open loop, not a record.</strong> Generate
            a fresh temporary password at the moment you hand it over — it exists only on this screen until you
            refresh, and the system never stores it. The row stays here until they sign in and set their own, so an
            unclaimed credential cannot sit forgotten.
          </p>
        </div>

        {users.length === 0 ? (
          <EmptyState icon={<Icon name="check-circle-2" size={24} />} title="No pending handoffs" description="Everyone has set their own password. New accounts will appear here automatically." />
        ) : (
          <>
            <div style={TOOLBAR}>
              <span style={{ flex: 1, fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>
                {users.length} account{users.length === 1 ? '' : 's'} still on a temporary password
              </span>
              <Button variant="secondary" size="sm" disabled={busy} onClick={generateAll} iconLeft={<Icon name="key-round" size={15} />}>
                {busy ? 'Generating…' : 'Generate all'}
              </Button>
              <Button variant="primary" size="sm" disabled={slips.length === 0} onClick={() => window.print()} iconLeft={<Icon name="printer" size={15} />}>
                Print slips{slips.length > 0 ? ` (${slips.length})` : ''}
              </Button>
            </div>

            <div style={{ padding: '14px 15px', display: 'flex', flexDirection: 'column', gap: 12 }}>
              {users.map((u) => (
                <div key={u.id} style={{ border: '1px solid var(--border)', borderRadius: 11, overflow: 'hidden' }}>
                  <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                    <Avatar name={u.fullname || u.email} tone="amber" size={38} />
                    <span style={{ flex: 1, minWidth: 160 }}>
                      <span style={{ display: 'block', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>{u.fullname || u.username}</span>
                      <span className="racco-mono" style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>{u.email}</span>
                    </span>
                    {u.role_name ? <RoleBadge role={u.role_name} size="sm" /> : null}
                    <Badge tone={generated[u.id] ? 'success' : 'warning'} size="sm" dot>
                      {generated[u.id] ? 'Password generated' : 'Awaiting handoff'}
                    </Badge>
                  </div>

                  <div style={{ padding: '11px 14px', background: 'var(--ink-25)', borderTop: '1px solid var(--divider)', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                    <span style={{ flex: 1, minWidth: 200 }}>
                      {generated[u.id] ? (
                        <>
                          <span className="racco-mono" style={{ display: 'block', fontSize: 15, fontWeight: 700, color: 'var(--text-strong)', letterSpacing: '0.04em' }}>{generated[u.id]}</span>
                          {/* Whether this person was emailed decides whether the
                              password still has to be handed over by hand. */}
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 3, fontSize: 11.5, color: mailed[u.id] ? 'var(--success-700)' : 'var(--warning-700)' }}>
                            <Icon name={mailed[u.id] ? 'mail' : 'mail-x'} size={13} />
                            {mailed[u.id] ? `Emailed to ${u.email}` : 'Not emailed — hand this over yourself'}
                          </span>
                        </>
                      ) : (
                        <span style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-muted)' }}>
                          No password on screen. Generate one when you are with them, not before.
                        </span>
                      )}
                    </span>
                    <div style={{ display: 'flex', gap: 8, flex: 'none' }}>
                      <Button variant="secondary" size="sm" onClick={() => generate(u)} iconLeft={<Icon name={generated[u.id] ? 'rotate-ccw' : 'key-round'} size={15} />}>
                        {generated[u.id] ? 'Re-issue' : 'Generate'}
                      </Button>
                      {generated[u.id] && (
                        <Button variant="primary" size="sm" onClick={() => copyPassword(u)} iconLeft={<Icon name="copy" size={15} />}>Copy</Button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Printable slips — one cut-out per generated password. Visible only in
          print (racco-print-only); index.css already hides the app chrome. */}
      <div className="racco-print-only" style={{ padding: 8 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          {slips.map((u) => (
            <div key={u.id} style={{ border: '1.5px dashed #94a3b8', borderRadius: 8, padding: '14px 16px', breakInside: 'avoid' }}>
              <div style={{ fontWeight: 800, fontSize: 13, color: '#1d4ed8' }}>NACC – RACCO 1</div>
              <div style={{ fontSize: 10.5, color: '#64748b', marginBottom: 8 }}>Temporary account credentials — keep this slip private</div>
              <div style={{ fontWeight: 700, fontSize: 14 }}>{u.fullname || u.username}</div>
              <div style={{ fontFamily: 'monospace', fontSize: 12, color: '#334155' }}>{u.email}</div>
              <div style={{ fontFamily: 'monospace', fontSize: 17, fontWeight: 700, letterSpacing: '0.06em', margin: '8px 0', padding: '6px 10px', border: '1px solid #cbd5e1', borderRadius: 6, display: 'inline-block' }}>
                {generated[u.id]}
              </div>
              <div style={{ fontSize: 10.5, color: '#64748b', lineHeight: 1.5 }}>
                Sign in with this password — you will be required to set your own
                password immediately. Destroy this slip after use.
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
