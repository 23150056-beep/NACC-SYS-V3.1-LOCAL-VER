import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../../api/client';
import { saveAssistantSettings } from '../../api/assistant';
import { testSmsDelivery } from '../../api/sms';
import { useActivity } from '../../context/ActivityContext';
import { useAuth } from '../../context/AuthContext';
import { useConfirm, useNotice } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import {
  Alert, Avatar, Button, Drawer, FormField, Icon, Input, Modal, Select, Skeleton, roleLabel,
} from '../../ui';
import { assistantSwitchConfirm } from '../../utils/assistantSwitch';
import { caseRef } from '../../utils/child';
import { firstError } from '../../utils/errors';
import { useSingleFlight } from '../../utils/singleFlight';

/* The footer of the ISA's admin console: everything an administrator does
 * rather than reads, grouped under the queues it acts on.
 *
 *   Accounts  Review access · Add user · Reset a password · Transfer records
 *   System    Send test text · Assistant on/off · Agency details · Settings
 *
 * Five of them go to another page (Review access, Add user, Agency details,
 * Settings, and Reset a password once it has been told whose), and three finish
 * the job here:
 *  - Reset a password picks WHOSE, then hands over to Users (/users?reset=<id>),
 *    which asks the question and shows the one-time password. The temporary
 *    password is never on this screen.
 *  - Transfer records moves a social worker's active records to another, many at
 *    a time (POST /api/children/transfer/, children/transfer.py).
 *  - Send test text and Assistant on/off ask first (useConfirm), as Settings does
 *    for the same two things, call the same endpoints, and end with a dialog.
 *
 * It sits in the Needs-you-now card and is drawn whether or not the overview has
 * loaded: none of it needs the overview but the assistant's state, and a console
 * that failed to load is exactly when "Add user" and "Settings" are still wanted.
 */

const plural = (n, one, many) => (n === 1 ? one : many);
const nameOf = (u) => u.fullname || u.username || u.email || '';
// Every count the drawer prints reads the same way (1,200, not 1200): the closed
// select, the checklist's header, the button and the dialogs said it two ways.
const num = (n) => n.toLocaleString('en-US');

// A case number is read out whole, and a line that runs out breaks it at its
// hyphen ("C-" at the end of one line, "0010" at the start of the next). Each one
// is drawn on a single line. Only what is shown is wrapped: the text, and what
// goes to the server, still say "C-0010" with an ordinary hyphen.
function KeepRefs({ children: text }) {
  return String(text).split(/(\bC-\d+\b)/).map((part, i) => (
    i % 2 ? <span key={i} style={{ whiteSpace: 'nowrap' }}>{part}</span> : part
  ));
}

/* ------------------------- Reset a password: whose? ------------------------- */

// Who a password can be reset for: active accounts, and never the one signed in -
// the new password would end the ISA's own session mid-task. The server refuses
// every other status, so they are not offered.
function ResetPicker({ onClose }) {
  const navigate = useNavigate();
  const { user: me } = useAuth();
  const [people, setPeople] = useState(null); // null while loading
  const [failed, setFailed] = useState(false);
  const [q, setQ] = useState('');
  const searchBox = useRef(null);
  const list = useRef(null);

  const load = useCallback(() => {
    setPeople(null);
    setFailed(false);
    api.get('/users/')
      .then((r) => setPeople(r.data))
      .catch(() => { setPeople([]); setFailed(true); });
  }, []);
  useEffect(() => { load(); }, [load]);

  // The dialog puts the focus on its close button when it opens; this effect is
  // the parent's, so it runs after that and the cursor ends up where typing
  // starts.
  useEffect(() => { searchBox.current?.querySelector('input')?.focus(); }, []);

  const eligible = useMemo(
    () => (people || []).filter((u) => u.status === 'active' && u.id !== me?.id)
      .sort((a, b) => nameOf(a).localeCompare(nameOf(b))),
    [people, me],
  );
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return eligible;
    return eligible.filter((u) => [nameOf(u), u.email, u.role_name, roleLabel(u.role_name)]
      .some((v) => v && String(v).toLowerCase().includes(needle)));
  }, [eligible, q]);

  const pick = (u) => {
    navigate(`/users?reset=${u.id}`);
    onClose();
  };

  // The rows are ordinary buttons in the tab order; the arrow keys are the
  // shortcut between them and the search box.
  const buttons = () => Array.from(list.current?.querySelectorAll('button') || []);
  const toSearch = () => searchBox.current?.querySelector('input')?.focus();
  const onSearchKey = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter') {
      e.preventDefault();
      buttons()[0]?.focus();
    }
  };
  const onRowKey = (e, i) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); buttons()[i + 1]?.focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); if (i === 0) toSearch(); else buttons()[i - 1]?.focus(); }
  };

  return (
    <Modal
      open onClose={onClose} width={520} icon={<Icon name="key-round" size={19} />} tone="warning"
      title="Reset a password"
      subtitle="Pick the account. You are asked to confirm on the next screen."
      footer={<Button variant="ghost" onClick={onClose}>Cancel</Button>}
    >
      <div ref={searchBox}>
        <Input
          size="sm" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onSearchKey}
          aria-label="Search accounts" placeholder="Search name, email or role…" autoComplete="off"
          leading={<Icon name="search" size={16} />}
        />
      </div>
      <div aria-live="polite" style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)', marginTop: -4 }}>
        {people === null ? 'Loading the accounts…'
          : failed ? ''
            : `Showing ${rows.length} of ${eligible.length} active ${plural(eligible.length, 'account', 'accounts')}`}
      </div>
      {people === null ? (
        <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Skeleton width={28} height={28} radius="50%" />
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 5 }}>
                <Skeleton width="45%" height={11} />
                <Skeleton width="65%" height={9} />
              </div>
            </div>
          ))}
        </div>
      ) : failed ? (
        <Alert tone="danger" icon={<Icon name="wifi-off" size={18} />}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start' }}>
            <span>The accounts could not be loaded. Nothing has been changed.</span>
            <Button size="sm" variant="secondary" onClick={load} iconLeft={<Icon name="refresh-cw" size={15} />}>Try again</Button>
          </div>
        </Alert>
      ) : rows.length === 0 ? (
        <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--text-muted)', padding: '6px 0' }}>
          {eligible.length === 0 ? 'There is no other active account to reset a password for.' : 'No account matches that. Try a name, an email or a role.'}
        </div>
      ) : (
        <ul
          ref={list} className="racco-scroll"
          style={{ listStyle: 'none', margin: 0, padding: 0, maxHeight: 'min(340px, 46vh)', overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}
        >
          {rows.map((u, i) => (
            <li key={u.id} style={{ borderTop: i ? '1px solid var(--divider-row)' : 'none' }}>
              <button
                type="button" className="racco-pick-row" onClick={() => pick(u)} onKeyDown={(e) => onRowKey(e, i)}
                style={{ display: 'flex', alignItems: 'center', gap: 11, width: '100%', padding: '9px 12px', border: 'none', background: 'transparent', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-sans)' }}
              >
                <Avatar name={nameOf(u)} size="sm" tone={u.role_name === 'Staff' ? 'amber' : 'brand'} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontWeight: 700, fontSize: 13, color: 'var(--text-strong)', overflowWrap: 'anywhere' }}>{nameOf(u)}</span>
                  <span className="racco-mono" style={{ display: 'block', fontSize: 11.5, color: 'var(--text-muted)', overflowWrap: 'anywhere' }}>{u.email}</span>
                </span>
                <span className="racco-pick-role" style={{ flex: 'none', fontWeight: 700, fontSize: 11.5, color: 'var(--text-muted)', textAlign: 'right' }}>{roleLabel(u.role_name)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}

/* ------------------------------ Transfer records ------------------------------ */

// Case numbers a sentence names before it says "and 12 more".
const NAMED = 12;
// The most one move takes (children/transfer.py MAX_RECORDS): the server refuses
// more, so the checklist never offers it.
const MAX_RECORDS = 1000;
const firstBatch = (rows) => rows.slice(0, MAX_RECORDS);

function TransferDrawer({ onClose, onMoved }) {
  const confirm = useConfirm();
  // `once` stops a second press from starting a second confirmation and a second
  // request; `saving` is only what the screen shows, and is true from the moment
  // the request goes out - not while the confirmation is up, when it would read
  // "Moving…" and lock every field before anybody had said yes.
  const [once] = useSingleFlight();
  const [saving, setSaving] = useState(false);
  // null while loading; { users, children } once both lists are in.
  const [lists, setLists] = useState(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [ticked, setTicked] = useState(() => new Set());
  const [problem, setProblem] = useState('');
  const allBox = useRef(null);
  const alertBox = useRef(null);

  // Both lists come from the same two requests the Users and Records screens
  // make, fetched each time the drawer opens so a record or an account that
  // changed since the console loaded is not offered. The counts beside each name
  // are counted from the very list the checklist shows, so they cannot disagree.
  const fetchLists = useCallback(async () => {
    const [users, children] = await Promise.all([
      api.get('/users/', { params: { include_archived: 'true' } }),
      api.get('/children/'),
    ]);
    return { users: users.data, children: children.data };
  }, []);
  const load = useCallback(() => {
    setLists(null);
    setLoadFailed(false);
    fetchLists().then(setLists).catch(() => setLoadFailed(true));
  }, [fetchLists]);
  useEffect(() => { load(); }, [load]);

  const socialWorkers = useMemo(
    () => (lists?.users || []).filter((u) => u.role_name === 'Staff'),
    [lists],
  );
  // Active records by holder.
  const byHolder = useMemo(() => {
    const map = new Map();
    for (const c of lists?.children || []) {
      if (c.status !== 'active' || !c.social_worker) continue;
      if (!map.has(c.social_worker)) map.set(c.social_worker, []);
      map.get(c.social_worker).push(c);
    }
    for (const rows of map.values()) rows.sort((a, b) => a.id - b.id);
    return map;
  }, [lists]);
  // Who can be moved FROM: anybody holding an active record. An account that can
  // no longer sign in comes first, since that is the usual reason to be here.
  const holders = useMemo(
    () => socialWorkers.filter((u) => byHolder.has(u.id))
      .sort((a, b) => (a.status === 'active') - (b.status === 'active')
        || byHolder.get(b.id).length - byHolder.get(a.id).length
        || nameOf(a).localeCompare(nameOf(b))),
    [socialWorkers, byHolder],
  );
  const receivers = useMemo(
    () => socialWorkers.filter((u) => u.status === 'active' && String(u.id) !== from)
      .sort((a, b) => nameOf(a).localeCompare(nameOf(b))),
    [socialWorkers, from],
  );
  const records = byHolder.get(Number(from)) || [];
  const fromUser = socialWorkers.find((u) => String(u.id) === from);
  const toUser = socialWorkers.find((u) => String(u.id) === to);
  const chosen = records.filter((c) => ticked.has(c.id));
  // "All" is everything one move can take: the whole list, or its first 1,000.
  const all = records.length > 0 && chosen.length === Math.min(records.length, MAX_RECORDS);
  const capped = records.length > MAX_RECORDS;

  useEffect(() => {
    if (allBox.current) allBox.current.indeterminate = chosen.length > 0 && !all;
  }, [chosen.length, all]);

  // The checklist can be long and scrolled, and the refusal is drawn at the top
  // of the same scrolling box, so it would be out of sight: a record that could
  // not be moved looked like nothing having happened. Bring it into view.
  useEffect(() => {
    if (problem) alertBox.current?.scrollIntoView({ block: 'nearest' });
  }, [problem]);

  const pickFrom = (value) => {
    setFrom(value);
    setProblem('');
    if (value === to) setTo('');
    setTicked(new Set(firstBatch(byHolder.get(Number(value)) || []).map((c) => c.id)));
  };
  const toggle = (id) => setTicked((now) => {
    const next = new Set(now);
    if (next.has(id)) next.delete(id);
    else if (next.size < MAX_RECORDS) next.add(id);
    return next;
  });
  const toggleAll = () => setTicked(all ? new Set() : new Set(firstBatch(records).map((c) => c.id)));

  // After a refusal the list is read again: what was ticked stays ticked where
  // the record is still the holder's to move, and the holder is dropped if they
  // have nothing left.
  const reload = async () => {
    try {
      const fresh = await fetchLists();
      setLists(fresh);
      const still = new Set();
      for (const c of fresh.children) {
        if (c.status === 'active' && String(c.social_worker) === from) still.add(c.id);
      }
      setTicked((now) => new Set([...now].filter((id) => still.has(id))));
      if (still.size === 0) { setFrom(''); setTo(''); }
      // The one they were going to may have been deactivated in the meantime.
      else if (!fresh.users.some((u) => String(u.id) === to && u.status === 'active')) setTo('');
    } catch {
      setLoadFailed(true);
    }
  };

  const move = () => once(async () => {
    if (!fromUser || !toUser || chosen.length === 0) return;
    const n = chosen.length;
    const ok = await confirm({
      title: `Move ${num(n)} ${plural(n, 'record', 'records')} from ${nameOf(fromUser)} to ${nameOf(toUser)}?`,
      description: 'Only who holds the records changes. Each keeps its psychologist, its appointments and its case study. The move is written to the audit trail.',
      confirmLabel: `Yes, move ${plural(n, 'it', 'them')}`,
    });
    if (!ok) return;
    setProblem('');
    setSaving(true);
    let done;
    try {
      ({ data: done } = await api.post('/children/transfer/', {
        from_social_worker: fromUser.id,
        to_social_worker: toUser.id,
        children: chosen.map((c) => c.id),
      }));
    } catch (err) {
      if (err.response) {
        setProblem(firstError(err.response.data, 'The records could not be moved. Nothing was changed.'));
      } else {
        setProblem('Could not reach the server, so it is not known whether the records moved. The list has been read again - check it before trying once more.');
      }
      // A stale list (409), an account that cannot take part any more (400: the
      // one they were going to was deactivated since the drawer opened) or a lost
      // answer: either way what is on screen may be wrong now, so it is fetched
      // again rather than trusted. Anything else - a 500, say - taught us nothing
      // about the lists.
      if (!err.response || err.response.status === 409 || err.response.status === 400) await reload();
      return;
    } finally {
      setSaving(false);
    }
    await onMoved(done, nameOf(fromUser));
  });

  const ready = !!fromUser && !!toUser && chosen.length > 0;
  return (
    <Drawer
      open onClose={onClose} dismissible={!saving} width={500}
      title="Transfer records"
      subtitle="From one social worker to another"
      footer={<>
        <Button type="button" variant="ghost" onClick={saving ? undefined : onClose} aria-disabled={saving || undefined}>Cancel</Button>
        <Button
          type="button" variant="primary" style={{ flex: 1, ...(saving ? { cursor: 'default', opacity: 0.7 } : null) }}
          disabled={!ready} aria-disabled={saving || undefined} onClick={saving ? undefined : move}
          iconLeft={<Icon name={saving ? 'refresh-cw' : 'arrow-right-left'} size={16} style={saving ? { animation: 'racco-spin 0.8s linear infinite' } : undefined} />}
        >
          {saving ? 'Moving…' : chosen.length > 0 ? `Move ${num(chosen.length)} ${plural(chosen.length, 'record', 'records')}` : 'Move records'}
        </Button>
      </>}
    >
      {problem && (
        <div ref={alertBox}>
          <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}><span role="alert"><KeepRefs>{problem}</KeepRefs></span></Alert>
        </div>
      )}

      {loadFailed ? (
        <Alert tone="danger" icon={<Icon name="wifi-off" size={18} />}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start' }}>
            <span>The social workers and their records could not be loaded. Nothing has been changed.</span>
            <Button size="sm" variant="secondary" onClick={load} iconLeft={<Icon name="refresh-cw" size={15} />}>Try again</Button>
          </div>
        </Alert>
      ) : lists === null ? (
        <div aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Skeleton width="30%" height={12} />
          <Skeleton width="100%" height={42} radius="var(--radius-md)" />
          <Skeleton width="30%" height={12} />
          <Skeleton width="100%" height={42} radius="var(--radius-md)" />
        </div>
      ) : holders.length === 0 ? (
        <div style={{ fontSize: 13, lineHeight: 1.55, color: 'var(--text-muted)' }}>
          No social worker holds an active record, so there is nothing to move.
        </div>
      ) : (
        <>
          <div style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-muted)' }}>
            Only who holds a record changes. Its psychologist, appointments and case study stay with it.
            Closed records are not moved.
          </div>
          <FormField label="From" hint={fromUser && fromUser.status !== 'active' ? 'This account is deactivated, so nobody can open these records until they are moved.' : null}>
            <Select value={from} onChange={(e) => pickFrom(e.target.value)} disabled={saving}>
              <option value="">Choose a social worker…</option>
              {holders.map((u) => {
                const n = byHolder.get(u.id).length;
                // "active" is left out of the option: at 320px the closed select
                // is about 26 characters wide, and a longer line cut the count
                // itself. The note above says closed records are not moved, and
                // the checklist counts the same records.
                return (
                  <option key={u.id} value={u.id}>
                    {nameOf(u)}{u.status === 'active' ? '' : ' (inactive)'} — {num(n)} {plural(n, 'record', 'records')}
                  </option>
                );
              })}
            </Select>
          </FormField>
          <FormField label="To" hint="Only active social workers can take records.">
            <Select value={to} onChange={(e) => { setTo(e.target.value); setProblem(''); }} disabled={saving || !from}>
              <option value="">{from ? 'Choose a social worker…' : 'Choose who they move from first'}</option>
              {receivers.map((u) => <option key={u.id} value={u.id}>{nameOf(u)}</option>)}
            </Select>
          </FormField>

          {fromUser && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: 'var(--ink-25)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md) var(--radius-md) 0 0' }}>
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 9, fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 12.5, color: 'var(--text-strong)', cursor: 'pointer' }}>
                  <input ref={allBox} type="checkbox" checked={all} onChange={toggleAll} disabled={saving} style={{ accentColor: 'var(--blue-600)', width: 15, height: 15 }} />
                  Select all / none
                </label>
                <span style={{ flex: 1 }} />
                <span aria-live="polite" style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-muted)' }}>
                  {num(chosen.length)} of {num(records.length)} selected
                </span>
              </div>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--border)', borderTop: 'none', borderRadius: '0 0 var(--radius-md) var(--radius-md)' }}>
                {records.map((c, i) => (
                  <li key={c.id} style={{ borderTop: i ? '1px solid var(--divider-row)' : 'none' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', cursor: saving ? 'default' : 'pointer' }}>
                      <input type="checkbox" checked={ticked.has(c.id)} onChange={() => toggle(c.id)} disabled={saving || (!ticked.has(c.id) && chosen.length >= MAX_RECORDS)} style={{ accentColor: 'var(--blue-600)', width: 15, height: 15 }} />
                      <span className="racco-mono" style={{ flex: 'none', fontWeight: 700, fontSize: 12, color: 'var(--text-muted)' }}>{caseRef(c.id)}</span>
                      <span style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 13, color: 'var(--text-strong)', overflowWrap: 'anywhere' }}>{c.fullname}</span>
                    </label>
                  </li>
                ))}
              </ul>
              {capped && (
                <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.5, color: 'var(--text-muted)' }}>
                  {nameOf(fromUser)} holds {num(records.length)} active records and one move takes at most {num(MAX_RECORDS)}, so the first {num(MAX_RECORDS)} are ticked. Move them, then open this again for the rest.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}

/* ----------------------------------- footer ----------------------------------- */

export default function ConsoleActions({ accessCount, narrow, assistant, onChanged }) {
  const navigate = useNavigate();
  const confirm = useConfirm();
  const notice = useNotice();
  const toast = useToast();
  const { refresh: refreshActivity } = useActivity();
  const [panel, setPanel] = useState(null); // 'reset' | 'transfer' | null
  const [onceText, sendingText] = useSingleFlight();
  const [onceSwitch, switching] = useSingleFlight();

  // The assistant's own row in the overview says whether it is on. Until the
  // overview has answered it is not known, and the button says so.
  const known = !!assistant;
  const on = known && assistant.status !== 'off';

  const sendTestText = () => onceText(async () => {
    // One real text through the gateway, so it asks first. The key check beside
    // Text messages spends nothing.
    const yes = await confirm({
      title: 'Send a test text to your own verified number?',
      description: 'This sends one real text message to your own confirmed number, through the SMS provider, and costs one credit. The Test button on the Text messages row only checks the key and spends nothing.',
      confirmLabel: 'Yes, send it',
    });
    if (!yes) return;
    try {
      const answer = await testSmsDelivery();
      await notice({
        // With no gateway set up (the local copy) the server writes the text to
        // its log and still answers ok, so "sent" would say something untrue.
        title: !answer.ok ? 'The test text was not sent'
          : answer.provider === 'console' ? 'Test text written to the log' : 'Test text sent',
        description: answer.detail,
        icon: answer.ok ? undefined : 'alert-triangle',
        tone: answer.ok ? 'brand' : 'warning',
        details: [['Gateway', answer.provider], ['Sender', answer.sender], ['To', answer.recipient]],
      });
    } catch (err) {
      const said = err.response?.data?.detail;
      if (err.response?.status === 400 && said) {
        // No verified number of their own: the answer is where to add one.
        const go = await confirm({
          title: 'Add and verify your mobile number first',
          description: said,
          confirmLabel: 'Open my profile',
          cancelLabel: 'Not now',
          icon: 'smartphone',
        });
        if (go) navigate('/profile');
        return;
      }
      await notice({
        title: 'The test could not be run',
        description: said || (err.response ? `The server answered with an error (${err.response.status}).` : 'Could not reach the server.'),
        icon: 'alert-triangle',
        tone: 'warning',
      });
    }
  });

  const flipAssistant = () => onceSwitch(async () => {
    if (!known) {
      // The button is aria-disabled until the console has answered, but it still
      // takes the press: a reason in a title attribute is never seen by someone
      // on a keyboard or a phone. The toast is read out (ToastContext).
      toast.info('The console has not loaded, so it is not known whether the assistant is on. Press Try again above, then switch it.');
      return;
    }
    const next = !on;
    if (!(await confirm(assistantSwitchConfirm(next)))) return;
    try {
      await saveAssistantSettings({ enabled: next });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'The assistant could not be switched. Nothing was changed.');
      return;
    }
    // The health row and this button read the same answer.
    onChanged();
    await notice({
      title: next ? 'The assistant is on' : 'The assistant is off',
      description: next
        ? 'The assistant is on for everyone in the agency.'
        : 'The assistant is off for everyone in the agency. Briefs, summaries, remark polishing and the chat panel stay off until it is turned on again.',
    });
  });

  // Called by the drawer once the server has moved the records: close it, read
  // the console again (the archived-holder tile and the workload change), and say
  // what happened where it will be read.
  const moved = async (done, fromName) => {
    setPanel(null);
    onChanged();
    // The move is in the audit trail now; the bell and the rail read it from there.
    refreshActivity();
    const refs = done.children.map((c) => c.ref);
    const shown = refs.length > NAMED ? `${refs.slice(0, NAMED).join(', ')} and ${refs.length - NAMED} more` : refs.join(', ');
    // Each case number stays on one line (KeepRefs).
    const records = <KeepRefs>{shown}</KeepRefs>;
    await notice({
      title: 'Records moved',
      description: `${num(done.moved)} ${plural(done.moved, 'record', 'records')} moved from ${fromName} to ${done.to.name}. ${plural(done.moved, 'It is', 'They are')} in ${done.to.name}'s Records now, and keep${plural(done.moved, 's', '')} ${plural(done.moved, 'its', 'their')} psychologist, appointments and case study.`,
      details: [['From', fromName], ['To', done.to.name], ['Records', records]],
    });
  };

  const groups = [
    {
      label: 'Accounts',
      items: [
        // The count only when somebody is waiting: a permanent "(0)" is a number
        // that cries wolf. The button stays, for the page it opens, and is
        // outlined like its neighbours as in the approved picture - the count
        // is what says somebody is waiting.
        { key: 'access', label: accessCount > 0 ? `Review access (${accessCount})` : 'Review access', icon: 'user-check', run: () => navigate('/users?tab=requests') },
        { key: 'add', label: 'Add user', icon: 'user-plus', run: () => navigate('/users?new=1') },
        { key: 'reset', label: 'Reset a password', icon: 'key-round', run: () => setPanel('reset') },
        { key: 'transfer', label: 'Transfer records', icon: 'arrow-right-left', run: () => setPanel('transfer') },
      ],
    },
    {
      label: 'System',
      items: [
        { key: 'text', label: 'Send test text', icon: 'message-square', busy: sendingText, run: sendTestText },
        {
          key: 'assistant', label: known ? `Assistant: ${on ? 'On' : 'Off'}` : 'Assistant on/off',
          icon: on ? 'toggle-right' : 'toggle-left', busy: switching, unsure: !known, run: flipAssistant,
          // The label says the state; the knob side and the colour say it two
          // other ways. Not `aria-pressed` as well: with a label that changes,
          // a screen reader would announce the state twice.
          tint: on ? 'var(--success-700)' : undefined,
          hint: known ? undefined : 'Reload the console to see whether the assistant is on.',
        },
        { key: 'agency', label: 'Agency details', icon: 'building-2', run: () => navigate('/settings?section=agency') },
        { key: 'settings', label: 'Settings', icon: 'settings', run: () => navigate('/settings') },
      ],
    },
  ];

  return (
    <>
      {/* One grid, so the label column is as wide as the longest label. The
          buttons are the outlined small ones the footer always had. A button
          that is working is aria-disabled, not disabled: a disabled button loses
          the keyboard focus, and whoever pressed Enter on it is left at the top
          of the page. */}
      <div style={{ padding: '11px 14px', background: 'var(--ink-25)', borderTop: '1px solid var(--divider)', display: 'grid', gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'max-content minmax(0, 1fr)', columnGap: 14, rowGap: narrow ? 6 : 8, alignItems: 'start' }}>
        {groups.map((g, gi) => (
          <Fragment key={g.label}>
            {/* Level with the FIRST row of buttons (32px tall), not centred on the
                whole group: when the group wraps onto a second or third line the
                label stays with the line it names. */}
            <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)', marginTop: narrow && gi ? 4 : 0, ...(narrow ? null : { display: 'flex', alignItems: 'center', height: 32 }) }}>{g.label}</span>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {g.items.map((a) => (
                <Button
                  key={a.key} size="sm" variant="secondary"
                  iconLeft={<Icon name={a.busy && a.key === 'text' ? 'refresh-cw' : a.icon} size={15} style={a.busy && a.key === 'text' ? { animation: 'racco-spin 0.8s linear infinite' } : a.tint ? { color: a.tint } : undefined} />}
                  onClick={a.busy ? undefined : a.run}
                  aria-disabled={a.busy || a.unsure || undefined} title={a.hint}
                  style={a.busy || a.unsure ? { cursor: 'default', opacity: 0.7 } : undefined}
                >
                  {a.label}
                </Button>
              ))}
            </div>
          </Fragment>
        ))}
      </div>
      {panel === 'reset' && <ResetPicker onClose={() => setPanel(null)} />}
      {panel === 'transfer' && <TransferDrawer onClose={() => setPanel(null)} onMoved={moved} />}
    </>
  );
}
