import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useLayout } from '../context/LayoutContext';
import { Icon } from '../ui';
import { ageFrom, caseRef, initialsOf } from '../utils/child';

/* One search box, in the chrome, on every screen.
 *
 * Before this there was a filter input on Records and another on Monitoring,
 * each of which only searched the page you were already on — so finding a
 * child meant first knowing which screen they were on. This searches children
 * and, for administrators, staff accounts, from wherever you are.
 *
 * The roster is fetched once on first open and kept: it is a few dozen rows,
 * the server already scopes it to what this account may see, and typing
 * against a local list is what makes the arrow keys feel instant.
 */
const MAX_PER_GROUP = 5;

export default function GlobalSearch({ open, onOpenChange }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const layout = useLayout();
  const isAdmin = user?.role_name === 'Administrator';

  const [q, setQ] = useState('');
  const [children, setChildren] = useState([]);
  const [staff, setStaff] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  const close = useCallback(() => { onOpenChange(false); setQ(''); setCursor(0); }, [onOpenChange]);

  // Ctrl/Cmd-K from anywhere, including from inside a text field — the
  // shortcut is worth nothing if it only works when nothing has focus.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open || loaded) return;
    setLoaded(true);
    api.get('/children/?include_archived=true').then((r) => setChildren(r.data || [])).catch(() => {});
    if (isAdmin) api.get('/users/').then((r) => setStaff(r.data || [])).catch(() => {});
  }, [open, loaded, isAdmin]);

  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) close(); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open, close]);

  const term = q.trim().toLowerCase();

  const childHits = useMemo(() => {
    if (!term) return [];
    return children
      .filter((c) => (c.fullname || '').toLowerCase().includes(term) || caseRef(c.id).toLowerCase().includes(term))
      .slice(0, MAX_PER_GROUP)
      .map((c) => {
        const age = ageFrom(c.birth_date);
        const bits = [
          c.status === 'inactive' ? 'Archived' : age != null ? `${age} y` : null,
          c.case_type,
          c.psychologist_name,
        ].filter(Boolean);
        return {
          key: `c${c.id}`, name: c.fullname, meta: bits.join(' · '),
          ref: caseRef(c.id), initials: initialsOf(c.fullname),
          to: `/report/child/${c.id}`,
        };
      });
  }, [children, term]);

  const staffHits = useMemo(() => {
    if (!term) return [];
    return staff
      .filter((u) => (u.fullname || '').toLowerCase().includes(term) || (u.email || '').toLowerCase().includes(term))
      .slice(0, MAX_PER_GROUP)
      .map((u) => ({
        key: `u${u.id}`, name: u.fullname || u.email, initials: initialsOf(u.fullname || u.email),
        meta: [u.role_name, (u.status || '').toLowerCase()].filter(Boolean).join(' · '),
        ref: '', to: '/users',
      }));
  }, [staff, term]);

  const flat = useMemo(() => childHits.concat(staffHits), [childHits, staffHits]);
  useEffect(() => { setCursor(0); }, [term]);

  const go = (hit) => { if (!hit) return; close(); navigate(hit.to); };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((i) => Math.min(flat.length - 1, i + 1)); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((i) => Math.max(0, i - 1)); return; }
    if (e.key === 'Enter') { e.preventDefault(); go(flat[cursor]); }
  };

  const groupLabel = {
    fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 'var(--text-3xs)',
    letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--text-faint)', padding: '10px 8px 4px',
  };

  const row = (hit, i) => (
    <button
      key={hit.key}
      type="button"
      onClick={() => go(hit)}
      onMouseEnter={() => setCursor(i)}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '7px 8px',
        borderRadius: 'var(--radius-sm)', border: 'none', cursor: 'pointer', textAlign: 'left',
        background: cursor === i ? 'var(--blue-50)' : 'transparent', fontFamily: 'var(--font-sans)',
      }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 11 }}>
        {hit.initials}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{hit.name}</span>
        <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{hit.meta}</span>
      </span>
      {hit.ref && <span className="racco-mono" style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-faint)', flex: 'none' }}>{hit.ref}</span>}
    </button>
  );

  return (
    <div ref={boxRef} style={{ position: 'relative', flex: 'none' }}>
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-label="Search children and staff"
        aria-expanded={open}
        style={{
          display: 'flex', alignItems: 'center', gap: 9,
          width: layout.searchWidth, height: 36, padding: '0 8px 0 12px',
          border: '1px solid var(--chrome-line)', borderRadius: 'var(--radius-pill)',
          background: 'rgba(255,255,255,0.09)', cursor: 'pointer', textAlign: 'left',
          transition: 'background var(--dur-fast) var(--ease-out)',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.16)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.09)'; }}
      >
        <Icon name="search" size={17} style={{ color: 'var(--chrome-ink-soft)' }} />
        <span style={{ flex: 1, minWidth: 0, fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 13, color: 'var(--chrome-ink-soft)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          Search children, staff, reports…
        </span>
        {layout.searchHintOn && (
          <span className="racco-mono" style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--chrome-ink-dim)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 'var(--radius-xs)', padding: '1px 5px', whiteSpace: 'nowrap', flex: 'none' }}>
            Ctrl K
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Search"
          style={{
            position: 'absolute', top: 44, left: 0, width: layout.searchPanelWidth, maxWidth: '80vw',
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)',
            boxShadow: 'var(--shadow-xl)', overflow: 'hidden', zIndex: 60,
            animation: 'racco-pop-in var(--dur-base) var(--ease-out)',
          }}
        >
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--divider)', display: 'flex', alignItems: 'center', gap: 9 }}>
            <Icon name="search" size={17} style={{ color: 'var(--blue-600)' }} />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Name, case reference, email…"
              aria-label="Search children and staff"
              style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 14, color: 'var(--text-strong)', height: 30 }}
            />
            {term && (
              <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 'var(--text-3xs)', letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--text-faint)', whiteSpace: 'nowrap' }}>
                {flat.length} result{flat.length === 1 ? '' : 's'}
              </span>
            )}
          </div>

          <div className="racco-scroll" style={{ padding: '2px 8px 10px', maxHeight: 360, overflowY: 'auto' }}>
            {!term && (
              <p style={{ padding: '18px 8px', fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                Type a child&rsquo;s name or a case reference{isAdmin ? ', or a colleague&rsquo;s name' : ''}. Results are
                limited to the records your account may open.
              </p>
            )}
            {term && flat.length === 0 && (
              <p style={{ padding: '18px 8px', fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                Nothing matches &ldquo;{q.trim()}&rdquo;.
              </p>
            )}
            {childHits.length > 0 && <div style={groupLabel}>Children</div>}
            {childHits.map((h, i) => row(h, i))}
            {staffHits.length > 0 && <div style={groupLabel}>Staff</div>}
            {staffHits.map((h, i) => row(h, childHits.length + i))}
          </div>

          <div style={{ padding: '9px 14px', background: 'var(--ink-50)', borderTop: '1px solid var(--divider)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)', display: 'flex', gap: 14 }}>
            <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>&uarr;&darr;</span> navigate</span>
            <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>&crarr;</span> open</span>
            <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>esc</span> close</span>
          </div>
        </div>
      )}
    </div>
  );
}
