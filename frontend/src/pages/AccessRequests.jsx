import { useEffect, useState } from 'react';
import api from '../api/client';
import { exactDate, timeAgo } from '../utils/time';
import { useActivity } from '../context/ActivityContext';
import { useToast } from '../context/ToastContext';
import {
  Alert, Avatar, Button, ConfirmDialog, EmptyState, FormField, Icon, ROLE_ACCESS,
  RoleAccessPanel, Select, Skeleton,
} from '../ui';

// People who asked for access and are waiting on a decision. Rendered as a
// tab inside User Management (Users.jsx), not its own route.
//
// Two doors lead here — the Google button and the sign-up form at /signup —
// and this screen is the only access control standing behind either. Sign-up
// is open to anyone on the internet, so nothing separates a stranger from
// child case records except an administrator reading a row and clicking
// Approve. Everything here is built around making that click deliberate
// rather than quick, and that now includes saying which door they used: a
// Google address was verified by Google, a typed one was verified by nobody.


const nameOf = (u) => (u.fullname || u.username || u.email || '');

/* Which door, and what it is worth.
 *
 * Google verified the address it handed over (the flow rejects an unverified
 * one). The sign-up form verifies nothing — no confirmation mail is sent, so
 * the address is only what someone typed. An approver deciding whether they
 * recognise this person should not have to guess which of the two they are
 * looking at. */
function DoorChip({ google }) {
  const [label, hint, icon, color] = google
    ? ['Google', 'Address verified by Google.', 'badge-check', 'var(--blue-600)']
    : ['Typed in', 'Signed up with the form. Nobody has verified this address.',
       'keyboard', 'var(--text-muted)'];
  return (
    <span title={hint}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 3,
                   fontSize: 11, fontWeight: 700, color, whiteSpace: 'nowrap' }}>
      <Icon name={icon} size={12} /> {label}
    </span>
  );
}

/* The claimed role, styled so it cannot be mistaken for a settled one.
 * Dashed and quiet, deliberately unlike the solid RoleBadge used everywhere
 * else: if this read as a fact, an administrator skimming the queue would
 * rubber-stamp it, which defeats the entire gate. */
function ClaimChip({ role }) {
  if (!role) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text-faint)', fontStyle: 'italic' }}>
        didn’t say
      </span>
    );
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 'var(--radius-pill)', border: '1px dashed var(--border-strong)', background: 'transparent', fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
      <Icon name="quote" size={11} style={{ opacity: 0.6 }} />
      asks to be <strong style={{ color: 'var(--text-body)', fontWeight: 700 }}>{role}</strong>
    </span>
  );
}

export default function AccessRequests({ onChange }) {
  const toast = useToast();
  const { refresh: refreshActivity } = useActivity();
  const [rows, setRows] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [confirm, setConfirm] = useState(null);   // { kind, user }
  const [grantRole, setGrantRole] = useState('');
  const [picked, setPicked] = useState({});   // user id -> role id chosen in the row
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const r = await api.get('/users/', { params: { include_archived: 'true' } });
      setRows(r.data.filter((u) => u.status === 'pending'));
      setLoadError('');
    } catch (err) {
      setLoadError(err.response?.data?.detail || 'Could not load access requests.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // Administrator is filtered out: approving cannot create one, and the
    // server refuses it anyway. Offering it here would only produce an error.
    api.get('/roles/')
      .then((r) => setRoles(r.data.filter((x) => x.role_name !== 'Administrator')))
      .catch(() => setRoles([]));
  }, []);

  const openApprove = (u, roleId) => {
    // Whatever the row's picker is showing, which is pre-filled from the claim
    // — but the administrator's submission is what the server acts on, and it
    // refuses to act at all without one.
    setGrantRole(roleId ? String(roleId) : (u.requested_role ? String(u.requested_role) : ''));
    setConfirm({ kind: 'approve', user: u });
  };

  const run = async () => {
    const { kind, user: u } = confirm;
    setBusy(true);
    try {
      if (kind === 'approve') {
        await api.post(`/users/${u.id}/approve/`, { role: grantRole });
        const granted = roles.find((r) => String(r.id) === String(grantRole))?.role_name;
        toast.success(`${nameOf(u)} approved as ${granted}`);
      } else {
        await api.post(`/users/${u.id}/decline/`);
        toast.success(`Request from ${nameOf(u)} declined`);
      }
      setConfirm(null);
      await load();
      refreshActivity();
      onChange?.();
    } catch (err) {
      const body = err.response?.data;
      toast.error(body?.role || body?.detail || 'That did not go through. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const granting = confirm?.kind === 'approve'
    ? roles.find((r) => String(r.id) === String(grantRole))
    : null;
  const claimed = confirm?.user?.requested_role_name || null;

  // Pre-filled from the claim so the queue moves quickly, but it is a form
  // field the administrator has to look at rather than a default that acts on
  // its own — approve/ on the server has no fallback to the claim either.
  const pickedFor = (u) => picked[u.id] ?? (roles.find((r) => r.role_name === u.requested_role_name)?.id ?? '');
  const nameOfRole = (id) => roles.find((r) => String(r.id) === String(id))?.role_name || '';

  return (
    <>
      <div style={{ padding: '12px 15px', display: 'flex', gap: 11, background: 'var(--warning-50)', borderBottom: '1px solid var(--warning-100)' }}>
        <Icon name="shield-alert" size={19} style={{ color: 'var(--warning-700)', flex: 'none', marginTop: 1 }} />
        <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-body)' }}>
          <strong style={{ color: 'var(--text-strong)' }}>Read each row before you approve it.</strong> Anyone on the
          internet can send a request — with a Google account or the sign-up form. A stated role is a claim, not a
          grant; you assign the real one. Approving cannot create another Administrator, and declining archives the
          address so it cannot ask again.
        </p>
      </div>

      {loadError ? (
        <div style={{ padding: 20 }}>
          <Alert tone="danger" title="Requests could not be loaded" icon={<Icon name="wifi-off" size={18} />}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
              <span>{loadError} Nothing has been changed.</span>
              <Button variant="secondary" size="sm" onClick={() => { setLoading(true); load(); }} iconLeft={<Icon name="refresh-cw" size={15} />}>Try again</Button>
            </div>
          </Alert>
        </div>
      ) : loading ? (
        <div style={{ padding: '14px 15px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={`sk-${i}`} style={{ border: '1px solid var(--border)', borderRadius: 11, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12 }}>
              <Skeleton width={38} height={38} radius="50%" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7, flex: 1 }}>
                <Skeleton width="38%" height={12} />
                <Skeleton width="58%" height={10} />
              </div>
              <Skeleton width={124} height={20} radius="var(--radius-pill)" />
            </div>
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<Icon name="inbox" size={24} />}
          title="No one is waiting"
          description="When a staff member or psychologist asks for access — with Google or the sign-up form — their request appears here for you to approve."
        />
      ) : (
        <div style={{ padding: '14px 15px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {rows.map((u) => {
            const pick = pickedFor(u);
            const grantName = nameOfRole(pick);
            const gains = ROLE_ACCESS[grantName] || [];
            return (
              <div key={u.id} style={{ border: '1px solid var(--border)', borderRadius: 11, overflow: 'hidden' }}>
                <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                  <Avatar name={nameOf(u)} tone="neutral" size={38} />
                  <span style={{ flex: 1, minWidth: 160 }}>
                    <span style={{ display: 'block', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>{nameOf(u)}</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2, flexWrap: 'wrap' }}>
                      <span className="racco-mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>{u.email}</span>
                      <DoorChip google={!!u.google_linked} />
                    </span>
                  </span>
                  <ClaimChip role={u.requested_role_name} />
                  <span style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-faint)', flex: 'none' }} title={exactDate(u.created_at)}>{timeAgo(u.created_at)}</span>
                </div>

                <div style={{ padding: '11px 14px', background: 'var(--ink-25)', borderTop: '1px solid var(--divider)', display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <span className="racco-eyebrow" style={{ display: 'block', fontSize: 'var(--text-3xs)', marginBottom: 5 }}>
                      {grantName ? `Assigning ${grantName} gives them` : 'Choose a role to see what it gives them'}
                    </span>
                    <span style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                      {gains.map((g) => (
                        <span key={g} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', borderRadius: 'var(--radius-pill)', background: 'var(--success-50)', color: 'var(--success-700)', fontWeight: 700, fontSize: 11 }}>
                          <Icon name="plus" size={12} />{g}
                        </span>
                      ))}
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5, width: 190, flex: 'none' }}>
                    <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)' }}>Role to assign</span>
                    <Select
                      size="sm" value={pick} aria-label={`Role to assign to ${nameOf(u)}`}
                      onChange={(e) => setPicked((p) => ({ ...p, [u.id]: e.target.value }))}
                    >
                      <option value="">— Select role —</option>
                      {roles.map((r) => <option key={r.id} value={r.id}>{r.role_name}</option>)}
                    </Select>
                  </div>
                  <div style={{ display: 'flex', gap: 8, flex: 'none' }}>
                    <Button variant="secondary" size="sm" style={{ color: 'var(--red-700)', borderColor: 'var(--red-200)' }} onClick={() => setConfirm({ kind: 'decline', user: u })}>Decline</Button>
                    {/* Still a confirmed action. The inline picker makes the
                        role visible; the dialog is what makes granting access
                        to child records a decision rather than a click. */}
                    <Button
                      variant="primary" size="sm" iconLeft={<Icon name="user-check" size={15} />}
                      disabled={!pick} onClick={() => openApprove(u, pick)}
                    >
                      {grantName ? `Approve as ${grantName}` : 'Approve'}
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {confirm?.kind === 'approve' && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={run} busy={busy || !grantRole}
          tone="brand" icon={<Icon name="user-check" size={19} />}
          title={`Give ${nameOf(confirm.user)} access?`}
          description={`They will be able to sign in with ${confirm.user.email} and see everything the role below allows.`}
          confirmLabel="Approve" cancelLabel="Cancel"
        >
          <FormField
            label="Role to grant"
            hint={claimed
              ? `They asked for ${claimed}. Set what is actually correct — this is your decision, not theirs.`
              : 'They did not say what they do. Choose the role that matches their work.'}
          >
            <Select value={grantRole} onChange={(e) => setGrantRole(e.target.value)}>
              <option value="">— Select role —</option>
              {roles.map((r) => <option key={r.id} value={r.id}>{r.role_name}</option>)}
            </Select>
          </FormField>
          {/* The same panel User Management shows when a role is corrected, so
              "what does this role mean?" has one answer in both places. */}
          <RoleAccessPanel to={granting?.role_name || null} />
          {granting?.role_name === 'Psychologist' && (
            <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />}>
              That includes clinical interviews, assessment results and uploaded
              reports for the children assigned to them.
            </Alert>
          )}
        </ConfirmDialog>
      )}

      {confirm?.kind === 'decline' && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={run} busy={busy}
          tone="danger" icon={<Icon name="user-x" size={19} />}
          title={`Decline ${nameOf(confirm.user)}?`}
          description="They will not be able to sign in, and this address cannot send another request. If you decline someone by mistake, an administrator can still create their account by hand."
          confirmLabel="Decline" cancelLabel="Cancel"
        />
      )}
    </>
  );
}
