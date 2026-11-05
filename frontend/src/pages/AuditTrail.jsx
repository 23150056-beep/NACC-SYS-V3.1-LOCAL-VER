import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import {
  Alert, Badge, Button, EmptyState, FilterPills, Icon, Input, PAGE, PageHeader, Select, Skeleton,
} from '../ui';
import { ACTION_META, eventDestination, eventText } from '../utils/activity';
import { clock, exactDate, shortDate } from '../utils/time';

/* The audit trail: every line the feed behind the bell and the right rail
 * leaves out of view, filtered and paged (backend activity/views.py `trail`).
 * Every filter lives in the address, so a link reproduces the view. */

const PAGE_SIZE = 50;

const ACTION_LABEL = {
  created: 'Created', updated: 'Edited', archived: 'Archived', login: 'Signed in',
  requested: 'Asked a psychologist', accepted: 'Accepted a case', declined: 'Declined a case',
  withdrawn: 'Withdrew a request', finalized: 'Finalized a case study', reopened: 'Reopened',
  removed: 'Removed a duplicate', exported: 'Exported a list', transferred: 'Moved records',
};

const CATEGORY_PILLS = [
  { key: 'all', label: 'All' },
  { key: 'record', label: 'Records' },
  { key: 'user', label: 'Accounts' },
  { key: 'security', label: 'Security' },
];
const CATEGORY_BADGE = {
  record: { label: 'Record', tone: 'brand' },
  user: { label: 'Account', tone: 'warning' },
  security: { label: 'Security', tone: 'danger' },
};

const SUBTITLE = {
  Administrator: 'Every record, account and security event in the agency.',
  Staff: 'What happened to your own records and what was addressed to you.',
  Psychologist: 'What was addressed to you.',
};

const FILTER_KEYS = ['q', 'category', 'action', 'from', 'to', 'child'];

function sentence(data, fallback) {
  if (data && typeof data === 'object') {
    const v = Object.values(data)[0];
    if (typeof v === 'string') return v;
    if (Array.isArray(v) && typeof v[0] === 'string') return v[0];
    if (typeof data.detail === 'string') return data.detail;
  }
  return fallback;
}

export default function AuditTrail() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const role = user?.role_name || 'Staff';
  const isAdmin = role === 'Administrator';
  const [params, setParams] = useSearchParams();

  const q = params.get('q') || '';
  const category = isAdmin ? params.get('category') || '' : '';
  const action = params.get('action') || '';
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const child = params.get('child') || '';
  const page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);

  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const seq = useRef(0);
  const top = useRef(null);
  const statusRef = useRef(null);
  const firstPage = useRef(page);

  // The search box types into its own state; the address follows 300 ms later.
  const [qText, setQText] = useState(q);
  useEffect(() => { setQText(q); }, [q]);

  const change = useCallback((patch, { keepPage = false } = {}) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(patch).forEach(([k, v]) => {
        if (v === '' || v == null || v === 'all' || (k === 'page' && Number(v) <= 1)) next.delete(k);
        else next.set(k, String(v));
      });
      if (!keepPage && !('page' in patch)) next.delete('page');
      return next;
    }, { replace: true });
  }, [setParams]);

  useEffect(() => {
    if (qText.trim() === q.trim()) return undefined;
    const t = setTimeout(() => change({ q: qText.trim() }), 300);
    return () => clearTimeout(t);
  }, [qText, q, change]);

  const query = [category, action, q, from, to, child, page].join('|');
  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError('');
    const p = { page, page_size: PAGE_SIZE };
    if (category) p.category = category;
    if (action) p.action = action;
    if (q) p.q = q;
    if (from) p.date_from = from;
    if (to) p.date_to = to;
    if (child) p.child = child;
    api.get('/activity/trail/', { params: p })
      .then((r) => { if (mine === seq.current) { setData(r.data); setLoading(false); } })
      .catch((e) => {
        if (mine !== seq.current) return;
        setError(sentence(e.response?.data, 'The audit trail could not be loaded.'));
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, reload]);

  // A new page starts at its top, not wherever the last one was scrolled to.
  useEffect(() => {
    if (firstPage.current === page) return;
    firstPage.current = page;
    top.current?.scrollIntoView?.({ block: 'start' });
    statusRef.current?.focus?.({ preventScroll: true });
  }, [page]);

  const active = FILTER_KEYS.some((k) => (k === 'category' ? category : params.get(k)));
  const clear = () => { setQText(''); setParams(new URLSearchParams(), { replace: true }); };

  const rows = data?.results || [];
  const count = data?.count ?? 0;
  const size = data?.page_size || PAGE_SIZE;
  const shown = data?.page || page;
  const firstN = count === 0 || rows.length === 0 ? 0 : (shown - 1) * size + 1;
  const lastN = Math.min(count, (shown - 1) * size + rows.length);
  const lastPage = Math.max(1, Math.ceil(count / size));
  const pastEnd = !!data && rows.length === 0 && count > 0;
  // A move or duplicate-removal line's label is a whole sentence; take the name from an ordinary line.
  const childLabel = rows.find((r) => r.entity_label && !['transferred', 'removed'].includes(r.action)
    && ['Child', 'CaseStudy', 'Assignment'].includes(r.entity_type))?.entity_label;
  const goTo = (n) => change({ page: n }, { keepPage: true });

  return (
    <div style={PAGE}>
      <div ref={top} style={{ scrollMarginTop: 8 }} />
      <PageHeader title="Audit trail" subtitle={SUBTITLE[role] || SUBTITLE.Staff} />

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', overflow: 'hidden' }}>
        <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10, background: 'var(--ink-25)', borderBottom: '1px solid var(--divider)' }}>
          {isAdmin && (
            <FilterPills
              label="Category" value={category || 'all'} options={CATEGORY_PILLS}
              onChange={(k) => change({ category: k })}
            />
          )}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div style={{ flex: '1 1 220px', minWidth: 0 }}>
              <Input
                size="sm" value={qText} onChange={(e) => setQText(e.target.value)} maxLength={100}
                placeholder="Search by name or sentence" aria-label="Search the audit trail"
                leading={<Icon name="search" size={15} />}
              />
            </div>
            <div style={{ flex: '0 1 190px', minWidth: 140 }}>
              <Select size="sm" value={action} aria-label="Filter by action" onChange={(e) => change({ action: e.target.value })}>
                <option value="">All actions</option>
                {Object.keys(ACTION_META).map((a) => <option key={a} value={a}>{ACTION_LABEL[a] || a}</option>)}
              </Select>
            </div>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', flex: '0 1 140px', minWidth: 130 }}>
              From
              <Input size="sm" type="date" value={from} max={to || undefined} onChange={(e) => change({ from: e.target.value })} />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', flex: '0 1 140px', minWidth: 130 }}>
              To
              <Input size="sm" type="date" value={to} min={from || undefined} onChange={(e) => change({ to: e.target.value })} />
            </label>
            {active && <Button variant="outline" size="sm" onClick={clear}>Clear filters</Button>}
          </div>
          {child && (
            <div>
              <button
                type="button" className="racco-btn" onClick={() => change({ child: '' })}
                aria-label={`Remove the filter: ${childLabel || "one child's record"} only`}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 'var(--radius-pill)', border: '1px solid var(--blue-200)', background: 'var(--blue-50)', color: 'var(--blue-700)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}
              >
                One child&rsquo;s record only{childLabel ? ` · ${childLabel}` : ''}
                <Icon name="x" size={13} />
              </button>
            </div>
          )}
        </div>

        <div ref={statusRef} tabIndex={-1} role="status" aria-live="polite" style={{ outline: 'none', padding: '8px 14px', fontSize: 12, color: 'var(--text-muted)', borderBottom: '1px solid var(--divider-row)', minHeight: 33 }}>
          {data && !error ? (count === 0 ? 'No events' : pastEnd ? `${count.toLocaleString()} events, none on page ${shown}` : `Showing ${firstN.toLocaleString()}-${lastN.toLocaleString()} of ${count.toLocaleString()}`) : ''}
        </div>

        {error ? (
          <div style={{ padding: 14 }}>
            <Alert tone="danger">
              {error}{' '}
              <Button variant="outline" size="sm" onClick={() => setReload((n) => n + 1)}>Try again</Button>
            </Alert>
          </div>
        ) : !data && loading ? (
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }} aria-busy="true">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} height={34} />)}
          </div>
        ) : pastEnd ? (
          <EmptyState
            icon={<Icon name="history" size={24} />}
            title="This page is past the end"
            description={`There are ${count.toLocaleString()} events, on ${lastPage} ${lastPage === 1 ? 'page' : 'pages'}.`}
            action={<Button variant="outline" size="sm" onClick={() => change({ page: 1 }, { keepPage: true })}>Back to page 1</Button>}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Icon name="history" size={24} />}
            title={active ? 'Nothing matches these filters' : 'No events yet'}
            description={active ? 'Try a wider date range or fewer filters.' : 'Events appear here as records, accounts and sign-ins change.'}
            action={active ? <Button variant="outline" size="sm" onClick={clear}>Clear filters</Button> : null}
          />
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, opacity: loading ? 0.6 : 1, transition: 'opacity var(--dur-fast)' }}>
            {rows.map((e) => {
              const meta = ACTION_META[e.action] || ACTION_META.created;
              const cat = CATEGORY_BADGE[e.category];
              return (
                <li key={e.id} style={{ display: 'flex', gap: 11, alignItems: 'flex-start', padding: '11px 14px', borderBottom: '1px solid var(--divider-row)' }}>
                  <span style={{ width: 28, height: 28, borderRadius: 'var(--radius-sm)', flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: meta.bg, color: meta.color }}>
                    <Icon name={meta.icon} size={14} />
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <button
                      type="button" className="racco-btn" onClick={() => navigate(eventDestination(e, role))}
                      style={{ display: 'block', maxWidth: '100%', padding: 0, border: 'none', background: 'transparent', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 13, lineHeight: 1.4, color: 'var(--text-strong)', overflowWrap: 'anywhere' }}
                    >
                      {eventText(e)}
                    </button>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 8px', alignItems: 'center', marginTop: 3, fontSize: 11.5, color: 'var(--text-muted)' }}>
                      <span>{e.actor_label || 'System'}</span>
                      <span aria-hidden="true">&middot;</span>
                      <time dateTime={e.created_at} title={exactDate(e.created_at)}>{shortDate(e.created_at)}, {clock(e.created_at)}</time>
                      {isAdmin && cat && <Badge tone={cat.tone} size="sm">{cat.label}</Badge>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {/* The nav's right padding keeps Next clear of the floating assistant button. */}
        {data && count > size && (
          <nav aria-label="Pages" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '10px 76px 10px 14px', background: 'var(--ink-25)', borderTop: '1px solid var(--divider)' }}>
            <Button variant="outline" size="sm" iconLeft={<Icon name="chevron-left" size={14} />} aria-disabled={page <= 1 || undefined} style={page <= 1 ? { opacity: 0.5, cursor: "not-allowed" } : undefined} onClick={() => { if (page > 1) goTo(page - 1); }}>Previous</Button>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Page {page} of {lastPage}</span>
            <Button variant="outline" size="sm" iconRight={<Icon name="chevron-right" size={14} />} aria-disabled={page >= lastPage || undefined} style={page >= lastPage ? { opacity: 0.5, cursor: "not-allowed" } : undefined} onClick={() => { if (page < lastPage) goTo(page + 1); }}>Next</Button>
          </nav>
        )}
      </div>
    </div>
  );
}
