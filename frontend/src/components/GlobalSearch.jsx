import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import api from '../api/client';
import { screensFor } from '../config/nav';
import { useAuth } from '../context/AuthContext';
import { useCensus } from '../context/CensusContext';
import { useLayout } from '../context/LayoutContext';
import { useToast } from '../context/ToastContext';
import { Icon, roleLabel } from '../ui';
import { ageFrom, caseRef, initialsOf } from '../utils/child';
import { loadAll } from '../utils/load';
import { matchChildren, matchesCommand, matchStaff, recentChildIds, rememberChild } from '../utils/search';
import './header-phone.css';

/* One search box, in the chrome, on every screen - and the quickest way to
 * DO something, not only to find somebody (owner, 11 Oct 2026).
 *
 * - The pill in the header IS the field: it is typed in where it sits, and the
 *   results drop down under it (owner, 11 Oct 2026). It used to be a button
 *   that opened a panel with a second box inside, so the first one looked like
 *   a field and acted like a button. Nothing in the panel takes text.
 * - The panel swallows mousedown. A click on a row, a label or empty space
 *   would otherwise move focus off the field, and the arrow keys - which are
 *   read there - would stop working until it was clicked again. Clicks still
 *   arrive. For the same reason nothing in the panel is a tab stop: Tab
 *   leaves the bar and closes it, and the rows are options the field names
 *   through aria-activedescendant, not buttons to be focused.
 * - Children are found by more than the name: the custodian, the barangay,
 *   the birth year, the case type, who works the case (utils/search.js), and
 *   a match outside the name says where it was found.
 * - A child has quick actions: highlight them and press the right arrow (or
 *   click the arrow on the row) for Open record, Book a session, Upload, and
 *   Edit - each through the link that screen already understands, so the
 *   drawer opens with the child chosen.
 * - Actions and screens are found by typing them: "book", "upload", "add".
 * - Before anything is typed it shows the children opened lately, what is
 *   waiting on this account, and its actions.
 *
 * - Under 480px there is no room for the pill beside the tabs, so it is a
 *   magnifier button (`phone`, LayoutContext). Pressing it puts the SAME field
 *   and panel across the full width of the header, over the tabs, with a Close
 *   button because a phone has no Esc. Nothing else changes: it is the one
 *   input, so the keys, the focus rules and the listbox are untouched.
 *
 * The roster is fetched each time the box opens, and the last copy is shown
 * meanwhile: the server already scopes it to what this account may see, a
 * child added since is found, and typing against a local list keeps the arrow
 * keys instant. Every action is the role's own, as the Dashboard offers it;
 * the server checks each one again.
 */
const MAX_CHILDREN = 6;
const MAX_STAFF = 5;
const MAX_RECENT = 5;
const MAX_NEEDS = 5;

function actionsFor(role, pendingAccess) {
  const list = role === 'Psychologist' ? [
    { key: 'a-pre', label: 'Start a pre-assessment', icon: 'clipboard-list', to: '/pre-assessment', keywords: 'begin new assess assessment' },
    { key: 'a-avail', label: 'Add my availability', icon: 'clock', to: '/schedule?availability=1', keywords: 'hours schedule time open' },
    { key: 'a-book', label: 'Book a session', icon: 'calendar-plus', to: '/schedule?book=1', keywords: 'appointment schedule new' },
    { key: 'a-upload', label: 'Upload a report', icon: 'upload', to: '/reports?upload=1', keywords: 'file document attach' },
  ] : [
    { key: 'a-add', label: 'Add a record', icon: 'user-plus', to: '/children?openCreate=1', keywords: 'new child intake create register' },
    { key: 'a-book', label: 'Book an appointment', icon: 'calendar-plus', to: '/schedule?book=1', keywords: 'session schedule new' },
    { key: 'a-upload', label: 'Upload a case referral', icon: 'upload', to: '/reports?upload=1', keywords: 'file document attach' },
  ];
  if (role === 'Administrator') {
    list.push({
      key: 'a-access', label: 'Review access requests', icon: 'user-check', to: '/users?tab=requests',
      keywords: 'approve sign-up signup pending accounts new users',
      meta: pendingAccess > 0 ? `${pendingAccess} waiting` : null,
    });
  }
  return list.map((a) => ({ ...a, kind: 'action' }));
}

/* What the roster says is waiting on this account, most pressing first. Read
 * from the list already fetched - nothing here asks the server again. */
function needsFor(role, children, userId) {
  const rows = [];
  for (const c of children) {
    if (c.status !== 'active') continue;
    if (role === 'Psychologist') {
      if (String(c.psychologist) === String(userId)
        && ['No Consent Yet', 'Not Yet Pre-Assessed'].includes(c.pre_assessment_status)) {
        rows.push({ c, rank: 1, reason: 'Pre-assessment not started' });
      }
      continue;
    }
    if (role === 'Administrator' && !c.social_worker) {
      rows.push({ c, rank: 0, reason: 'No social worker yet' });
    } else if (!c.psychologist && !c.pending_assignment) {
      rows.push({
        c, rank: 1,
        reason: c.declined_assignment
          ? `Declined by ${c.declined_assignment.psychologist_name || 'the psychologist'}`
          : 'No psychologist yet',
      });
    } else if (!c.has_case_referral) {
      rows.push({ c, rank: 2, reason: 'No case referral yet' });
    }
  }
  return rows.sort((a, b) => (a.rank - b.rank) || String(a.c.fullname).localeCompare(String(b.c.fullname)));
}

// `field` is where a match outside the name was found; what it already
// names is not said twice on the line.
function childItem(c, { field, ...extra } = {}) {
  const age = ageFrom(c.birth_date);
  return {
    kind: 'child', key: `c${c.id}`, child: c, name: c.fullname, ref: caseRef(c.id),
    initials: initialsOf(c.fullname), to: `/report/child/${c.id}`,
    meta: [
      c.status === 'inactive' ? 'Archived' : age != null && field !== 'born' ? `${age} y` : null,
      field !== 'case' && c.case_type,
      field !== 'psych' && c.psychologist_name,
    ].filter(Boolean).join(' · '),
    ...extra,
  };
}

export default function GlobalSearch({ open, onOpenChange }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const layout = useLayout();
  const toast = useToast();
  const { pendingAccess, pendingAssignments, refreshPendingAccess, refreshPendingAssignments } = useCensus();
  const role = user?.role_name;
  const isAdmin = role === 'Administrator';
  const isStaff = role === 'Staff';
  const isPsych = role === 'Psychologist';

  const [q, setQ] = useState('');
  const [children, setChildren] = useState([]);
  const [staff, setStaff] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [cursor, setCursor] = useState(0);
  // The child whose quick actions are showing, and where to return to.
  const [focus, setFocus] = useState(null);
  const [recent, setRecent] = useState(() => recentChildIds(user?.id));
  const [hover, setHover] = useState(false);
  const boxRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  // Where the pointer last was, to tell a pointer that moved from a list that
  // moved under it.
  const pointerAt = useRef(null);
  // The magnifier, and whether the last close came from Esc or Close - those
  // give the focus back to it. A Tab away must not: focus is already leaving.
  const iconRef = useRef(null);
  const returnFocus = useRef(false);
  const phone = !!layout.phone;

  const reset = useCallback(() => {
    onOpenChange(false); setQ(''); setCursor(0); setFocus(null);
  }, [onOpenChange]);
  // Closing lets go of the field too, so the keys go back to the page.
  const close = useCallback(() => { reset(); inputRef.current?.blur(); }, [reset]);
  const closeAndReturn = useCallback(() => { returnFocus.current = true; close(); }, [close]);
  useEffect(() => {
    if (!open && returnFocus.current) { returnFocus.current = false; iconRef.current?.focus(); }
  }, [open, phone]);

  // Closed for ANY reason - this component, another header popover opening,
  // a new screen - the text goes with it. A query left in the bar with no
  // panel under it would look like a search that found nothing.
  useEffect(() => {
    if (!open) { setQ(''); setCursor(0); setFocus(null); }
  }, [open]);

  // Ctrl/Cmd-K from anywhere, including from inside a text field — the
  // shortcut is worth nothing if it only works when nothing has focus. Only
  // the plain chord: Ctrl+Shift+K and Ctrl+Alt+K belong to the browser, and
  // Ctrl+Alt is AltGr on some keyboards.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (open) { closeAndReturn(); return; }
        // A drawer or dialog owns the keyboard. Opened over it, the panel
        // would sit under its scrim and take the focus out of it.
        if (document.querySelector('[aria-modal="true"]')) return;
        onOpenChange(true);
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onOpenChange, close, closeAndReturn]);

  // Every child's record opened, from here or anywhere else, is "recent".
  useEffect(() => {
    const m = location.pathname.match(/^\/report\/child\/(\d+)/);
    if (m && user?.id) setRecent(rememberChild(user.id, m[1]));
  }, [location.pathname, user?.id]);
  useEffect(() => { setRecent(recentChildIds(user?.id)); }, [user?.id]);

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    refreshPendingAccess();
    refreshPendingAssignments();
    // Silence here is worse than elsewhere: search would simply find
    // nothing, which reads as "no such child" rather than "not loaded".
    loadAll(toast, [
      () => api.get('/children/?include_archived=true').then((r) => { if (live) setChildren(r.data || []); }),
      isAdmin && (() => api.get('/users/').then((r) => { if (live) setStaff(r.data || []); })),
    ], 'Search could not load. Results will be incomplete until you refresh.')
      .then(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, [open, isAdmin, toast, refreshPendingAccess, refreshPendingAssignments]);

  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    // The path is read, not `contains(e.target)`: a press on the Ctrl K chip
    // opens the panel, which unmounts the chip, and by the time this runs the
    // target is no longer in the box.
    const onDoc = (e) => { if (boxRef.current && !e.composedPath().includes(boxRef.current)) close(); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open, close]);

  const term = q.trim();
  const actions = useMemo(() => actionsFor(role, pendingAccess), [role, pendingAccess]);
  const screens = useMemo(() => screensFor(role).map((s) => ({
    kind: 'screen', key: `s-${s.id}`, label: s.label, icon: s.icon, to: s.to, keywords: 'go to open screen page',
  })), [role]);

  /* The child's quick actions, each only where this account may do it - the
   * same rules the screens use (Children.jsx canEditRecord, the Calendar's
   * referral gate). */
  const childActions = useCallback((c) => {
    const active = c.status === 'active';
    const mine = isPsych && String(c.psychologist) === String(user?.id);
    const items = [{ key: 'x-open', label: 'Open record', icon: 'folder-open', to: `/report/child/${c.id}` }];
    if (active && c.has_case_referral) {
      items.push({ key: 'x-book', label: 'Book a session', icon: 'calendar-plus', to: `/schedule?book=1&child=${c.id}` });
    }
    if (active && (isAdmin || isStaff)) {
      items.push({
        key: 'x-referral', label: 'Upload a case referral', icon: 'upload', to: `/reports?upload=1&child=${c.id}`,
        meta: c.has_case_referral ? null : 'Needed before a session can be booked',
      });
    }
    if (active && mine) {
      items.push({ key: 'x-report', label: 'Upload a report', icon: 'upload', to: `/reports?upload=1&child=${c.id}` });
    }
    if (isAdmin || isStaff || (mine && active)) {
      items.push({ key: 'x-edit', label: 'Edit record', icon: 'pencil', to: `/children?edit=${c.id}` });
    }
    const notes = [];
    if (!active) notes.push('This case is closed. Reopen it from Records to book or upload.');
    else if (!c.has_case_referral) notes.push('No case referral on file yet, so a session cannot be booked.');
    return { items: items.map((i) => ({ ...i, kind: 'action' })), notes };
  }, [isAdmin, isStaff, isPsych, user?.id]);

  /* What the panel shows: groups of rows, plus lines that are read, not
   * picked (`notes`). */
  const groups = useMemo(() => {
    if (focus) {
      const { items, notes } = childActions(focus.child);
      return [{ label: null, items, notes }];
    }
    if (!term) {
      const byId = new Map(children.map((c) => [String(c.id), c]));
      const recentItems = recent.map((id) => byId.get(id)).filter(Boolean).slice(0, MAX_RECENT)
        .map((c) => childItem(c, { key: `r${c.id}` }));
      const needs = needsFor(role, children, user?.id);
      const needItems = needs.slice(0, MAX_NEEDS).map(({ c, reason }) => childItem(c, { key: `n${c.id}`, reason, tone: 'amber' }));
      if (isPsych && pendingAssignments > 0) {
        needItems.unshift({
          kind: 'action', key: 'n-answer', icon: 'inbox', to: '/children',
          label: `Answer ${pendingAssignments} assignment request${pendingAssignments === 1 ? '' : 's'}`,
          meta: 'Waiting for you to accept or decline',
        });
      }
      if (isAdmin && pendingAccess > 0) needItems.unshift({ ...actions.find((a) => a.key === 'a-access'), key: 'n-access' });
      return [
        { label: 'Recent', items: recentItems },
        {
          label: 'Needs you', items: needItems,
          notes: needs.length > MAX_NEEDS ? [`And ${needs.length - MAX_NEEDS} more. Records lists them all.`] : [],
        },
        { label: 'Actions', items: actions.filter((a) => a.key !== 'a-access') },
      ];
    }
    const hits = matchChildren(children, term);
    const childItems = hits.slice(0, MAX_CHILDREN).map(({ child, reason, field }) => childItem(child, { reason, field }));
    const staffItems = isAdmin ? matchStaff(staff, term).slice(0, MAX_STAFF).map((u) => ({
      kind: 'staff', key: `u${u.id}`, name: u.fullname || u.email, initials: initialsOf(u.fullname || u.email),
      meta: [roleLabel(u.role_name), (u.status || '').toLowerCase()].filter(Boolean).join(' · '), to: '/users',
    })) : [];
    return [
      {
        label: 'Children', items: childItems,
        notes: hits.length > MAX_CHILDREN ? [`${hits.length - MAX_CHILDREN} more match. Type more to narrow it.`] : [],
      },
      { label: 'Staff', items: staffItems },
      { label: 'Actions', items: actions.filter((a) => matchesCommand(a, term)) },
      { label: 'Go to', items: screens.filter((s) => matchesCommand(s, term)) },
    ];
  }, [focus, term, children, staff, recent, role, user?.id, isAdmin, isPsych, actions, screens,
    pendingAccess, pendingAssignments, childActions]);

  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  // A new search starts from the top, out of any child's actions. The clamp
  // below is a functional update so it cannot overwrite that with the cursor
  // the shorter list was rendered with: from the foot of a long list, a letter
  // typed left the highlight on the new list's last row, not its first.
  useEffect(() => { setCursor(0); setFocus(null); }, [term]);
  useEffect(() => { setCursor((i) => Math.min(i, Math.max(0, flat.length - 1))); }, [flat.length]);
  // Keep the highlighted row in view as the arrows move it. The first row sits
  // under its group's label, which 'nearest' leaves scrolled off: back at the
  // top of the list, it goes to the top.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    if (cursor === 0) list.scrollTop = 0;
    else list.querySelector(`[data-row="${cursor}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [cursor, focus]);

  const go = (hit) => { if (!hit?.to) return; close(); navigate(hit.to); };
  const showActions = (item, index) => {
    if (item?.kind !== 'child') return;
    setFocus({ child: item.child, from: index });
    setCursor(0);
    inputRef.current?.focus();
  };
  const back = () => { const from = focus?.from ?? 0; setFocus(null); setCursor(from); inputRef.current?.focus(); };

  const onKeyDown = (e) => {
    const input = e.currentTarget;
    // The field can hold focus with the panel shut (tabbed onto, or left so by
    // the browser's back button). Enter must not open a row nobody can see, so
    // only the way back in is read.
    if (!open) {
      if (e.key === 'Escape') { e.preventDefault(); closeAndReturn(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); onOpenChange(true); }
      return;
    }
    // Tab or Shift+Tab leaves the bar and takes the panel with it. Nothing in
    // the panel is a tab stop, so there is no inside to walk into, and a row
    // focused there would not answer Esc or the arrows. Not close(): the
    // browser is about to move focus on from where it is.
    if (e.key === 'Tab') { reset(); return; }
    if (e.key === 'Escape') { e.preventDefault(); if (focus) back(); else closeAndReturn(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setCursor((i) => Math.min(flat.length - 1, i + 1)); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); setCursor((i) => Math.max(0, i - 1)); return; }
    if (e.key === 'Enter') { e.preventDefault(); go(flat[cursor]); return; }
    // The arrows only take over where the caret has nowhere to go, so the
    // text can still be edited.
    const atEnd = input.selectionStart === input.value.length && input.selectionEnd === input.value.length;
    if (e.key === 'ArrowRight' && !focus && atEnd && flat[cursor]?.kind === 'child') {
      e.preventDefault(); showActions(flat[cursor], cursor); return;
    }
    if (e.key === 'ArrowLeft' && focus) { e.preventDefault(); back(); }
  };

  const groupLabel = {
    fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 'var(--text-3xs)',
    letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--text-faint)', padding: '10px 8px 4px',
  };
  const noteStyle = { padding: '4px 8px 6px', fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-muted)' };

  const row = (hit, i) => {
    const on = cursor === i;
    const isChild = hit.kind === 'child';
    return (
      <div
        key={hit.key} data-row={i} role="option" id={`global-search-row-${i}`} aria-selected={on}
        style={{ display: 'flex', alignItems: 'center', borderRadius: 'var(--radius-sm)', background: on ? 'var(--blue-50)' : 'transparent' }}
      >
        <div
          onClick={() => go(hit)}
          style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 10, padding: '7px 8px', cursor: 'pointer', textAlign: 'left', fontFamily: 'var(--font-sans)' }}
        >
          {hit.initials != null ? (
            <span aria-hidden="true" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 11 }}>
              {hit.initials}
            </span>
          ) : (
            <span aria-hidden="true" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, borderRadius: 'var(--radius-control)', flex: 'none', background: on ? 'var(--blue-100)' : 'var(--ink-50)', color: 'var(--blue-700)' }}>
              <Icon name={hit.icon || 'arrow-right'} size={15} />
            </span>
          )}
          <span style={{ flex: 1, minWidth: 0 }}>
            <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{hit.name || hit.label}</span>
            {(hit.reason || hit.meta) && (
              <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {hit.reason && (
                  <span style={{ fontWeight: 600, color: hit.tone === 'amber' ? 'var(--amber-700)' : 'var(--blue-700)' }}>
                    {hit.reason}{hit.meta ? ' · ' : ''}
                  </span>
                )}
                {hit.meta}
              </span>
            )}
          </span>
          {hit.ref && <span className="racco-mono" style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-faint)', flex: 'none' }}>{hit.ref}</span>}
        </div>
        {isChild && (
          <div
            aria-hidden="true" onClick={() => showActions(hit, i)} title="Quick actions (→)"
            style={{ flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, marginRight: 4, borderRadius: 'var(--radius-sm)', background: on ? 'var(--blue-100)' : 'transparent', color: on ? 'var(--blue-700)' : 'var(--text-faint)', cursor: 'pointer' }}
          >
            <Icon name="chevron-right" size={16} />
          </div>
        )}
      </div>
    );
  };

  let index = 0;
  const shownGroups = groups.filter((g) => g.items.length || g.notes?.length);
  const nothing = term && !focus && loaded && flat.length === 0;

  const openFromIcon = () => {
    // Inside the press, so a phone raises its keyboard: a field focused from an
    // effect, after the press has ended, is not allowed to.
    flushSync(() => onOpenChange(true));
    inputRef.current?.focus();
  };

  // The pill is the field. Clicking anywhere on it, icon, padding and the
  // Ctrl K chip included, puts the caret in the input and opens the panel
  // - also when the field already has focus with the panel shut (the
  // browser's back button leaves it so), where focusing does nothing.
  const pill = (
      <div
        className="racco-chrome-search"
        onMouseDown={(e) => {
          if (e.target !== inputRef.current) { e.preventDefault(); inputRef.current?.focus(); }
          if (!open) onOpenChange(true);
        }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          display: 'flex', alignItems: 'center', gap: 9,
          width: phone ? 'auto' : layout.searchWidth, flex: phone ? 1 : undefined, minWidth: 0, height: phone ? 40 : 36, padding: '0 8px 0 12px',
          border: `1px solid ${open ? 'rgba(255,255,255,0.35)' : 'var(--chrome-line)'}`,
          borderRadius: 'var(--radius-pill)',
          background: open || hover ? 'rgba(255,255,255,0.16)' : 'rgba(255,255,255,0.09)',
          cursor: 'text',
          transition: 'background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out)',
        }}
      >
        <Icon name="search" size={17} aria-hidden="true" style={{ color: 'var(--chrome-ink-soft)' }} />
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls="global-search-results"
          aria-activedescendant={open && flat[cursor] ? `global-search-row-${cursor}` : undefined}
          aria-autocomplete="list"
          aria-label={isAdmin ? 'Search children, staff and actions' : 'Search children and actions'}
          autoComplete="off"
          spellCheck={false}
          placeholder="Find a child or task"
          value={q}
          onChange={(e) => { setQ(e.target.value); if (!open) onOpenChange(true); }}
          // No onFocus: tabbing onto the bar is a pass through the header, not
          // a search. A press, typing, the down arrow and Ctrl K open it.
          onKeyDown={onKeyDown}
          style={{ flex: 1, minWidth: 0, height: '100%', padding: 0, border: 'none', background: 'transparent', color: 'var(--chrome-ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: phone ? 16 : 13 }}
        />
        {q && (
          <button
            type="button" tabIndex={-1} aria-label="Clear search"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => { setQ(''); setCursor(0); setFocus(null); inputRef.current?.focus(); }}
            style={{ flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 22, height: 22, padding: 0, border: 'none', borderRadius: '50%', background: 'rgba(255,255,255,0.12)', color: 'var(--chrome-ink-soft)', cursor: 'pointer' }}
          >
            <Icon name="x" size={13} />
          </button>
        )}
        {layout.searchHintOn && !open && !q && (
          <span className="racco-mono" style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--chrome-ink-dim)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: 'var(--radius-xs)', padding: '1px 5px', whiteSpace: 'nowrap', flex: 'none' }}>
            Ctrl K
          </span>
        )}
      </div>
  );

  return (
    <div ref={boxRef} style={{ position: 'relative', flex: 'none' }}>
      {phone && !open && (
        <button
          ref={iconRef} type="button" className="racco-phone-search" aria-label="Find a child" title="Find a child"
          aria-expanded={false} onClick={openFromIcon}
        >
          <Icon name="search" size={20} aria-hidden="true" />
        </button>
      )}
      {phone && open && (
        <div className="racco-phone-searchbar">
          {pill}
          <button
            type="button" tabIndex={-1} className="racco-phone-search-close"
            onMouseDown={(e) => e.preventDefault()} onClick={closeAndReturn}
          >
            Close
          </button>
        </div>
      )}
      {!phone && pill}

      {open && (
        <div
          onMouseDown={(e) => e.preventDefault()}
          // The highlight follows the pointer when it MOVES. The list scrolling
          // or refilling under a pointer that is resting fires the same events,
          // and would pull the highlight off the row the keys had reached.
          onMouseMove={(e) => {
            const at = pointerAt.current;
            if (at && at.x === e.clientX && at.y === e.clientY) return;
            pointerAt.current = { x: e.clientX, y: e.clientY };
            const hit = e.target.closest?.('[data-row]');
            if (hit) setCursor(+hit.dataset.row);
          }}
          style={{
            ...(phone
              ? { position: 'fixed', top: 'var(--topbar-h)', left: 0, right: 0, borderRadius: '0 0 var(--radius-card) var(--radius-card)' }
              : { position: 'absolute', top: 44, left: 0, width: layout.searchPanelWidth, maxWidth: '80vw', borderRadius: 'var(--radius-card)' }),
            background: 'var(--surface)', border: '1px solid var(--border)',
            boxShadow: 'var(--shadow-xl)', overflow: 'hidden', zIndex: 60,
            animation: 'racco-pop-in var(--dur-base) var(--ease-out)',
          }}
        >
          {focus && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px 2px' }}>
              <button
                type="button" onClick={back} aria-label="Back to the results"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 26, border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', background: 'var(--surface)', color: 'var(--text-body)', cursor: 'pointer', flex: 'none' }}
              >
                <Icon name="chevron-left" size={15} />
              </button>
              <span style={{ flex: 1, minWidth: 0, fontWeight: 800, fontSize: 13, color: 'var(--text-strong)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {focus.child.fullname}
              </span>
              <span className="racco-mono" style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-faint)', flex: 'none' }}>{caseRef(focus.child.id)}</span>
            </div>
          )}

          <div ref={listRef} className="racco-scroll" style={{ padding: '2px 8px 10px', maxHeight: phone ? 'min(400px, calc(100dvh - var(--topbar-h) - 120px))' : 400, overflowY: 'auto' }}>
            {!term && !focus && (
              <p style={{ ...noteStyle, padding: '8px 8px 2px' }}>
                Search by name, case no., custodian, barangay or birth year{isAdmin ? ", or a colleague's name" : ''}.
              </p>
            )}
            {!term && !focus && !loaded && children.length === 0 && (
              <p style={{ padding: '18px 8px', fontSize: 12.5, color: 'var(--text-muted)' }}>Loading…</p>
            )}
            {term && !focus && !loaded && children.length === 0 && (
              <p style={{ padding: '18px 8px', fontSize: 12.5, color: 'var(--text-muted)' }}>Loading records…</p>
            )}
            {nothing && (
              <p style={{ padding: '18px 8px', fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)', overflowWrap: 'anywhere' }}>
                Nothing matches &ldquo;{term}&rdquo;. Try part of the name, the case number, the custodian, the
                barangay or the birth year. Only the records your account may open are searched.
              </p>
            )}
            <div id="global-search-results" role="listbox" aria-label="Search results">
              {shownGroups.map((g) => (
                <div key={g.label || 'focus'} role="group" aria-label={g.label || undefined}>
                  {g.label && <div aria-hidden="true" style={groupLabel}>{g.label}</div>}
                  {g.items.map((item) => row(item, index++))}
                  {(g.notes || []).map((n) => <p key={n} style={noteStyle}>{n}</p>)}
                </div>
              ))}
            </div>
          </div>

          <div style={{ padding: '9px 14px', background: 'var(--ink-50)', borderTop: '1px solid var(--divider)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)', display: 'flex', flexWrap: 'wrap', columnGap: 14, rowGap: 2 }}>
            <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>&uarr;&darr;</span> move</span>
            <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>&crarr;</span> open</span>
            {focus
              ? <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>&larr; esc</span> back</span>
              : (
                <>
                  <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>&rarr;</span> actions for a child</span>
                  <span><span className="racco-mono" style={{ color: 'var(--text-body)' }}>esc</span> close</span>
                </>
              )}
          </div>
        </div>
      )}
    </div>
  );
}
