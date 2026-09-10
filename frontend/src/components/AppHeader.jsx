import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useActivity } from '../context/ActivityContext';
import { useLayout } from '../context/LayoutContext';
import { useCensus } from '../context/CensusContext';
import api from '../api/client';
import {
  Alert, Button, FormField, Icon, PasswordInput, ConfirmDialog, RoleAccessPanel, ROLE_META,
} from '../ui';
import { screenIdFor, screensFor, tabIsActive, topTabsFor } from '../config/nav';
import { ACTION_META, eventDestination, eventText } from '../utils/activity';
import { initialsOf } from '../utils/child';
import { timeAgo } from '../utils/time';
import GlobalSearch from './GlobalSearch';

/* The chrome, on deep navy, 56px tall, identical on every screen.
 *
 * Left to right: who this office is, one search box, the handful of places
 * people actually go, then the things that are about YOU rather than about the
 * casework — notifications, help, your own account. That order is not a
 * preference; it is the order almost every application the staff already use
 * puts them in, and borrowing it is the entire point.
 *
 * The old header carried the screen's title and subtitle. Those moved into the
 * screens themselves, where a title can sit next to the buttons that act on
 * it, and the space they freed is what the tab strip now occupies.
 */

const NOTIF_TABS = [
  { key: 'all', label: 'All' },
  { key: 'record', label: 'Records' },
  { key: 'user', label: 'Users' },
  { key: 'security', label: 'Security' },
];

const EMPTY_PW = { current_password: '', new_password: '', confirm: '' };

/* One popover shape for the three things that hang off the right of the bar.
 * `onClose` fires on an outside click or Escape — every one of them needs it,
 * and three copies of the same effect is how they drift. */
function Popover({ open, onClose, width, children, label }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open, onClose]);

  return (
    <div ref={ref} style={{ position: 'relative', flex: 'none' }}>
      {children[0]}
      {open && (
        <div
          role="menu"
          aria-label={label}
          style={{
            position: 'absolute', top: 46, right: 0, width,
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-xl)',
            overflow: 'hidden', zIndex: 60, animation: 'racco-pop-in var(--dur-base) var(--ease-out)',
          }}
        >
          {children[1]}
        </div>
      )}
    </div>
  );
}

function UtilityButton({ icon, label, on, badge = 0, onClick }) {
  return (
    <button
      type="button" onClick={onClick} title={label} aria-label={label} aria-expanded={on}
      style={{
        position: 'relative', width: 38, height: 38, borderRadius: '50%', border: 'none',
        background: on ? 'rgba(255,255,255,0.22)' : 'var(--chrome-fill)', color: 'var(--chrome-ink)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
        transition: 'background var(--dur-fast) var(--ease-out)',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.22)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = on ? 'rgba(255,255,255,0.22)' : 'var(--chrome-fill)'; }}
    >
      <Icon name={icon} size={19} />
      {badge > 0 && (
        <span className="racco-mono" style={{ position: 'absolute', top: -2, right: -2, minWidth: 17, height: 17, padding: '0 4px', borderRadius: 'var(--radius-pill)', background: 'var(--red-500)', color: '#fff', fontSize: 10, fontWeight: 800, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: '2px solid var(--chrome)' }}>
          {badge}
        </span>
      )}
    </button>
  );
}

function MenuRow({ icon, label, sub, tone = 'neutral', onClick }) {
  return (
    <button
      type="button" role="menuitem" onClick={onClick}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 11, padding: '8px 9px',
        borderRadius: 'var(--radius-sm)', border: 'none', background: 'transparent',
        cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ink-50)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 32, height: 32, borderRadius: '50%', flex: 'none', background: tone === 'danger' ? 'var(--red-50)' : 'var(--ink-50)', color: tone === 'danger' ? 'var(--red-700)' : 'var(--text-body)' }}>
        <Icon name={icon} size={17} />
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: tone === 'danger' ? 'var(--red-700)' : 'var(--text-strong)' }}>{label}</span>
        {sub && <span style={{ display: 'block', fontSize: 11, color: 'var(--text-faint)' }}>{sub}</span>}
      </span>
    </button>
  );
}

export default function AppHeader() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const layout = useLayout();
  const { pendingAccess } = useCensus();
  const { events, unreadCount, markSeen } = useActivity();

  const role = user?.role_name || 'Staff';
  const name = user?.fullname || user?.username || 'User';
  const firstName = name.split(' ')[0];
  const screenId = screenIdFor(location.pathname);

  const tabs = topTabsFor(role);
  // Everything the tab strip does not carry. Only offered when the rail is
  // gone — otherwise the rail is already showing all of it, and a menu
  // duplicating the thing next to it is just noise.
  const overflow = screensFor(role).filter((s) => !tabs.some((t) => t.id === s.id));

  const [searchOpen, setSearchOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [notifTab, setNotifTab] = useState('all');
  const [logoutOpen, setLogoutOpen] = useState(false);

  // Self-service password change — every role, not just administrators.
  const [pwOpen, setPwOpen] = useState(false);
  const [pw, setPw] = useState(EMPTY_PW);
  const [pwError, setPwError] = useState('');
  const [pwBusy, setPwBusy] = useState(false);

  // Only one of these may be open at a time; two popovers overlapping in a
  // 56px bar is unreadable.
  const openOnly = (which) => {
    setSearchOpen(which === 'search');
    setNotifOpen(which === 'notif');
    setHelpOpen(which === 'help');
    setMenuOpen(which === 'menu');
    setMoreOpen(which === 'more');
  };

  // Navigating away closes whatever was hanging open. Written out rather than
  // calling openOnly so the dependency list stays honest.
  useEffect(() => {
    setSearchOpen(false); setNotifOpen(false); setHelpOpen(false);
    setMenuOpen(false); setMoreOpen(false);
  }, [location.pathname]);

  const shownEvents = events.filter((e) => notifTab === 'all' || e.category === notifTab);
  const notifTabs = role === 'Administrator' ? NOTIF_TABS
    : role === 'Staff' ? NOTIF_TABS.filter((t) => ['all', 'record'].includes(t.key))
      : NOTIF_TABS.filter((t) => t.key === 'all');

  const openPw = () => { setPw(EMPTY_PW); setPwError(''); setPwOpen(true); openOnly(null); };

  const submitPw = async (e) => {
    e.preventDefault();
    setPwError('');
    if (pw.new_password.length < 8) { setPwError('New password must be at least 8 characters.'); return; }
    if (pw.new_password !== pw.confirm) { setPwError('Passwords do not match.'); return; }
    setPwBusy(true);
    try {
      const { data } = await api.post('/auth/change-password/', {
        current_password: pw.current_password, new_password: pw.new_password,
      });
      setPwOpen(false);
      // Same rule as the forced-change gate, and for the same reason: the
      // session this was changed from no longer authenticates.
      if (data?.reauthenticate) {
        toast.success('Password changed. Please sign in with your new password.');
        logout();
        return;
      }
      toast.success('Password changed.');
    } catch (err) {
      const data = err.response?.data || {};
      const msg = data.current_password || data.new_password || data.non_field_errors || data.detail
        || 'Could not change the password.';
      setPwError(Array.isArray(msg) ? msg[0] : msg);
    } finally {
      setPwBusy(false);
    }
  };

  const badgeFor = (id) => (id === 'users' ? pendingAccess : 0);
  const roleDot = (ROLE_META[role] || ROLE_META.Staff).color;

  return (
    <header
      style={{
        height: 'var(--topbar-h)', flex: 'none', background: 'var(--chrome)',
        display: 'flex', alignItems: 'center', gap: 16, padding: '0 16px',
        position: 'relative', zIndex: 30,
      }}
    >
      {/* Brand. Its width tracks the rail so the two line up down the page —
          but it must not reserve a 234px column once the rail is gone. */}
      <div style={{ width: layout.brandWidth, flex: 'none', display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <img
          src="/racco-seal.jpg" alt="" aria-hidden="true"
          style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', flex: 'none', boxShadow: '0 0 0 2px rgba(255,255,255,0.18)' }}
        />
        {layout.brandTextOn && (
          <div style={{ lineHeight: 1.05, minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 14, color: 'var(--chrome-ink)', whiteSpace: 'nowrap' }}>NACC &ndash; RACCO 1</div>
            <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 8.5, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--chrome-ink-dim)', whiteSpace: 'nowrap' }}>Child Care · Region I</div>
          </div>
        )}
      </div>

      <GlobalSearch open={searchOpen} onOpenChange={(v) => openOnly(v ? 'search' : null)} />

      {/* Tabs. Icon-only and centred, with the label as the tooltip: five or
          six destinations is few enough to learn by position, and labels here
          cost the width the search box needs. */}
      <nav aria-label="Primary" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, minWidth: 0 }}>
        {tabs.map((t) => {
          const on = tabIsActive(t.id, screenId, role);
          const badge = badgeFor(t.id);
          return (
            <button
              key={t.id} type="button" title={t.label} aria-label={t.label} aria-current={on ? 'page' : undefined}
              onClick={() => navigate(t.to)}
              style={{
                position: 'relative', width: layout.tabWidth, height: 44, border: 'none',
                borderRadius: 'var(--radius-sm)', background: on ? 'rgba(255,255,255,0.12)' : 'transparent',
                cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: on ? 'var(--chrome-ink)' : 'var(--chrome-ink-soft)', flex: 'none',
                transition: 'background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out)',
              }}
              onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = 'rgba(255,255,255,0.08)'; }}
              onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = 'transparent'; }}
            >
              <Icon name={t.icon} size={21} strokeWidth={on ? 2.2 : 1.9} />
              <span style={{ position: 'absolute', left: 14, right: 14, bottom: -6, height: 3, borderRadius: 3, background: on ? 'var(--amber-400)' : 'transparent' }} />
              {badge > 0 && (
                <span className="racco-mono" style={{ position: 'absolute', top: 4, right: Math.max(8, layout.tabWidth / 2 - 23), minWidth: 16, height: 16, padding: '0 4px', borderRadius: 'var(--radius-pill)', background: 'var(--red-500)', color: '#fff', fontSize: 9.5, fontWeight: 800, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', border: '2px solid var(--chrome)' }}>
                  {badge}
                </span>
              )}
            </button>
          );
        })}

        {/* Below 900px the rail is gone, so the destinations it was carrying
            have to arrive somewhere. Without this, Settings and Instruments
            are simply unreachable on a narrow window. */}
        {!layout.leftRailOn && overflow.length > 0 && (
          <Popover open={moreOpen} onClose={() => setMoreOpen(false)} width={272} label="More destinations">
            <button
              type="button" title="More" aria-label="More destinations" aria-expanded={moreOpen}
              onClick={() => openOnly(moreOpen ? null : 'more')}
              style={{
                width: layout.tabWidth, height: 44, border: 'none', borderRadius: 'var(--radius-sm)',
                background: moreOpen ? 'rgba(255,255,255,0.12)' : 'transparent', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: moreOpen ? 'var(--chrome-ink)' : 'var(--chrome-ink-soft)', flex: 'none',
              }}
            >
              <Icon name="more-horizontal" size={21} />
            </button>
            <div style={{ padding: 6 }}>
              {overflow.map((s) => (
                <MenuRow
                  key={s.id} icon={s.icon} label={s.label}
                  sub={badgeFor(s.id) > 0 ? `${badgeFor(s.id)} waiting` : undefined}
                  onClick={() => { setMoreOpen(false); navigate(s.to); }}
                />
              ))}
            </div>
          </Popover>
        )}
      </nav>

      <div style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 8 }}>
        {/* The bell is not width-gated. Everything else in this cluster has a
            second door — help repeats what the profile menu says, the profile
            menu is the avatar itself — but an unread notification has no other
            route, and hiding it below 1024 meant a narrow window silently
            stopped telling anyone anything. */}
        <Popover open={notifOpen} onClose={() => setNotifOpen(false)} width={340} label="Notifications">
            <UtilityButton
              icon="bell" on={notifOpen} badge={unreadCount}
              label={`Notifications${unreadCount ? `, ${unreadCount} unread` : ''}`}
              onClick={() => { const next = !notifOpen; openOnly(next ? 'notif' : null); if (next) markSeen(); }}
            />
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '13px 16px', borderBottom: '1px solid var(--divider)' }}>
                <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>Notifications</span>
                <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)' }}>{unreadCount} new</span>
              </div>
              <div style={{ display: 'flex', gap: 4, padding: '8px 12px', borderBottom: '1px solid var(--divider)' }}>
                {notifTabs.map((t) => {
                  const on = notifTab === t.key;
                  return (
                    <button
                      key={t.key} type="button" onClick={() => setNotifTab(t.key)}
                      style={{ flex: 1, padding: '5px 6px', borderRadius: 'var(--radius-sm)', border: 'none', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 11.5, background: on ? 'var(--blue-50)' : 'transparent', color: on ? 'var(--blue-700)' : 'var(--text-muted)' }}
                    >
                      {t.label}
                    </button>
                  );
                })}
              </div>
              <div className="racco-scroll" style={{ maxHeight: 320, overflowY: 'auto' }}>
                {shownEvents.length === 0 ? (
                  <div style={{ padding: '24px 16px', textAlign: 'center', fontSize: 12.5, color: 'var(--text-faint)' }}>No activity yet.</div>
                ) : shownEvents.map((n, i) => {
                  const meta = ACTION_META[n.action] || ACTION_META.created;
                  return (
                    <button
                      key={n.id ?? i} type="button" role="menuitem"
                      onClick={() => { setNotifOpen(false); navigate(eventDestination(n, role)); }}
                      style={{ width: '100%', textAlign: 'left', display: 'flex', gap: 11, padding: '11px 16px', borderBottom: i < shownEvents.length - 1 ? '1px solid var(--divider-row)' : 'none', border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--blue-50)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                    >
                      <span style={{ width: 26, height: 26, borderRadius: 'var(--radius-sm)', flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: meta.bg, color: meta.color }}>
                        <Icon name={meta.icon} size={14} />
                      </span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text-strong)', fontWeight: 600, lineHeight: 1.4 }}>{eventText(n)}</span>
                        <span style={{ display: 'block', fontSize: 11, color: 'var(--text-faint)', marginTop: 2 }}>{n.actor_label} · {timeAgo(n.created_at)}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
            </Popover>

        {layout.utilityIconsOn && (
          <Popover open={helpOpen} onClose={() => setHelpOpen(false)} width={318} label="Help">
              <UtilityButton icon="help-circle" label="Help" on={helpOpen} onClick={() => openOnly(helpOpen ? null : 'help')} />
              <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)', marginBottom: 6 }}>Shortcuts</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 12.5, color: 'var(--text-body)' }}>
                    <span className="racco-mono" style={{ fontSize: 11, fontWeight: 600, border: '1px solid var(--border)', borderRadius: 'var(--radius-xs)', padding: '1px 6px' }}>Ctrl K</span>
                    Search every child and colleague
                  </div>
                </div>
                <RoleAccessPanel to={role} />
                <p style={{ fontSize: 11.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                  Something missing that you expect to see? It is a role, not a fault &mdash; ask an
                  administrator to check what your account is assigned.
                </p>
              </div>
          </Popover>
        )}

        <Popover open={menuOpen} onClose={() => setMenuOpen(false)} width={290} label="Your account">
          <button
            type="button" onClick={() => openOnly(menuOpen ? null : 'menu')}
            aria-label="Your account" aria-expanded={menuOpen}
            style={{
              display: 'flex', alignItems: 'center', gap: 9, height: 38, padding: '0 10px 0 4px',
              border: 'none', borderRadius: 'var(--radius-pill)', background: 'var(--chrome-fill)',
              cursor: 'pointer', transition: 'background var(--dur-fast) var(--ease-out)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.22)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--chrome-fill)'; }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, borderRadius: '50%', background: 'var(--amber-400)', color: 'var(--amber-900)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 11.5, flex: 'none' }}>
              {initialsOf(name)}
            </span>
            {layout.identityOn && (
              <>
                <span style={{ lineHeight: 1.1, textAlign: 'left' }}>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12.5, color: 'var(--chrome-ink)', whiteSpace: 'nowrap' }}>{firstName}</span>
                  <span style={{ display: 'block', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 9.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--chrome-ink-soft)' }}>{role}</span>
                </span>
                <Icon name="chevron-down" size={17} style={{ color: 'var(--chrome-ink-soft)' }} />
              </>
            )}
          </button>
          <>
            <div style={{ padding: '12px 13px', display: 'flex', alignItems: 'center', gap: 11, borderBottom: '1px solid var(--divider)' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 42, height: 42, borderRadius: '50%', flex: 'none', background: 'var(--amber-400)', color: 'var(--amber-900)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 15 }}>
                {initialsOf(name)}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: roleDot }} />{role}
                </span>
              </span>
            </div>
            <div style={{ padding: 6 }}>
              {['Staff', 'Psychologist'].includes(role) && (
                <MenuRow icon="user-circle" label="See your profile" sub="Name, contact number, signature" onClick={() => { setMenuOpen(false); navigate('/profile'); }} />
              )}
              {role === 'Administrator' && (
                <MenuRow icon="settings" label="Settings" sub="Agency, notifications, assistant" onClick={() => { setMenuOpen(false); navigate('/settings'); }} />
              )}
              <MenuRow icon="key-round" label="Change password" sub="Takes effect immediately" onClick={openPw} />
            </div>
            <div style={{ height: 1, background: 'var(--divider)' }} />
            <div style={{ padding: 6 }}>
              <MenuRow icon="log-out" label="Log out" tone="danger" onClick={() => { setMenuOpen(false); setLogoutOpen(true); }} />
            </div>
          </>
        </Popover>
      </div>

      {pwOpen && (
        <div onClick={() => setPwOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 90, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
          <form onSubmit={submitPw} onClick={(e) => e.stopPropagation()} style={{ width: 420, maxWidth: '92%', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>Change Password</div>
              <button type="button" onClick={() => setPwOpen(false)} aria-label="Close" style={{ width: 30, height: 30, borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-muted)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="x" size={16} /></button>
            </div>
            {pwError && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{pwError}</Alert>}
            <FormField label="Current Password">
              <PasswordInput value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value.replace(/\s/g, '') })} placeholder="••••••••" autoComplete="current-password" required autoFocus />
            </FormField>
            <FormField label="New Password">
              <PasswordInput value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value.replace(/\s/g, '') })} placeholder="••••••••" leading={<Icon name="lock-keyhole" size={16} />} autoComplete="new-password" required />
            </FormField>
            <FormField label="Confirm New Password">
              <PasswordInput value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value.replace(/\s/g, '') })} placeholder="••••••••" leading={<Icon name="lock-keyhole" size={16} />} autoComplete="new-password" required />
            </FormField>
            <Button type="submit" variant="primary" fullWidth disabled={pwBusy} iconLeft={<Icon name="check" size={16} />}>
              {pwBusy ? 'Updating…' : 'Update Password'}
            </Button>
          </form>
        </div>
      )}

      {logoutOpen && (
        <ConfirmDialog
          onClose={() => setLogoutOpen(false)}
          onConfirm={() => { setLogoutOpen(false); logout(); navigate('/login'); }}
          tone="warning" icon={<Icon name="log-out" size={19} />}
          title="Log out?"
          description="You'll need to sign in again to get back in. Anything already saved is kept."
          confirmLabel="Log out" cancelLabel="Stay signed in"
        />
      )}
    </header>
  );
}
