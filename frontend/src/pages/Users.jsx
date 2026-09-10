import { useEffect, useMemo, useRef, useState } from 'react';
import api from '../api/client';
import { useActivity } from '../context/ActivityContext';
import {
  Alert, Avatar, Button, ConfirmDialog, Drawer, EmptyState, FilterPills, FormField, hoverLift,
  Icon, iconBtn, Input, Menu, Modal, PAGE, PageHeader, RoleAccessPanel, RoleBadge, Select,
  Skeleton, Tabs, TOOLBAR,
} from '../ui';
import { useToast } from '../context/ToastContext';
import CredentialHandoffs from './CredentialHandoffs';
import AccessRequests from './AccessRequests';
import { exactDate, shortDate, timeAgo } from '../utils/time';
import { useOpenFromLink } from '../utils/links';

// No password field: the server generates a temporary password on create and
// returns it exactly once — admins never choose another user's password.
const EMPTY = { email: '', first_name: '', last_name: '', middle_initial: '', contact_details: '', role: '' };
const EDITABLE = ['first_name', 'middle_initial', 'last_name', 'email', 'contact_details', 'role'];

/* An account's lifecycle is not one field — it is `status`, plus
 * `must_change_password`, plus `admin_takeover_pending`. The directory is the
 * one place those have to be read as a single state, so derive it here and let
 * every other part of the screen (column, filter, drawer) share the answer. */
const LIFECYCLE = {
  requested: { label: 'Awaiting approval', dot: 'var(--blue-500)', note: 'Asked for access and is waiting on an administrator. Holds no role and can reach nothing until approved. The “Signs in with” column says which door they came through.' },
  active: { label: 'Active', dot: 'var(--success-500)', note: 'Has signed in and set their own password.' },
  pending: { label: 'Pending first sign-in', dot: 'var(--warning-500)', note: 'Holding a temporary password — they must set their own before they reach any case data.' },
  takeover: { label: 'Takeover pending', dot: 'var(--red-500)', note: 'At this administrator’s first sign-in, every other administrator account is deactivated.' },
  deactivated: { label: 'Deactivated', dot: 'var(--ink-400)', note: 'Cannot sign in. Everything they recorded is retained.' },
  declined: { label: 'Request declined', dot: 'var(--ink-300)', note: 'An access request an administrator refused. The account was never approved, holds no role, and this address cannot ask again.' },
};

// Two things this ordering has to get right.
//
// `status` is checked before the flag-derived states: an account awaiting
// approval carries neither flag, so falling through would render it as Active
// — the one reading this screen must never give.
//
// And archived covers two different people. A colleague who left has a role; a
// refused sign-up never got one. Filing a declined stranger under
// "Deactivated" alongside former staff misreads the directory, and over time
// the refused ones outnumber the colleagues.
const statusOf = (u) => (
  u.status === 'archived' ? (u.role ? 'deactivated' : 'declined')
    : u.status === 'pending' ? 'requested'
      : u.admin_takeover_pending ? 'takeover'
        : u.must_change_password ? 'pending'
          : 'active');

/* Why a role field is not editable, in the words the administrator needs —
 * every case here is refused by the server too, so the lock is an explanation
 * rather than the enforcement. Returns null when the role can be changed. */
const roleLockOf = (u) => {
  if (!u?.id) return null;
  const state = statusOf(u);
  if (state === 'requested') {
    return 'This is still a request. Approve it under Access Requests — that is where the role is granted.';
  }
  if (state === 'declined') {
    return 'This request was declined, so there is no role to set. The address cannot ask again.';
  }
  if (u.role_name === 'Administrator') {
    return 'An Administrator’s role cannot be changed — it is the agency’s way back in. Create the replacement administrator first.';
  }
  return null;
};

// Filter buckets are mutually exclusive so the counts add up to the total —
// nothing in this directory is hidden without a number next to it.
const BUCKET_OF = {
  requested: 'requested', active: 'active', pending: 'pending',
  takeover: 'pending', deactivated: 'deactivated', declined: 'declined',
};


// Ordered by how much of the administrator's attention the state wants, not
// alphabetically: sorting by Status should surface what needs a decision.
const SORT_ORDER = { requested: 0, takeover: 1, pending: 2, active: 3, deactivated: 4, declined: 5 };
const nameOf = (u) => (u.fullname || u.username || u.email || '');
const compare = (a, b, key) => {
  if (key === 'role') return (a.role_name || '').localeCompare(b.role_name || '') || nameOf(a).localeCompare(nameOf(b));
  if (key === 'status') return SORT_ORDER[statusOf(a)] - SORT_ORDER[statusOf(b)] || nameOf(a).localeCompare(nameOf(b));
  if (key === 'signin') return Number(!!a.google_linked) - Number(!!b.google_linked) || nameOf(a).localeCompare(nameOf(b));
  if (key === 'last') return new Date(a.last_login).getTime() - new Date(b.last_login).getTime();
  return nameOf(a).localeCompare(nameOf(b));
};

/* "Never signed in" is not an old date, so it must not ride the sort
 * direction: pinned to the bottom both ways, the way a null is in any data
 * table. Applied outside the asc/desc multiplier — inside it, flipping the
 * column would push every never-used account to the top. */
const pinnedLast = (a, b, key) => {
  if (key !== 'last') return 0;
  const av = a.last_login || null;
  const bv = b.last_login || null;
  if (av && bv) return 0;
  if (!av && !bv) return nameOf(a).localeCompare(nameOf(b));
  return av ? -1 : 1;
};

const COLUMNS = [
  { key: 'name', label: 'User', dir: 'asc' },
  { key: 'role', label: 'Role', dir: 'asc' },
  { key: 'status', label: 'Status', dir: 'asc' },
  { key: 'signin', label: 'Sign-in', dir: 'desc' },
  { key: 'last', label: 'Last sign-in', dir: 'desc' },
];

const TH = { textAlign: 'left', padding: '9px 14px', fontFamily: 'var(--font-sans)', fontSize: 'var(--text-3xs)', fontWeight: 800, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--text-muted)', whiteSpace: 'nowrap', background: 'var(--ink-25)', position: 'sticky', top: 0, zIndex: 2, borderBottom: '1px solid var(--border)' };
const TD = { padding: '9px 14px', fontSize: 12.5, color: 'var(--text-body)', verticalAlign: 'middle' };

function StatusCell({ state }) {
  const s = LIFECYCLE[state];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 700, color: state === 'active' ? 'var(--text-body)' : 'var(--text-strong)', whiteSpace: 'nowrap' }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: s.dot, flex: 'none', boxShadow: state === 'takeover' ? '0 0 0 3px var(--red-50)' : 'none' }} />
      {s.label}
    </span>
  );
}

function SignInCell({ google }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
      <Icon name={google ? 'badge-check' : 'key-round'} size={14} style={{ color: google ? 'var(--blue-500)' : 'var(--text-faint)' }} />
      {google ? 'Google' : 'Password'}
    </span>
  );
}

// How each audited action reads in a person's history. `verb` is what they
// did; `passive` is what was done to their account — the same word means
// opposite things depending on which side of the entry you are on.
const ACTION_META = {
  login: { icon: 'log-in', tint: 'var(--ink-400)', verb: 'Signed in', passive: 'Signed in' },
  created: { icon: 'plus', tint: 'var(--success-500)', verb: 'Created', passive: 'Account created' },
  updated: { icon: 'pencil', tint: 'var(--blue-500)', verb: 'Updated', passive: 'Account updated' },
  archived: { icon: 'archive', tint: 'var(--amber-500)', verb: 'Archived', passive: 'Account deactivated' },
};

function ActivityRow({ entry }) {
  const meta = ACTION_META[entry.action] || { icon: 'dot', tint: 'var(--ink-400)', verb: entry.action, passive: entry.action };
  const label = entry.by_them ? meta.verb : meta.passive;
  const target = entry.entity_label || entry.entity_type || '';
  return (
    <li style={{ display: 'flex', gap: 10, padding: '9px 0', borderBottom: '1px solid var(--divider-row)' }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24, flex: 'none', borderRadius: '50%', background: 'var(--surface)', border: '1px solid var(--border)', color: meta.tint, marginTop: 1 }}>
        <Icon name={meta.icon} size={12} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, color: 'var(--text-strong)', fontWeight: 600 }}>
          {label}
          {target && entry.action !== 'login' && (
            <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}> · {target}</span>
          )}
          {!entry.by_them && entry.actor_label && (
            <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}> by {entry.actor_label}</span>
          )}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-faint)' }} title={exactDate(entry.created_at)}>
          {timeAgo(entry.created_at)}
        </div>
      </div>
    </li>
  );
}

function Fact({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, fontSize: 13 }}>
      <span style={{ width: 108, flex: 'none', color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0, color: 'var(--text-strong)', fontWeight: 600 }}>{children}</span>
    </div>
  );
}

export default function Users() {
  const [tab, setTab] = useState('users');
  // "Review access" on the Dashboard means the queue, not the directory.
  useOpenFromLink('tab', 'requests', () => setTab('requests'));
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const [q, setQ] = useState('');
  const [bucket, setBucket] = useState('all');
  const [roleFilter, setRoleFilter] = useState('');
  const [sort, setSort] = useState({ key: 'name', dir: 'asc' });

  const [form, setForm] = useState(null);
  const [pristine, setPristine] = useState(null);
  // null while in flight, [] when there is genuinely nothing.
  const [activity, setActivity] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  // { kind, user } — every destructive action goes through one dialog rather
  // than window.confirm(), so the button can name the consequence.
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);

  // Holds { user, temp_password } while the just-generated temp password is
  // on screen. Closing the modal discards it for good — the API never
  // returns it again, so there is nothing to keep in state afterward.
  const [resetResult, setResetResult] = useState(null);

  // Set when the drawer was opened from "Change role" rather than "Edit
  // details": the role field sits below the account facts and the activity
  // list, so without this the administrator lands on a panel scrolled to the
  // top and has to hunt for the one field they came for.
  const [seekRole, setSeekRole] = useState(false);
  const roleFieldRef = useRef(null);

  const { refresh: refreshActivity } = useActivity();
  const toast = useToast();

  // include_archived: deactivated accounts belong in the directory. Hiding
  // them made "this person is gone" indistinguishable from "this person was
  // never here", and left no way back for an account archived by mistake.
  const load = async () => {
    try {
      const r = await api.get('/users/', { params: { include_archived: 'true' } });
      setUsers(r.data);
      setLoadError('');
    } catch (err) {
      setLoadError(err.response?.data?.detail || 'Could not load the user directory.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    api.get('/roles/').then((r) => setRoles(r.data)).catch(() => setRoles([]));
  }, []);

  const openCreate = () => {
    setError(''); setActivity(null); setPristine({ ...EMPTY }); setForm({ ...EMPTY });
  };

  const openEdit = (u, { toRole = false } = {}) => {
    setError('');
    setPristine({ ...EMPTY, ...u });
    setForm({ ...EMPTY, ...u });
    setSeekRole(toRole);
    // Fetched per open rather than with the directory: it is 25 rows per user
    // and nobody opens every account.
    setActivity(null);
    api.get(`/users/${u.id}/activity/`)
      .then((r) => setActivity(r.data))
      .catch(() => setActivity([]));
  };

  const dirty = !!form && !!pristine && EDITABLE.some((k) => String(form[k] ?? '') !== String(pristine[k] ?? ''));
  const closeForm = () => { setForm(null); setPristine(null); setError(''); setSeekRole(false); };

  const roleName = (id) => roles.find((r) => String(r.id) === String(id))?.role_name || null;
  const roleLock = form ? roleLockOf(form) : null;
  // The pending change, or null. Read off `pristine` rather than a flag so it
  // survives the administrator changing their mind back: picking the original
  // role again leaves nothing to confirm.
  const roleChange = (form?.id && pristine && String(form.role ?? '') !== String(pristine.role ?? ''))
    ? { from: pristine.role_name || roleName(pristine.role), to: roleName(form.role) }
    : null;

  useEffect(() => {
    // Held until the activity list has resolved: it sits above the role field
    // and grows from three skeleton rows to its real height, so scrolling
    // before then puts the field on screen and immediately shoves it off again.
    if (!seekRole || activity === null || !roleFieldRef.current) return;
    // Runs after the Drawer's own effect has focused its first control (child
    // effects fire before the parent's), so this wins rather than fights it.
    roleFieldRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
    roleFieldRef.current.querySelector('select')?.focus();
    setSeekRole(false);
  }, [seekRole, activity, form?.id]);

  // The preview appears *below* the dropdown that summoned it, which on a
  // short window is below the fold — an explanation nobody sees is not one.
  useEffect(() => {
    if (!roleChange) return;
    roleFieldRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
    // roleChange is derived fresh on every render, so depending on the object
    // itself would scroll the field into view on every keystroke. The only
    // thing that should move the page is the target role actually changing.
    // (The disable has to sit on the line before the dependency array — that
    // is where the rule reports, not on the useEffect above.)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleChange?.to]);

  const save = (e) => {
    e.preventDefault();
    setError('');
    // Caught here as well as on the server so the answer is immediate. An
    // account saved without a role cannot sign in at all, and finding that out
    // after handing someone their password is a bad way to learn it. Covers
    // both directions: never created without one, never emptied back to none.
    if (!form.role && (!form.id || pristine?.role)) {
      setError('Choose a role — an account without one cannot sign in.');
      return;
    }
    // A role change alters what someone can reach in the system, so it does not
    // ride along silently with a corrected phone number.
    if (roleChange) { setConfirm({ kind: 'role' }); return; }
    submitForm();
  };

  const submitForm = async () => {
    setError('');
    setSaving(true);
    try {
      const payload = { ...form };
      delete payload.role_name; delete payload.fullname;
      delete payload.must_change_password; delete payload.admin_takeover_pending;
      delete payload.google_linked; delete payload.last_login; delete payload.created_at;
      if (form.id) {
        await api.put(`/users/${form.id}/`, payload);
        // Administrator never appears here — it cannot be reached by an edit —
        // so the article is always "a".
        toast.success(roleChange ? `${nameOf(form)} is now a ${roleChange.to}` : 'User updated');
      } else {
        const { data } = await api.post('/users/', payload);
        toast.success('User added');
        // Same one-time display contract as reset: show the generated temp
        // password now — the server can never show it again.
        setResetResult({ user: data, temp_password: data.temp_password, email_queued: data.email_queued });
      }
      closeForm();
      load();
      refreshActivity();
    } catch (err) {
      const body = err.response?.data;
      setError(typeof body === 'string' ? body
        : body ? Object.entries(body).map(([k, v]) => `${k}: ${[].concat(v).join(' ')}`).join('\n')
          : 'Save failed.');
      toast.error('Could not save the user. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const runConfirmed = async () => {
    const { kind, user: u } = confirm;
    if (kind === 'discard') { setConfirm(null); closeForm(); return; }
    // The drawer's own save path — the dialog was the confirmation, not a
    // separate action, so errors still land in the drawer where the form is.
    // Awaited before closing so the dialog carries the saving state; submitForm
    // handles its own failures, so this always gets to the close.
    if (kind === 'role') { await submitForm(); setConfirm(null); return; }
    setBusy(true);
    try {
      if (kind === 'archive') {
        await api.post(`/users/${u.id}/archive/`);
        toast.success(`${nameOf(u)} deactivated`);
      } else if (kind === 'reactivate') {
        const { data } = await api.post(`/users/${u.id}/reactivate/`);
        // Only true where there is a password to change. A colleague who signs
        // in with Google has none, and telling them to set one sends them
        // looking for a screen that cannot help them.
        toast.success(data?.must_change_password
          ? `${nameOf(u)} reactivated — they must set a new password at next sign-in`
          : `${nameOf(u)} reactivated — they sign in with Google as before`);
      } else if (kind === 'reset') {
        const { data } = await api.post(`/users/${u.id}/reset-password/`);
        setResetResult({ user: u, temp_password: data.temp_password, email_queued: data.email_queued });
      }
      setConfirm(null);
      // These can all be launched from inside the drawer; leaving it open
      // would show account facts the action just invalidated.
      closeForm();
      load();
      refreshActivity();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'That did not go through. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const copyTempPassword = () => {
    if (!resetResult) return;
    navigator.clipboard.writeText(resetResult.temp_password);
    toast.success('Temporary password copied to clipboard.');
  };

  const toneFor = (role) => (role === 'Administrator' ? 'brand' : role === 'Psychologist' ? 'red' : 'amber');

  const counts = useMemo(() => {
    const c = { all: users.length, requested: 0, active: 0, pending: 0, deactivated: 0, declined: 0 };
    users.forEach((u) => { c[BUCKET_OF[statusOf(u)]] += 1; });
    return c;
  }, [users]);

  const pendingHandoffs = users.filter((u) => u.must_change_password && u.status !== 'archived').length;

  // Mirrors the server guard in UserViewSet.archive. Nothing in the product can
  // mint a replacement administrator — approving a request refuses the role,
  // reactivating refuses administrators, and creating one needs an
  // administrator signed in — so deactivating the last one locks everyone out.
  // Raw `status`, not statusOf(): an admin owing a password change is still an
  // active account, and that is exactly what the server counts.
  const activeAdmins = useMemo(
    () => users.filter((u) => u.role_name === 'Administrator' && u.status === 'active').length,
    [users]);
  const isLastAdmin = (u) => (
    u?.role_name === 'Administrator' && u?.status === 'active' && activeAdmins <= 1);
  const LAST_ADMIN_HINT = 'The only administrator account. Create the replacement administrator first.';

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rows = users.filter((u) => {
      if (bucket !== 'all' && BUCKET_OF[statusOf(u)] !== bucket) return false;
      if (roleFilter && String(u.role) !== String(roleFilter)) return false;
      if (!needle) return true;
      return [u.fullname, u.username, u.email, u.contact_details, u.role_name]
        .some((v) => v && String(v).toLowerCase().includes(needle));
    });
    const dir = sort.dir === 'asc' ? 1 : -1;
    return rows.sort((a, b) => pinnedLast(a, b, sort.key) || dir * compare(a, b, sort.key));
  }, [users, q, bucket, roleFilter, sort]);

  const filtered = q.trim() || bucket !== 'all' || roleFilter;
  const clearFilters = () => { setQ(''); setBucket('all'); setRoleFilter(''); };

  const toggleSort = (col) => setSort((s) => (
    s.key === col.key ? { key: col.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: col.key, dir: col.dir }));

  const menuItems = (u) => {
    const state = statusOf(u);
    const archived = state === 'deactivated';
    const isAdmin = u.role_name === 'Administrator';
    // A refused sign-up was never approved: there is no role to restore and no
    // credential to issue. Offering either would be offering a way back in.
    if (state === 'declined') {
      return [{ id: 'edit', label: 'View details', icon: 'eye', onSelect: () => openEdit(u) }];
    }
    const lock = roleLockOf(u);
    return [
      { id: 'edit', label: 'Edit details', icon: 'pencil', onSelect: () => openEdit(u) },
      {
        id: 'role',
        label: 'Change role',
        icon: 'shield',
        disabled: !!lock,
        hint: lock || undefined,
        onSelect: () => openEdit(u, { toRole: true }),
      },
      {
        id: 'reset',
        label: 'Issue temporary password',
        icon: 'key-round',
        disabled: archived,
        hint: archived ? 'Reactivate the account first.' : undefined,
        onSelect: () => setConfirm({ kind: 'reset', user: u }),
      },
      { separator: true },
      archived
        ? {
          id: 'reactivate',
          label: 'Reactivate account',
          icon: 'user-check',
          disabled: isAdmin,
          hint: isAdmin ? 'A deactivated administrator can only return as a brand-new account.' : undefined,
          onSelect: () => setConfirm({ kind: 'reactivate', user: u }),
        }
        : {
          id: 'archive',
          label: 'Deactivate account',
          icon: 'user-x',
          tone: 'danger',
          disabled: isLastAdmin(u),
          hint: isLastAdmin(u) ? LAST_ADMIN_HINT : undefined,
          onSelect: () => setConfirm({ kind: 'archive', user: u }),
        },
    ];
  };

  const formRole = roleName(form?.role);

  return (
    <div style={{ ...PAGE, position: 'relative' }}>
      <PageHeader
        title="User Management"
        subtitle="Accounts, roles and the access queue — the only access control this system has"
      >
        <Button variant="primary" onClick={openCreate} iconLeft={<Icon name="user-plus" size={18} />}>Create account</Button>
      </PageHeader>

      {/* Tabs, filters and rows in one card: the queue, the directory and the
          handoffs are three views of the same object, and three separate
          slabs made them look like three unrelated screens. */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
      <Tabs
        tabs={[
          { id: 'users', label: 'Directory', count: counts.all || undefined },
          // Second, not last: someone is waiting on this one, and a queue an
          // administrator has to go looking for is a queue that sits.
          { id: 'requests', label: 'Access Requests', count: counts.requested || undefined },
          { id: 'handoffs', label: 'Credential Handoffs', count: pendingHandoffs || undefined },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === 'requests' ? (
        // Approving changes the directory underneath, so refresh it rather
        // than leaving a stale row behind on the Users tab.
        <AccessRequests onChange={load} />
      ) : tab === 'handoffs' ? (
        <CredentialHandoffs />
      ) : (
        <>
          <div style={TOOLBAR}>
            <div style={{ width: 280, maxWidth: '100%' }}>
              <Input
                size="sm"
                value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users"
                placeholder="Search name, email, contact or role…"
                leading={<Icon name="search" size={16} />}
                trailing={q ? (
                  <button type="button" aria-label="Clear search" onClick={() => setQ('')} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--text-faint)', display: 'inline-flex', padding: 0 }}>
                    <Icon name="x" size={15} />
                  </button>
                ) : null}
              />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <FilterPills
                label="Filter users by account status"
                value={bucket}
                onChange={setBucket}
                options={[
                  { key: 'all', label: 'All', count: counts.all },
                  // Only shown once someone is actually waiting — an empty
                  // pill would imply a queue that does not exist yet.
                  ...(counts.requested ? [{ key: 'requested', label: 'Awaiting approval', count: counts.requested, dot: LIFECYCLE.requested.dot }] : []),
                  { key: 'active', label: 'Active', count: counts.active, dot: LIFECYCLE.active.dot },
                  { key: 'pending', label: 'Pending first sign-in', count: counts.pending, dot: LIFECYCLE.pending.dot },
                  { key: 'deactivated', label: 'Deactivated', count: counts.deactivated, dot: LIFECYCLE.deactivated.dot },
                  // Kept out of sight until there is one, then counted rather
                  // than hidden — refused sign-ups accumulate and would
                  // otherwise quietly pad the Deactivated tally.
                  ...(counts.declined ? [{ key: 'declined', label: 'Declined requests', count: counts.declined, dot: LIFECYCLE.declined.dot }] : []),
                ]}
              />
              <div style={{ width: 178 }}>
                <Select size="sm" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} aria-label="Filter by role">
                  <option value="">All roles</option>
                  {roles.map((r) => <option key={r.id} value={r.id}>{r.role_name}</option>)}
                </Select>
              </div>
            </div>
            <span style={{ flex: 1 }} />
            <div aria-live="polite" style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
              Showing <strong style={{ color: 'var(--text-strong)' }}>{visible.length}</strong> of {users.length} accounts
            </div>
          </div>

          <>
            {loadError ? (
              <div style={{ padding: 20 }}>
                <Alert tone="danger" title="The directory could not be loaded" icon={<Icon name="wifi-off" size={18} />}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
                    <span>{loadError} Nothing has been changed — this is a display problem, not missing accounts.</span>
                    <Button variant="secondary" size="sm" onClick={() => { setLoading(true); load(); }} iconLeft={<Icon name="refresh-cw" size={15} />}>Try again</Button>
                  </div>
                </Alert>
              </div>
            ) : (
              <div className="racco-scroll" style={{ overflow: 'auto', maxHeight: 'max(340px, calc(100vh - 340px))' }}>
                <table style={{ width: '100%', minWidth: 880, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr>
                      {COLUMNS.map((c) => {
                        const on = sort.key === c.key;
                        return (
                          <th key={c.key} scope="col" aria-sort={on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'} style={TH}>
                            <button
                              type="button" onClick={() => toggleSort(c)}
                              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-strong)'; }}
                              onMouseLeave={(e) => { e.currentTarget.style.color = on ? 'var(--blue-700)' : 'var(--text-muted)'; }}
                              style={{ display: 'inline-flex', alignItems: 'center', gap: 5, border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', font: 'inherit', letterSpacing: 'inherit', textTransform: 'inherit', color: on ? 'var(--blue-700)' : 'var(--text-muted)' }}
                            >
                              {c.label}
                              <Icon name={on ? (sort.dir === 'asc' ? 'chevron-up' : 'chevron-down') : 'chevrons-up-down'} size={13} style={{ opacity: on ? 1 : 0.4 }} />
                            </button>
                          </th>
                        );
                      })}
                      <th scope="col" style={{ ...TH, width: 56 }}><span style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading && Array.from({ length: 5 }).map((_, i) => (
                      <tr key={`skeleton-${i}`} style={{ borderBottom: '1px solid var(--divider-row)' }}>
                        <td style={TD}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                            <Skeleton width={30} height={30} radius="50%" />
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}>
                              <Skeleton width="52%" height={11} />
                              <Skeleton width="72%" height={9} />
                            </div>
                          </div>
                        </td>
                        <td style={TD}><Skeleton width={92} height={18} radius="var(--radius-pill)" /></td>
                        <td style={TD}><Skeleton width={80} height={11} /></td>
                        <td style={TD}><Skeleton width={64} height={11} /></td>
                        <td style={TD}><Skeleton width={70} height={11} /></td>
                        <td style={TD}><Skeleton width={18} height={11} /></td>
                      </tr>
                    ))}

                    {!loading && visible.map((u) => {
                      const state = statusOf(u);
                      const off = state === 'deactivated';
                      return (
                        <tr
                          key={u.id}
                          onClick={() => openEdit(u)}
                          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--blue-50)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                          style={{ borderBottom: '1px solid var(--divider-row)', cursor: 'pointer', transition: 'background var(--dur-fast) var(--ease-out)', opacity: off ? 0.66 : 1 }}
                        >
                          <td style={TD}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 11, minWidth: 0 }}>
                              <Avatar name={nameOf(u)} tone={off ? 'neutral' : toneFor(u.role_name)} size="sm" />
                              <div style={{ minWidth: 0 }}>
                                {/* The focusable thing in the row, rather than
                                    the row itself. Everything the row's old
                                    aria-label said is said here, on a control
                                    a keyboard can actually reach. */}
                                <button
                                  type="button"
                                  onClick={(e) => { e.stopPropagation(); openEdit(u); }}
                                  aria-label={`${nameOf(u)} — ${u.role_name || 'no role'}, ${LIFECYCLE[state].label}. Open account.`}
                                  style={{ all: 'unset', cursor: 'pointer', display: 'block', maxWidth: '100%', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                                >
                                  {nameOf(u)}
                                </button>
                                <div className="racco-mono" style={{ fontSize: 11.5, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{u.email}</div>
                              </div>
                            </div>
                          </td>
                          <td style={TD}>{u.role_name ? <RoleBadge role={u.role_name} size="sm" /> : <span style={{ color: 'var(--text-faint)' }}>—</span>}</td>
                          <td style={TD} title={LIFECYCLE[state].note}><StatusCell state={state} /></td>
                          <td style={TD}><SignInCell google={u.google_linked} /></td>
                          <td style={{ ...TD, whiteSpace: 'nowrap' }} title={exactDate(u.last_login)}>
                            {u.last_login
                              ? <span style={{ color: 'var(--text-body)' }}>{timeAgo(u.last_login)}</span>
                              : <span style={{ color: 'var(--text-faint)' }}>Never</span>}
                          </td>
                          <td style={{ ...TD, textAlign: 'right' }} onClick={(e) => e.stopPropagation()}>
                            <Menu label={`Actions for ${nameOf(u)}`} items={menuItems(u)} />
                          </td>
                        </tr>
                      );
                    })}

                    {!loading && visible.length === 0 && (
                      <tr>
                        <td colSpan={6} style={{ padding: 0 }}>
                          {filtered ? (
                            <EmptyState
                              icon={<Icon name="search-x" size={24} />}
                              title="No accounts match those filters"
                              description="Try a different name or email, or widen the status and role filters."
                              action={<Button variant="secondary" size="sm" onClick={clearFilters} iconLeft={<Icon name="rotate-ccw" size={15} />}>Clear filters</Button>}
                            />
                          ) : (
                            <EmptyState
                              icon={<Icon name="users" size={24} />}
                              title="No accounts yet"
                              description="Add the agency's administrators, psychologists and staff. Each one gets a temporary password to hand over."
                              action={<Button variant="primary" size="sm" onClick={openCreate} iconLeft={<Icon name="user-plus" size={15} />}>Add the first user</Button>}
                            />
                          )}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        </>
      )}
      </div>

      {form && (
        <Drawer
          as="form" onSubmit={save} noValidate
          title={form.id ? nameOf(form) : 'Add User'}
          subtitle={form.id ? form.email : 'A new agency account'}
          avatar={form.id ? <Avatar name={nameOf(form)} tone={toneFor(form.role_name)} size="md" /> : null}
          dismissible={!dirty}
          onClose={closeForm}
          onDismissBlocked={() => setConfirm({ kind: 'discard' })}
          footer={<>
            <Button type="button" variant="ghost" onClick={() => (dirty ? setConfirm({ kind: 'discard' }) : closeForm())}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={saving} style={{ flex: 1 }} iconLeft={<Icon name={roleChange ? 'shield' : 'save'} size={16} />}>
              {saving ? 'Saving…' : roleChange ? 'Review role change' : form.id ? 'Save changes' : 'Create user'}
            </Button>
          </>}
        >
          {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}><span style={{ whiteSpace: 'pre-line' }}>{error}</span></Alert>}

          {/* Account facts first: what an admin opens a row to check is almost
              always the state of the account, not the spelling of the name. */}
          {form.id && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px', background: 'var(--ink-50)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
              <div className="racco-eyebrow" style={{ fontSize: 10 }}>Account</div>
              <Fact label="Status"><StatusCell state={statusOf(form)} /></Fact>
              <Fact label="Signs in with"><SignInCell google={form.google_linked} /></Fact>
              <Fact label="Last sign-in">{form.last_login ? exactDate(form.last_login) : <span style={{ color: 'var(--text-faint)', fontWeight: 500 }}>Never</span>}</Fact>
              <Fact label="Added">{form.created_at ? shortDate(form.created_at) : '—'}</Fact>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                {LIFECYCLE[statusOf(form)].note}
              </div>
              {/* The same actions as the row menu, so opening an account to
                  check it does not mean closing it again to act on it. */}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {statusOf(form) === 'declined' ? (
                  <span style={{ fontSize: 12, color: 'var(--text-muted)', fontStyle: 'italic' }}>
                    Nothing to restore — this request was never approved.
                  </span>
                ) : statusOf(form) === 'deactivated' ? (
                  <Button
                    variant="secondary" size="sm" iconLeft={<Icon name="user-check" size={15} />}
                    disabled={form.role_name === 'Administrator'}
                    title={form.role_name === 'Administrator' ? 'A deactivated administrator can only return as a brand-new account.' : undefined}
                    onClick={() => setConfirm({ kind: 'reactivate', user: form })}
                  >Reactivate</Button>
                ) : (
                  <>
                    <Button variant="secondary" size="sm" iconLeft={<Icon name="key-round" size={15} />} onClick={() => setConfirm({ kind: 'reset', user: form })}>Temporary password</Button>
                    <Button variant="ghost" size="sm" iconLeft={<Icon name="user-x" size={15} />} style={{ color: 'var(--red-600)' }} disabled={isLastAdmin(form)} title={isLastAdmin(form) ? LAST_ADMIN_HINT : undefined} onClick={() => setConfirm({ kind: 'archive', user: form })}>Deactivate</Button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* What has this person actually done? For an agency accountable
              under RA 10173 that is the question an account gets opened to
              answer, and until now the drawer could not answer it. */}
          {form.id && (
            <div>
              <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 6 }}>Recent activity</div>
              {activity === null ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 4 }}>
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                      <Skeleton width={24} height={24} radius="50%" />
                      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 5 }}>
                        <Skeleton width="62%" height={10} />
                        <Skeleton width="30%" height={8} />
                      </div>
                    </div>
                  ))}
                </div>
              ) : activity.length === 0 ? (
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '6px 0' }}>
                  Nothing recorded yet.
                </div>
              ) : (
                <>
                  {/* Five, not more: enough to answer "what has this person
                      been doing?" at a glance, few enough that the edit fields
                      below stay reachable without scrolling past a wall. */}
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                    {activity.slice(0, 5).map((e) => <ActivityRow key={e.id} entry={e} />)}
                  </ul>
                  {activity.length > 5 && (
                    <div style={{ fontSize: 11.5, color: 'var(--text-muted)', paddingTop: 8 }}>
                      Showing the 5 most recent of {activity.length}. The full trail is in the audit log.
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {[['first_name', 'First name'], ['middle_initial', 'Middle initial'], ['last_name', 'Last name'], ['email', 'Email'], ['contact_details', 'Contact details']].map(([k, label]) => (
            <FormField key={k} label={label} hint={k === 'email' ? 'Also their username. For anyone signing in with Google, it must be the Google address.' : undefined}>
              <Input value={form[k] || ''} onChange={(e) => setForm({ ...form, [k]: e.target.value })} type={k === 'email' ? 'email' : 'text'} />
            </FormField>
          ))}

          {/* A role used to be frozen once assigned, which left a mis-set role
              fixable only by deactivating the person and creating them again —
              losing their history to correct a dropdown. It is editable now,
              minus the two directions the server refuses (see roleLockOf). */}
          <div ref={roleFieldRef}>
            {roleLock ? (
              <FormField label="Role" hint={roleLock}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 42, padding: '0 13px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)', color: 'var(--text-strong)', fontWeight: 700, fontSize: 14 }}>
                  {form.role_name || formRole || (form.requested_role_name ? `${form.requested_role_name} (claimed)` : '—')}
                  <Icon name="lock" size={13} style={{ color: 'var(--text-faint)', marginLeft: 'auto' }} />
                </div>
              </FormField>
            ) : (
              <FormField
                label="Role"
                hint={form.id
                  ? 'What this person can reach. Changing it takes effect the moment you save.'
                  : undefined}
              >
                <Select value={form.role || ''} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                  {/* No blank option once a role exists: emptying it would
                      leave an active account that fails at sign-in with no
                      explanation. Deactivating is how access is removed. */}
                  {(!form.id || !pristine?.role) && <option value="">— Select role —</option>}
                  {roles
                    // Administrator is offered when creating an account (that
                    // path runs the handover) but never as an edit: promoting
                    // someone here would skip the handover and leave two live
                    // administrators. The server refuses it either way.
                    .filter((r) => !form.id || r.role_name !== 'Administrator')
                    .map((r) => <option key={r.id} value={r.id}>{r.role_name}</option>)}
                </Select>
              </FormField>
            )}

            {roleChange && (
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <RoleAccessPanel from={roleChange.from} to={roleChange.to} />
                <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />}>
                  Not saved yet — you&rsquo;ll be asked to confirm. If {form.first_name || 'this person'} is
                  signed in right now, the new access applies immediately, but their
                  menu keeps the old shape until they sign out and back in.
                </Alert>
              </div>
            )}
          </div>

          {/* Single-admin handover warning (product decision 2026-07-18). */}
          {!form.id && formRole === 'Administrator' && (
            <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />}>
              Admin handover: when this new administrator logs in for the
              first time, every other administrator account — including
              yours — is deactivated immediately. A deactivated
              administrator can only return with a brand-new account.
            </Alert>
          )}
          {!form.id && (
            <Alert tone="info" icon={<Icon name="key-round" size={18} />}>
              A temporary password is generated automatically when you save.
              It is shown once — hand it to the user, and they must set their
              own password at first login.
            </Alert>
          )}
        </Drawer>
      )}

      {confirm?.kind === 'discard' && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={runConfirmed}
          tone="warning" icon={<Icon name="alert-triangle" size={19} />}
          title="Discard unsaved changes?"
          description="The edits in this panel have not been saved yet."
          cancelLabel="Keep editing" confirmLabel="Discard"
        />
      )}

      {confirm?.kind === 'role' && roleChange && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={runConfirmed} busy={saving}
          tone="warning" icon={<Icon name="shield" size={19} />}
          title={`Change ${nameOf(form)} to ${roleChange.to}?`}
          description="Their access changes as soon as you save."
          confirmLabel={saving ? 'Saving…' : `Change to ${roleChange.to}`} cancelLabel="Cancel"
        >
          <RoleAccessPanel from={roleChange.from} to={roleChange.to} />
          {/* Records already entered under the old role stay where they are —
              worth saying, because the fear that prompts the question is that
              correcting a role quietly reassigns or hides someone's work. */}
          <Alert tone="info" icon={<Icon name="info" size={18} />}>
            Everything they have already recorded stays exactly as it is, and the
            change is written to the audit trail with your name on it.
          </Alert>
        </ConfirmDialog>
      )}

      {confirm?.kind === 'archive' && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={runConfirmed} busy={busy}
          tone="danger" icon={<Icon name="user-x" size={19} />}
          title={`Deactivate ${nameOf(confirm.user)}?`}
          description="They lose access immediately and any signed-in session ends. Everything they recorded is kept, and you can reactivate the account later."
          confirmLabel="Deactivate" cancelLabel="Cancel"
          // Deactivating an administrator is the one action here that can lock
          // the agency out of its own system, so it costs more than a click.
          confirmPhrase={confirm.user.role_name === 'Administrator' ? confirm.user.email : null}
          confirmHint={confirm.user.role_name === 'Administrator' ? <>This is an <strong>administrator</strong>. Type their email to confirm</> : null}
        >
          {confirm.user.role_name === 'Administrator' && (
            <Alert tone="danger" icon={<Icon name="shield-alert" size={18} />}>
              If this is the last active administrator, nobody will be able to
              manage users, settings or catalogues afterwards.
            </Alert>
          )}
        </ConfirmDialog>
      )}

      {confirm?.kind === 'reactivate' && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={runConfirmed} busy={busy}
          tone="brand" icon={<Icon name="user-check" size={19} />}
          title={`Reactivate ${nameOf(confirm.user)}?`}
          description="They can sign in again with their previous password, and will be required to set a new one before reaching any case data."
          confirmLabel="Reactivate" cancelLabel="Cancel"
        />
      )}

      {confirm?.kind === 'reset' && (
        <ConfirmDialog
          onClose={() => setConfirm(null)} onConfirm={runConfirmed} busy={busy}
          tone="warning" icon={<Icon name="key-round" size={19} />}
          title="Issue a temporary password?"
          description={`${nameOf(confirm.user)}'s current password stops working the moment you confirm. The new one is shown once — have a way to hand it over ready.`}
          confirmLabel="Send password" cancelLabel="Cancel"
        />
      )}

      {resetResult && (
        // Not dismissible: a stray click on the backdrop would destroy a
        // secret the server will never show again.
        <Modal
          open onClose={() => setResetResult(null)} dismissible={false} width={460}
          tone="warning" icon={<Icon name="key-round" size={19} />}
          title="Temporary password"
          subtitle={nameOf(resetResult.user)}
          footer={<Button type="button" variant="primary" onClick={() => setResetResult(null)}>Close</Button>}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px', background: 'var(--ink-50)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
            <span className="racco-mono" style={{ flex: 1, fontSize: 18, fontWeight: 700, color: 'var(--text-strong)', letterSpacing: '0.04em', wordBreak: 'break-all' }}>{resetResult.temp_password}</span>
            <button type="button" title="Copy" aria-label="Copy temporary password" onClick={copyTempPassword} {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--blue-600)')}><Icon name="copy" size={15} /></button>
          </div>
          {/* "Queued", not "sent": the server hands the message to a background
              thread and never reads Brevo's answer, so a rejected key or an
              unverified sender looks identical to a delivered email from here.
              Saying "sent" is what would stop an administrator handing the
              password over — which is the one thing that always works. */}
          {resetResult.email_queued ? (
            <Alert tone="info" icon={<Icon name="mail" size={18} />}>
              Queued for delivery to {resetResult.user.email}. Arrival is not
              confirmed here — if it does not turn up, hand the password over
              directly.
            </Alert>
          ) : (
            <Alert tone="danger" icon={<Icon name="mail-warning" size={18} />}>
              The temporary password was not emailed. Ask the system administrator to configure the Brevo email service, then generate a new password.
            </Alert>
          )}
          <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />}>
            Give this to the user — they&rsquo;ll be required to set a new password at next login.
            This password will not be shown again; generate a new one if it&rsquo;s lost.
          </Alert>
        </Modal>
      )}
    </div>
  );
}
