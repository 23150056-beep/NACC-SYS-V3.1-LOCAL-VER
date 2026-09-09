import { useMemo } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useLayout } from '../context/LayoutContext';
import { useCensus } from '../context/CensusContext';
import { Icon, ROLE_META } from '../ui';
import { railIsActive, railRowsFor, screenIdFor } from '../config/nav';
import { initialsOf, PURPOSE_LABEL } from '../utils/child';

/* The left rail: every destination, labelled, in the order the work happens.
 *
 * It is a rail rather than a panel — no white slab down the side of the
 * window, just items sitting on the app's own ground, with the current one
 * lifted onto a white chip. That is what lets it shrink to 60px of icons at
 * 1080 and disappear at 900 without the layout visibly losing a wall.
 *
 * The header's tab strip carries the five or six places people go constantly;
 * this carries all of them. Both read the same list in config/nav.js, so a
 * destination cannot exist in one and not the other.
 */

const GAP_DOT = {
  danger: 'var(--red-500)',
  warning: 'var(--amber-500)',
  info: 'var(--blue-400)',
};

function railRowStyle({ active, icons }) {
  return {
    position: 'relative', width: '100%', display: 'flex', alignItems: 'center',
    justifyContent: icons ? 'center' : 'flex-start', gap: 11,
    padding: icons ? '9px 0' : '8px 10px',
    border: `1px solid ${active ? 'var(--blue-100)' : 'transparent'}`,
    borderRadius: 'var(--radius-control)',
    background: active ? 'var(--surface)' : 'transparent',
    color: active ? 'var(--blue-700)' : 'var(--text-body)',
    fontFamily: 'var(--font-sans)', fontWeight: active ? 800 : 600, fontSize: 13.5,
    textDecoration: 'none', cursor: 'pointer', textAlign: 'left',
    boxShadow: active ? 'var(--shadow-card)' : 'none',
    transition: 'background var(--dur-fast) var(--ease-out)',
  };
}

export default function Sidebar() {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const layout = useLayout();
  const { stats, pendingAccess } = useCensus();

  const role = user?.role_name || 'Staff';
  const name = user?.fullname || user?.username || 'User';
  const screenId = screenIdFor(location.pathname);
  const labels = layout.railLabels;
  const rows = railRowsFor(role);
  const roleDot = (ROLE_META[role] || ROLE_META.Staff).color;

  /* Shortcuts: the children this account is most likely to need to open in
   * the next ten minutes, which is not a list anybody has to curate. Today's
   * appointments first — those are happening — then whichever care gaps the
   * deterministic rules raised. Same rules the Dashboard shows, so the rail
   * and the page can never name a different urgent child. */
  const pinned = useMemo(() => {
    const seen = new Set();
    const out = [];
    const push = (id, entry) => {
      if (!id || seen.has(id) || out.length >= 3) return;
      seen.add(id);
      out.push(entry);
    };
    (stats.today_schedule || []).forEach((a) => push(a.child_id, {
      id: a.child_id, name: a.child_name,
      meta: `${a.time} · ${PURPOSE_LABEL[a.purpose] || a.purpose}`,
      dot: a.status === 'completed' ? 'var(--success-500)' : 'var(--amber-500)',
    }));
    (stats.care_gaps || []).forEach((g) => push(g.child_id, {
      id: g.child_id, name: g.child_name, meta: g.message,
      dot: GAP_DOT[g.severity] || 'var(--blue-400)',
    }));
    return out;
  }, [stats.today_schedule, stats.care_gaps]);

  const badgeFor = (id) => (id === 'users' ? pendingAccess : 0);

  return (
    <aside
      className="racco-scroll"
      aria-label="All destinations"
      style={{
        width: layout.leftRailWidth, flex: 'none', display: 'flex', flexDirection: 'column',
        gap: 6, overflowY: 'auto', overflowX: 'hidden',
      }}
    >
      {labels && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', borderRadius: 'var(--radius-control)', background: 'var(--surface)', border: '1px solid var(--border)', flex: 'none' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, borderRadius: '50%', flex: 'none', background: 'var(--amber-400)', color: 'var(--amber-900)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 13 }}>
            {initialsOf(name)}
          </span>
          <span style={{ minWidth: 0 }}>
            <span style={{ display: 'block', fontWeight: 800, fontSize: 13.5, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 700, fontSize: 10.5, color: 'var(--text-muted)' }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: roleDot }} />{role}
            </span>
          </span>
        </div>
      )}

      <nav style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {rows.map((r, i) => {
          if (r.kind === 'section') {
            // Icon mode has no room for a word, and the gap alone reads as
            // the grouping it is.
            if (!labels) return <div key={`s${i}`} style={{ height: 8 }} />;
            return (
              <div key={`s${i}`} style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 'var(--text-3xs)', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-faint)', padding: '11px 10px 3px' }}>
                {r.label}
              </div>
            );
          }
          const s = r.screen;
          const active = railIsActive(s.id, screenId);
          const badge = badgeFor(s.id);
          return (
            <Link
              key={s.id} to={s.to} title={s.label} aria-current={active ? 'page' : undefined}
              style={railRowStyle({ active, icons: !labels })}
              onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,0.6)'; }}
              onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
            >
              <Icon name={s.icon} size={19} strokeWidth={active ? 2.2 : 1.9} />
              {labels && <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.label}</span>}
              {labels && badge > 0 && (
                <span className="racco-mono" style={{ minWidth: 19, height: 18, padding: '0 5px', borderRadius: 'var(--radius-pill)', background: 'var(--red-500)', color: '#fff', fontSize: 10, fontWeight: 800, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
                  {badge}
                </span>
              )}
              {/* In icon mode the number has nowhere to sit, so it becomes a
                  dot: still "there is something here", without a digit at 8px. */}
              {!labels && badge > 0 && (
                <span style={{ position: 'absolute', top: 6, right: 12, width: 8, height: 8, borderRadius: '50%', background: 'var(--red-500)' }} />
              )}
            </Link>
          );
        })}
      </nav>

      {labels && pinned.length > 0 && (
        <>
          <div style={{ height: 1, background: 'var(--border-strong)', margin: '12px 8px 4px', flex: 'none' }} />
          <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 'var(--text-3xs)', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-faint)', padding: '2px 10px 4px' }}>
            Needs you today
          </div>
          {pinned.map((p) => (
            <button
              key={p.id} type="button" onClick={() => navigate(`/report/child/${p.id}`)}
              title={`${p.name} — ${p.meta}`}
              style={{
                display: 'flex', alignItems: 'center', gap: 9, width: '100%', padding: '7px 9px',
                border: '1px solid transparent', borderRadius: 'var(--radius-control)',
                background: 'transparent', cursor: 'pointer', textAlign: 'left',
                fontFamily: 'var(--font-sans)', flex: 'none',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface)'; e.currentTarget.style.borderColor = 'var(--border)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'transparent'; }}
            >
              <span style={{ position: 'relative', flex: 'none' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, borderRadius: '50%', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 10.5 }}>
                  {initialsOf(p.name)}
                </span>
                <span style={{ position: 'absolute', right: -1, bottom: -1, width: 9, height: 9, borderRadius: '50%', background: p.dot, border: '2px solid var(--bg-app)' }} />
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 700, fontSize: 12.5, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</span>
                <span style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.meta}</span>
              </span>
            </button>
          ))}
        </>
      )}
    </aside>
  );
}
