import { useCallback, useEffect, useState } from 'react';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useActivity } from '../context/ActivityContext';
import { useCensus } from '../context/CensusContext';
import { useConfirm, useNotice } from '../context/ConfirmContext';
import { Alert, Badge, Button, FormField, Icon, IconChip, Input, Modal, Select } from '../ui';
import { DURATIONS, PURPOSE_LABEL, initialsOf } from '../utils/child';
import { firstError } from '../utils/errors';
import { timeAgo } from '../utils/time';

/* "Waiting for your answer" - the children a psychologist has been asked to
 * take (owner's request, 28 Sep 2026).
 *
 * A child picked for a psychologist is not in their records until they
 * accept (backend children/assignment.py), so this panel is the only place
 * the child appears to them before then - on the Dashboard and on Records.
 * It shows what is needed to decide and nothing clinical: the records are not
 * theirs to read until they say yes.
 *
 * Accept -> "Are you sure?" -> the scheduling dialog (now / from my
 * availability / later) -> an end dialog. Decline -> a reason, which the
 * social worker reads -> an end dialog. Every path ends by saying what
 * happened, because "it joins your records" and "they see your reason" are
 * the sentences somebody needs to have read.
 */

const localIsoDay = (d = new Date()) => (
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
);

// "Tue 30 Sep" from 2026-09-30, read as a local date (not UTC midnight).
function dayLabel(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

const caseLine = (r) => [
  r.child_age != null ? `${r.child_age} yrs` : null, r.child_gender, r.case_type, r.case_category,
].filter(Boolean).join(' · ');

const textarea = {
  width: '100%', resize: 'vertical', padding: '11px 13px', borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.55,
};

export default function AssignmentRequests({ onChanged }) {
  const { user } = useAuth();
  const confirm = useConfirm();
  const notice = useNotice();
  const { refresh: refreshCensus, refreshPendingAssignments } = useCensus();
  const { refresh: refreshActivity } = useActivity();
  const [requests, setRequests] = useState([]);
  const [failed, setFailed] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [declining, setDeclining] = useState(null);
  const [scheduling, setScheduling] = useState(null);

  const load = useCallback(() => api.get('/assignment-requests/', { params: { status: 'pending' } })
    .then((r) => { setRequests(r.data || []); setFailed(false); })
    .catch(() => setFailed(true)), []);
  useEffect(() => { load(); }, [load]);

  const settled = () => {
    load();
    refreshPendingAssignments();
    refreshActivity();
    onChanged?.();
  };

  const accept = async (req) => {
    if (!(await confirm({
      description: `This adds ${req.child_name} to your records. You can book the first session straight after.`,
      confirmLabel: 'Yes, accept',
      details: [['Child', `${req.child_name} (${req.child_ref})`], ['Case', caseLine(req)],
        ['Asked by', req.requested_by_name],
        ['Moving from', req.previous_psychologist_name]],
    }))) return;
    setBusyId(req.id);
    try {
      const { data } = await api.post(`/assignment-requests/${req.id}/accept/`);
      settled();
      setScheduling(data);
    } catch (err) {
      settled();
      // A 409 says what happened - withdrawn, or answered in another tab -
      // and that is worth more than a toast that is gone in three seconds.
      await notice({
        title: 'This case could not be accepted', tone: 'warning', icon: 'alert-triangle',
        description: firstError(err.response?.data, 'Could not accept the case. Try again.'),
      });
    } finally {
      setBusyId(null);
    }
  };

  const decline = async (req, reason) => {
    try {
      await api.post(`/assignment-requests/${req.id}/decline/`, { reason });
    } catch (err) {
      return firstError(err.response?.data, 'Could not decline the case. Try again.');
    }
    setDeclining(null);
    settled();
    await notice({
      title: 'Case declined',
      description: `${req.child_name} stays off your records. ${req.requested_by_name || 'The social worker'} `
        + 'sees your reason and can ask another psychologist.',
      details: [['Child', `${req.child_name} (${req.child_ref})`], ['Your reason', reason]],
    });
    return null;
  };

  const doneScheduling = async (booked) => {
    const req = scheduling;
    setScheduling(null);
    if (booked) {
      refreshCensus();
      refreshActivity();
      await notice({
        title: 'All set',
        description: `${req.child_name} is now in your records, and the first session is booked.`,
        details: [['Child', `${req.child_name} (${req.child_ref})`],
          ['First session', `${dayLabel(booked.date)}, ${booked.time}`],
          ['Length', DURATIONS.find((d) => d.v === booked.duration)?.label],
          ['Purpose', PURPOSE_LABEL[booked.purpose]]],
      });
      return;
    }
    await notice({
      title: `${req.child_name} is now in your records`,
      description: req.has_case_referral
        ? 'Book the first session from the Calendar when you are ready.'
        : `No session can be booked until ${req.requested_by_name || 'the social worker'} files the case `
          + 'referral. Book the first session from the Calendar once it is on file.',
      details: [['Child', `${req.child_name} (${req.child_ref})`], ['First session', 'Not booked yet']],
    });
  };

  if (!failed && requests.length === 0 && !scheduling) return null;

  return (
    <>
      {(failed || requests.length > 0) && (
        <section
          aria-labelledby="assignment-requests-title"
          style={{ background: 'var(--surface)', border: '1px solid var(--amber-200)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}
        >
          <div style={{ padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--amber-50)', borderBottom: '1px solid var(--divider)' }}>
            <IconChip icon="user-plus" tone="warning" />
            <span style={{ flex: 1, minWidth: 0 }}>
              <h3 id="assignment-requests-title" style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 14.5, color: 'var(--text-strong)', margin: 0 }}>
                Waiting for your answer{requests.length > 0 ? ` (${requests.length})` : ''}
              </h3>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
                A child joins your records only once you accept. Declining asks you why, for the social worker.
              </span>
            </span>
          </div>
          {failed && (
            <p style={{ padding: '12px 14px', fontSize: 12.5, color: 'var(--red-700)', margin: 0 }}>
              Your assignment requests could not be loaded. Check your connection and refresh.
            </p>
          )}
          {requests.map((r, i) => (
            <div key={r.id} style={{ display: 'flex', gap: 12, padding: '12px 14px', borderBottom: i < requests.length - 1 ? '1px solid var(--divider-row)' : 'none', alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, borderRadius: '50%', flex: 'none', background: 'var(--amber-100)', color: 'var(--amber-900)', fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 12 }}>
                {initialsOf(r.child_name)}
              </span>
              <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>{r.child_name}</span>
                  <span className="racco-mono" style={{ fontWeight: 600, fontSize: 11.5, color: 'var(--text-faint)' }}>{r.child_ref}</span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-body)', marginTop: 2 }}>{caseLine(r) || '—'}</div>
                {r.referral_reason && (
                  <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                    <span style={{ fontWeight: 700, color: 'var(--text-body)' }}>Reason for referral: </span>{r.referral_reason}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 7 }}>
                  <Badge tone={r.has_case_referral ? 'success' : 'amber'} size="sm" dot>
                    {r.has_case_referral ? 'Case referral on file' : 'No case referral yet'}
                  </Badge>
                  {r.previous_psychologist_name && (
                    <Badge tone="neutral" size="sm">Moving from {r.previous_psychologist_name}</Badge>
                  )}
                  <span style={{ fontSize: 11.5, color: 'var(--text-faint)', alignSelf: 'center' }}>
                    Asked by {r.requested_by_name || 'someone'} · {timeAgo(r.created_at)}
                  </span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, flex: 'none', alignSelf: 'center' }}>
                <Button variant="secondary" size="sm" disabled={busyId === r.id} onClick={() => setDeclining(r)}
                  aria-label={`Decline ${r.child_name}`}>
                  Decline
                </Button>
                <Button variant="primary" size="sm" disabled={busyId === r.id} onClick={() => accept(r)}
                  iconLeft={<Icon name="check" size={15} />} aria-label={`Accept ${r.child_name}`}>
                  Accept
                </Button>
              </div>
            </div>
          ))}
        </section>
      )}
      {declining && (
        <DeclineDialog request={declining} onDecline={decline} onClose={() => setDeclining(null)} />
      )}
      {scheduling && (
        <ScheduleFirstSession request={scheduling} psychologistId={user?.id} onDone={doneScheduling} />
      )}
    </>
  );
}

function DeclineDialog({ request, onDecline, onClose }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setError('');
    const refused = await onDecline(request, reason.trim());
    if (refused) setError(refused);
    setBusy(false);
  };
  return (
    <Modal
      open onClose={onClose} tone="danger" icon={<Icon name="user-x" size={19} />}
      title={`Decline ${request.child_name}'s case?`}
      subtitle={`${request.child_ref} · asked by ${request.requested_by_name || 'someone'}`}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Go back</Button>
        <Button variant="danger" onClick={submit} disabled={busy || !reason.trim()}>Decline the case</Button>
      </>}
    >
      <p style={{ margin: '0 0 12px', fontSize: 14, lineHeight: 1.6, color: 'var(--text-body)' }}>
        The child stays off your records.{' '}
        {request.previous_psychologist_name
          ? `They stay with ${request.previous_psychologist_name}.`
          : 'They stay unassigned until the social worker asks someone else.'}
      </p>
      {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />} style={{ marginBottom: 12 }}>{error}</Alert>}
      <FormField label="Why are you declining?" htmlFor="decline-reason" required
        hint="The social worker reads this, to decide whom to ask next.">
        <textarea
          id="decline-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={4} maxLength={1000}
          placeholder="e.g. My caseload is full until November."
          autoFocus style={textarea}
        />
      </FormField>
    </Modal>
  );
}

/* Straight after accepting: when will you see this child?
 *
 * "Schedule now" is a date and time the psychologist picks - on their own
 * calendar, so their posted hours do not limit it (the booking rule's
 * own_calendar), while a clash or a day of leave is still refused. "From my
 * availability" offers the next open times from their posted hours, worked
 * out on the server by the booking endpoint's own rule, so every one offered
 * can be booked. Both book through POST /appointments/, unchanged. */
function ScheduleFirstSession({ request, psychologistId, onDone }) {
  const confirm = useConfirm();
  const canBook = !!request.has_case_referral;
  const [mode, setMode] = useState(canBook ? 'availability' : 'later');
  const [date, setDate] = useState(localIsoDay());
  const [time, setTime] = useState('');
  const [duration, setDuration] = useState(60);
  const [purpose, setPurpose] = useState(request.case_status === 'counseling' ? 'session' : 'pre_assessment');
  const [openings, setOpenings] = useState(null);
  const [openingsNote, setOpeningsNote] = useState('');
  const [picked, setPicked] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const loadOpenings = useCallback(() => {
    setOpenings(null);
    setPicked(null);
    api.get('/availability/openings/', { params: { child: request.child, duration } })
      .then((r) => { setOpenings(r.data.openings || []); setOpeningsNote(r.data.reason || ''); })
      .catch((err) => { setOpenings([]); setOpeningsNote(firstError(err.response?.data, 'Your open times could not be loaded.')); });
  }, [request.child, duration]);

  useEffect(() => {
    if (mode === 'availability' && canBook) loadOpenings();
  }, [mode, canBook, loadOpenings]);

  const when = mode === 'now' ? (date && time ? { date, time } : null)
    : mode === 'availability' && picked ? { date: picked.date, time: picked.start } : null;

  const book = async () => {
    if (!when) return;
    setError('');
    if (!(await confirm({
      description: `This books ${request.child_name}'s first session on your calendar.`,
      confirmLabel: 'Yes, book it',
      details: [['Child', `${request.child_name} (${request.child_ref})`], ['Date', dayLabel(when.date)],
        ['Time', when.time], ['Length', DURATIONS.find((d) => d.v === duration)?.label],
        ['Purpose', PURPOSE_LABEL[purpose]]],
    }))) return;
    setBusy(true);
    try {
      await api.post('/appointments/', {
        child: request.child, psychologist: psychologistId, start: `${when.date}T${when.time}:00`,
        duration_minutes: duration, purpose, notes: '',
      });
      onDone({ ...when, duration, purpose });
    } catch (err) {
      setError(firstError(err.response?.data, 'Could not book the session. Try another time.'));
      // What was offered a moment ago may have just been taken.
      if (mode === 'availability') loadOpenings();
    } finally {
      setBusy(false);
    }
  };

  const option = (value, icon, title, body, disabled = false) => {
    const on = mode === value;
    return (
      <button
        type="button" key={value} role="radio" aria-checked={on} disabled={disabled}
        onClick={() => { setMode(value); setError(''); }}
        style={{
          flex: '1 1 150px', textAlign: 'left', padding: '11px 12px', borderRadius: 'var(--radius-md)',
          border: `1px solid ${on ? 'var(--blue-500)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : 'var(--surface)',
          cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.55 : 1, fontFamily: 'var(--font-sans)',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontWeight: 800, fontSize: 13.5, color: on ? 'var(--blue-700)' : 'var(--text-strong)' }}>
          <Icon name={icon} size={16} />{title}
        </span>
        <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginTop: 3, lineHeight: 1.45 }}>{body}</span>
      </button>
    );
  };

  return (
    <Modal
      open onClose={() => onDone(null)} tone="success" icon={<Icon name="calendar-plus" size={19} />} width={620}
      title={`${request.child_name} is now in your records`}
      subtitle={`${request.child_ref} · When will you see them first?`}
      footer={mode === 'later' ? (
        <Button variant="primary" onClick={() => onDone(null)}>Finish</Button>
      ) : (<>
        <Button variant="ghost" onClick={() => onDone(null)}>Book later</Button>
        <Button variant="primary" onClick={book} disabled={!when || busy} iconLeft={<Icon name="calendar-check" size={16} />}>
          {busy ? 'Booking…' : 'Book this time'}
        </Button>
      </>)}
    >
      {!canBook && (
        <Alert tone="warning" icon={<Icon name="file-warning" size={18} />} style={{ marginBottom: 12 }}>
          No case referral is on file yet, and no session can be booked without one. Book the first session
          from the Calendar once {request.requested_by_name || 'the social worker'} files it.
        </Alert>
      )}
      <div role="radiogroup" aria-label="When to book the first session" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {option('now', 'clock', 'Schedule now', 'Pick any date and time yourself.', !canBook)}
        {option('availability', 'calendar-range', 'From my availability', 'The next open times from your posted hours.', !canBook)}
        {option('later', 'calendar-x', 'Later', 'Book it from the Calendar when you are ready.')}
      </div>

      {mode !== 'later' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 14 }}>
          <FormField label="Purpose">
            <Select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
              {Object.entries(PURPOSE_LABEL).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </Select>
          </FormField>
          <FormField label="Length">
            <Select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
              {DURATIONS.map((d) => <option key={d.v} value={d.v}>{d.label}</option>)}
            </Select>
          </FormField>
        </div>
      )}

      {mode === 'now' && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
            <FormField label="Date">
              <Input type="date" value={date} min={localIsoDay()} onChange={(e) => setDate(e.target.value)} />
            </FormField>
            <FormField label="Time">
              <Input type="time" value={time} step={900} onChange={(e) => setTime(e.target.value)} />
            </FormField>
          </div>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '8px 0 0', lineHeight: 1.5 }}>
            This goes on your own calendar, so your posted hours do not limit it. A clash with another
            session, or a day you are on leave, is still refused.
          </p>
        </>
      )}

      {mode === 'availability' && canBook && (
        <div style={{ marginTop: 12 }}>
          <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Next open times · two weeks</div>
          {openings === null ? (
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: 0 }}>Finding your open times…</p>
          ) : openings.length === 0 ? (
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', padding: '9px 12px', border: '1px dashed var(--border)', borderRadius: 'var(--radius-control)' }}>
              {openingsNote || 'No open time in the next two weeks.'}{' '}
              Choose <strong>Schedule now</strong> to pick a time yourself, or add hours on the Calendar.
            </div>
          ) : (
            <div role="radiogroup" aria-label="Open times" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 7 }}>
              {openings.map((o) => {
                const on = picked && picked.date === o.date && picked.start === o.start;
                return (
                  <button
                    key={`${o.date}-${o.start}`} type="button" role="radio" aria-checked={!!on}
                    onClick={() => { setPicked(o); setError(''); }}
                    style={{ padding: '8px 10px', borderRadius: 'var(--radius-control)', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-sans)', border: `1px solid ${on ? 'var(--blue-500)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : 'var(--surface)' }}
                  >
                    <span style={{ display: 'block', fontWeight: 800, fontSize: 13, color: on ? 'var(--blue-700)' : 'var(--text-strong)' }}>{dayLabel(o.date)}</span>
                    <span className="racco-mono" style={{ fontSize: 12, color: 'var(--text-body)' }}>{o.start}–{o.end}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {error && (
        <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />} style={{ marginTop: 12 }}>{error}</Alert>
      )}
    </Modal>
  );
}
