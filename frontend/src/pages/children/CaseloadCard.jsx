import { useState } from 'react';
import { Icon, MiniBar } from '../../ui';

/* How many active cases each psychologist holds, under the Records table.
 *
 * It was a row of pills squeezed into the table's footer beside "Showing 0 of
 * 1 children", wrapping onto a second line, every count in red from five up
 * (owner, 30 Sep 2026: "not pain in the eye ... it's messing up this
 * module"). Now it is its own quiet section: one line per psychologist, the
 * busiest first, a bar to compare them at a glance, amber - not red - from
 * five, as the Assignment step's picker marks it. It can be folded away, and
 * stays folded on this device.
 */
const KEY = 'nacc-caseload-open';
const HEAVY = 5;

export default function CaseloadCard({ psychologists }) {
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem(KEY) !== 'false'; } catch { return true; }
  });
  const toggle = () => setOpen((v) => {
    try { localStorage.setItem(KEY, String(!v)); } catch { /* private browsing */ }
    return !v;
  });
  const rows = [...psychologists].sort((a, b) => (b.caseload - a.caseload) || String(a.name).localeCompare(String(b.name)));
  const most = Math.max(1, ...rows.map((p) => p.caseload || 0));
  const total = rows.reduce((n, p) => n + (p.caseload || 0), 0);

  return (
    <section aria-labelledby="caseload-title" style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: open ? '1px solid var(--divider)' : 'none' }}>
        <Icon name="users" size={16} style={{ color: 'var(--blue-600)', flex: 'none' }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 id="caseload-title" style={{ margin: 0, fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>
            Psychologist caseloads
          </h2>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {total} active case{total === 1 ? '' : 's'} across {rows.length} psychologist{rows.length === 1 ? '' : 's'}
            {open && <> · amber means {HEAVY} or more</>}
          </div>
        </div>
        <button
          type="button" onClick={toggle} aria-expanded={open} aria-controls="caseload-list"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '5px 10px', border: '1px solid var(--border)', borderRadius: 'var(--radius-control)', background: 'var(--surface)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12, color: 'var(--text-body)', cursor: 'pointer', flex: 'none' }}
        >
          {open ? 'Hide' : 'Show'}
          <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} />
        </button>
      </div>
      {open && (
        <div id="caseload-list" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '12px 28px', padding: '14px 16px 16px' }}>
          {rows.map((p) => {
            const heavy = p.caseload >= HEAVY;
            return (
              <div key={p.id} title={`${p.name}: ${p.caseload} active case${p.caseload === 1 ? '' : 's'}`}>
                <MiniBar
                  label={p.name}
                  value={p.caseload ? `${p.caseload} case${p.caseload === 1 ? '' : 's'}` : 'No cases'}
                  pct={`${Math.round(((p.caseload || 0) / most) * 100)}%`}
                  color={heavy ? 'var(--amber-500)' : 'var(--blue-500)'}
                />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
