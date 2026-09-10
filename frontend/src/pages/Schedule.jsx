import { useCallback, useEffect, useMemo, useState } from 'react';
import { Calendar, dateFnsLocalizer } from 'react-big-calendar';
import { format, parse, startOfWeek, getDay } from 'date-fns';
import { enUS } from 'date-fns/locale';
import 'react-big-calendar/lib/css/react-big-calendar.css';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import {
  Alert, Avatar, Badge, Button, Card, FormField, hoverLift, Icon, iconBtn, Input, PAGE, PageHeader, Select,
} from '../ui';
import { prefetchBriefs } from '../api/assistant';
import { useOpenFromLink } from '../utils/links';

const localizer = dateFnsLocalizer({ format, parse, startOfWeek, getDay, locales: { 'en-US': enUS } });
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const PURPOSES = [
  { v: 'pre_assessment', label: 'Pre-Assessment' },
  { v: 'session', label: 'Session' },
  { v: 'follow_up', label: 'Follow-up' },
];
const STATUS_TONE = { scheduled: 'brand', completed: 'success', no_show: 'amber', cancelled: 'neutral' };
const DURATIONS = [
  { v: 30, label: '30 min' }, { v: 45, label: '45 min' }, { v: 60, label: '1 hour' },
  { v: 90, label: '1 hr 30' }, { v: 120, label: '2 hours' },
];
const SLOT_EMPTY = {
  fontSize: 12.5, color: 'var(--text-muted)', padding: '9px 12px',
  border: '1px dashed var(--border)', borderRadius: 'var(--radius-control)',
};

const todayIso = () => {
  // Local date. toISOString() converts to UTC and hands back yesterday for
  // anybody east of Greenwich, which is all of the Philippines.
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/* DRF answers {field: ["message"]}, and rendering that raw put a JSON array on
   screen. Take the first readable sentence, whatever shape it arrives in. */
function firstError(data, fallback = 'Booking failed.') {
  if (!data) return fallback;
  if (typeof data === 'string') return data;
  for (const key of ['start', 'child', 'psychologist', 'detail', 'non_field_errors']) {
    const v = data[key];
    if (Array.isArray(v) && v.length) return String(v[0]);
    if (typeof v === 'string' && v) return v;
  }
  const first = Object.values(data)[0];
  if (Array.isArray(first) && first.length) return String(first[0]);
  return typeof first === 'string' ? first : fallback;
}
const STATUS_COLOR = { scheduled: 'var(--blue-600)', completed: 'var(--success-600)', no_show: 'var(--amber-500)', cancelled: 'var(--text-faint)' };

export default function Schedule() {
  const { user } = useAuth();
  const toast = useToast();
  const role = user?.role_name || 'Staff';
  const isPsych = role === 'Psychologist';
  const canBook = ['Administrator', 'Staff', 'Psychologist'].includes(role);
  const [appointments, setAppointments] = useState([]);
  const [blocks, setBlocks] = useState([]);
  const [children, setChildren] = useState([]);
  const [psychologists, setPsychologists] = useState([]);
  const [booking, setBooking] = useState(null);
  const [blockForm, setBlockForm] = useState(null);
  const [sel, setSel] = useState(null);
  const [error, setError] = useState('');
  const [daySlots, setDaySlots] = useState(null);   // { slots, reason, psychologist } for the chosen day
  const [slotsBusy, setSlotsBusy] = useState(false);
  const [openPsy, setOpenPsy] = useState(null); // { id, name } — full-page availability view (admin/staff)
  // The calendar is controlled so the page knows which month is on screen and
  // can fetch that range rather than the entire history.
  const [calDate, setCalDate] = useState(() => new Date());
  const [calView, setCalView] = useState('month');

  // Which appointments to hold in memory. The page used to ask for EVERY
  // appointment ever booked on every load - the endpoint is unpaginated, and
  // this database is already at 200 and only grows. A three-month window
  // around the month on screen covers the calendar plus a month either side,
  // so ordinary month-stepping never shows a gap.
  const range = useMemo(() => {
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const from = new Date(calDate.getFullYear(), calDate.getMonth() - 1, 1);
    const to = new Date(calDate.getFullYear(), calDate.getMonth() + 2, 0);
    return { from: iso(from), to: iso(to) };
  }, [calDate]);

  const load = useCallback(() => {
    api.get(`/appointments/?from=${range.from}&to=${range.to}`).then((r) => setAppointments(r.data)).catch(() => {});
    api.get('/availability/').then((r) => setBlocks(r.data)).catch(() => {});
    api.get('/children/').then((r) => setChildren(r.data.filter((c) => c.status === 'active'))).catch(() => {});
    if (!isPsych) api.get('/psychologists/').then((r) => setPsychologists(r.data)).catch(() => {});
  }, [isPsych, range.from, range.to]);
  useEffect(() => { load(); }, [load]);
  // The openings for the psychologist actually chosen, on the day actually
  // chosen, at the duration actually chosen. The old version asked about the
  // child's ASSIGNED psychologist regardless of who was picked in the form,
  // so booking anyone else showed somebody else's free time.
  const bookingPsy = booking?.psychologist || (isPsych ? user?.id : '');
  const bookingDate = booking?.date;
  const bookingDuration = booking?.duration;
  const bookingChild = booking?.child;
  const movingId = booking?.id;
  useEffect(() => {
    if (!bookingPsy || !bookingDate) { setDaySlots(null); return; }
    let live = true;
    setSlotsBusy(true);
    const q = new URLSearchParams({
      psychologist: bookingPsy, date: bookingDate, duration: bookingDuration || 60,
    });
    if (bookingChild) q.set('child', bookingChild);
    // When moving one, it must not be counted as a clash with itself, or the
    // time it currently holds vanishes from the grid offering to move it.
    if (movingId) q.set('exclude', movingId);
    api.get(`/availability/slots/?${q}`)
      .then((r) => { if (live) setDaySlots(r.data); })
      .catch(() => { if (live) setDaySlots(null); })
      .finally(() => { if (live) setSlotsBusy(false); });
    return () => { live = false; };
  }, [bookingPsy, bookingDate, bookingDuration, bookingChild, movingId]);
  // Quietly warms today's brief cache in the background. prefetchBriefs()
  // already swallows its own errors (including a 503 when the assistant is
  // off) — this screen must never know or care whether it succeeded.
  useEffect(() => {
    prefetchBriefs();
    /* eslint-disable-next-line */
  }, []);

  const openBooking = () => {
    setError('');
    // No default time. A prefilled 09:00 was the reason everybody booked
    // 09:00 and the second one was refused.
    setBooking({ child: '', psychologist: '', date: todayIso(), time: '', purpose: 'session', duration: 60, notes: '' });
  };
  useOpenFromLink('book', '1', openBooking, !!booking);

  const events = useMemo(() => appointments.map((a) => {
    const start = new Date(a.start);
    return {
      id: a.id,
      title: `${a.child_name} · ${PURPOSES.find((p) => p.v === a.purpose)?.label || a.purpose}`,
      start,
      end: new Date(start.getTime() + (a.duration_minutes || 60) * 60000),
      resource: a,
    };
  }), [appointments]);

  const eventStyleGetter = useCallback((event) => ({
    style: {
      backgroundColor: STATUS_COLOR[event.resource.status] || 'var(--blue-600)',
      borderRadius: 6, border: 'none', color: '#fff', fontSize: 12, fontWeight: 600,
      opacity: event.resource.status === 'cancelled' ? 0.55 : 1,
      textDecoration: event.resource.status === 'cancelled' ? 'line-through' : 'none',
    },
  }), []);

  const myBlocks = isPsych ? blocks.filter((b) => String(b.psychologist) === String(user?.id)) : blocks;

  // Admin/staff see one container per psychologist instead of a flat list of
  // every block — click a container to open that psychologist's full page.
  const psyGroups = useMemo(() => {
    if (isPsych) return [];
    const map = new Map();
    for (const b of blocks) {
      const key = String(b.psychologist);
      if (!map.has(key)) map.set(key, { id: b.psychologist, name: b.psychologist_name || 'Unassigned', blocks: [] });
      map.get(key).blocks.push(b);
    }
    return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [blocks, isPsych]);

  const daysLabel = (bs) => {
    const wd = [...new Set(bs.filter((b) => b.date == null).map((b) => b.weekday))].sort((a, b) => a - b);
    const parts = wd.map((i) => WEEKDAYS[i].slice(0, 3));
    if (bs.some((b) => b.date != null)) parts.push('Dates');
    return parts.join(' · ');
  };

  const openPsyBlocks = openPsy ? blocks.filter((b) => String(b.psychologist) === String(openPsy.id)) : [];
  const openPsyWeeklySlots = openPsyBlocks.filter((b) => b.date == null).reduce((s, b) => s + (b.capacity || 0), 0);
  const openPsyDated = openPsyBlocks.filter((b) => b.date != null)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));

  const book = async (e) => {
    e.preventDefault();
    setError('');
    const payload = {
      child: booking.child, psychologist: booking.psychologist || undefined,
      start: `${booking.date}T${booking.time}:00`,
      duration_minutes: booking.duration || 60,
      purpose: booking.purpose, notes: booking.notes || '',
    };
    try {
      // Moving one is the same form and the same rules, so it is the same
      // handler — only the verb differs. Cancel-and-rebook was the previous
      // answer, and it threw away who booked it and when.
      if (booking.id) {
        await api.patch(`/appointments/${booking.id}/`, payload);
        toast.success('Appointment moved');
      } else {
        await api.post('/appointments/', payload);
        toast.success('Appointment booked');
      }
      setBooking(null); setSel(null); load();
    } catch (err) {
      setError(firstError(err.response?.data));
    }
  };

  /* Reopen the booking drawer over an existing appointment. The day is kept
     so the grid opens on it; the time is cleared because picking a new one is
     the entire point. */
  const openReschedule = (appt) => {
    setError('');
    const at = new Date(appt.start);
    const pad = (n) => String(n).padStart(2, '0');
    setBooking({
      id: appt.id,
      child: String(appt.child),
      psychologist: String(appt.psychologist),
      date: `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`,
      time: '',
      purpose: appt.purpose,
      duration: appt.duration_minutes || 60,
      notes: appt.notes || '',
    });
  };

  const openCreateBlock = () => {
    setError('');
    setBlockForm({ mode: 'weekly', weekdays: [], date: '', start_time: '09:00', end_time: '12:00', capacity: 2, psychologist: '' });
  };

  useOpenFromLink('availability', '1', openCreateBlock, !!blockForm);

  const openEditBlock = (b) => {
    setError('');
    setBlockForm({
      id: b.id,
      mode: b.date ? 'date' : 'weekly',
      weekday: b.weekday ?? 0,
      date: b.date || '',
      start_time: String(b.start_time).slice(0, 5),
      end_time: String(b.end_time).slice(0, 5),
      capacity: b.capacity,
      psychologist: b.psychologist,
      psychologist_name: b.psychologist_name,
    });
  };

  const saveBlock = async (e) => {
    e.preventDefault();
    setError('');
    if (!isPsych && !blockForm.id && !blockForm.psychologist) {
      setError('Select which psychologist this availability belongs to.');
      return;
    }
    if (!blockForm.id && blockForm.mode === 'weekly' && blockForm.weekdays.length === 0) {
      setError('Tick at least one weekday.');
      return;
    }
    const base = {
      start_time: blockForm.start_time, end_time: blockForm.end_time,
      capacity: Number(blockForm.capacity) || 1,
    };
    // Owner is only set on create — editing never reassigns whose calendar a block belongs to.
    if (!isPsych && !blockForm.id) base.psychologist = blockForm.psychologist;
    try {
      if (blockForm.id) {
        const payload = {
          ...base,
          weekday: blockForm.mode === 'weekly' ? Number(blockForm.weekday) : null,
          date: blockForm.mode === 'date' ? blockForm.date : null,
        };
        await api.patch(`/availability/${blockForm.id}/`, payload);
      } else if (blockForm.mode === 'weekly') {
        for (const wd of blockForm.weekdays) {
          await api.post('/availability/', { ...base, weekday: wd, date: null });
        }
      } else {
        await api.post('/availability/', { ...base, weekday: null, date: blockForm.date });
      }
      toast.success(blockForm.id ? 'Availability updated' : 'Availability added');
      setBlockForm(null); load();
    } catch (err) {
      setError(JSON.stringify(err.response?.data || 'Could not save availability.'));
    }
  };

  const removeBlock = async (b) => {
    if (!window.confirm('Remove this availability block?')) return;
    try { await api.delete(`/availability/${b.id}/`); load(); }
    catch { toast.error('Could not remove the block.'); }
  };

  const setStatus = async (a, actionName) => {
    try {
      await api.post(`/appointments/${a.id}/${actionName}/`);
      toast.success(`Appointment ${actionName === 'no_show' ? 'marked no-show' : actionName + 'd'}`);
      setSel(null); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Could not update.'); }
  };


  return (
    <div style={{ ...PAGE, position: 'relative' }}>
      {openPsy ? (
        <>
          {/* Full-page availability view for one psychologist */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
            <Button variant="ghost" onClick={() => setOpenPsy(null)} iconLeft={<Icon name="arrow-left" size={17} />}>Back to Calendar</Button>
            {role === 'Administrator' && (
              <Button variant="secondary" iconLeft={<Icon name="clock" size={16} />}
                onClick={() => { setError(''); setBlockForm({ mode: 'weekly', weekdays: [], date: '', start_time: '09:00', end_time: '12:00', capacity: 2, psychologist: openPsy.id }); }}>
                Add Availability
              </Button>
            )}
          </div>
          <Card padding="22px" style={{ marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <Avatar name={openPsy.name} tone="brand" size="lg" />
              <div>
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 20, color: 'var(--text-strong)' }}>{openPsy.name}</div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
                  {openPsyBlocks.length} availability block{openPsyBlocks.length === 1 ? '' : 's'}
                  {' · '}{openPsyWeeklySlots} bookable slot{openPsyWeeklySlots === 1 ? '' : 's'} per week
                  {openPsyDated.length > 0 ? ` · ${openPsyDated.length} single-date block${openPsyDated.length === 1 ? '' : 's'}` : ''}
                </div>
              </div>
            </div>
          </Card>
          <Card eyebrow="Psychologist availability" title={`Weekly schedule — ${openPsy.name}`} padding="20px">
            {openPsyBlocks.length === 0 ? (
              <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>No availability blocks for this psychologist yet.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {WEEKDAYS.map((d, i) => {
                  const dayBlocks = openPsyBlocks.filter((b) => b.date == null && b.weekday === i)
                    .sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)));
                  if (dayBlocks.length === 0) return null;
                  return (
                    <div key={d}>
                      <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>{d}s</div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {dayBlocks.map((b) => (
                          <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 'var(--radius-lg)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
                            <Icon name="clock" size={16} style={{ color: 'var(--blue-600)' }} />
                            <span style={{ flex: 1, fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>
                              {String(b.start_time).slice(0, 5)}–{String(b.end_time).slice(0, 5)}
                            </span>
                            <Badge tone="neutral" size="sm">{b.capacity} slot{b.capacity === 1 ? '' : 's'}</Badge>
                            {role === 'Administrator' && (
                              <>
                                <button title="Edit" onClick={() => openEditBlock(b)} style={iconBtn('var(--blue-600)')}><Icon name="pencil" size={14} /></button>
                                <button title="Remove" onClick={() => removeBlock(b)} style={iconBtn('var(--red-700)')}><Icon name="trash-2" size={14} /></button>
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
                {openPsyDated.length > 0 && (
                  <div>
                    <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Specific dates</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {openPsyDated.map((b) => (
                        <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 'var(--radius-lg)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
                          <Icon name="calendar" size={16} style={{ color: 'var(--blue-600)' }} />
                          <span style={{ flex: 1, fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>
                            {b.date} · {String(b.start_time).slice(0, 5)}–{String(b.end_time).slice(0, 5)}
                          </span>
                          <Badge tone="neutral" size="sm">{b.capacity} slot{b.capacity === 1 ? '' : 's'}</Badge>
                          {role === 'Administrator' && (
                            <>
                              <button title="Edit" onClick={() => openEditBlock(b)} style={iconBtn('var(--blue-600)')}><Icon name="pencil" size={14} /></button>
                              <button title="Remove" onClick={() => removeBlock(b)} style={iconBtn('var(--red-700)')}><Icon name="trash-2" size={14} /></button>
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>
        </>
      ) : (
        <>
      <PageHeader title="Calendar &amp; booking" subtitle="Appointments and psychologist availability">
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          {Object.entries(STATUS_COLOR).map(([k, color]) => (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 11.5, color: 'var(--text-body)' }}>
              <span style={{ width: 9, height: 9, borderRadius: 3, background: color }} />{k.replace('_', '-')}
            </span>
          ))}
        </div>
        {(isPsych || role === 'Administrator') && <Button variant="secondary" onClick={openCreateBlock} iconLeft={<Icon name="clock" size={17} />}>Add availability</Button>}
        {canBook && <Button variant="primary" onClick={() => { setError(''); setBooking({ child: '', psychologist: isPsych ? '' : '', date: '', time: '09:00', purpose: 'session', duration: 60, notes: '' }); }} iconLeft={<Icon name="calendar-plus" size={18} />}>Book appointment</Button>}
      </PageHeader>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        <div style={{ height: 620 }}>
          <Calendar
            localizer={localizer}
            events={events}
            startAccessor="start"
            endAccessor="end"
            views={['month', 'week', 'day']}
            popup
            eventPropGetter={eventStyleGetter}
            onSelectEvent={(ev) => setSel(ev.resource)}
            selectable
            date={calDate}
            onNavigate={setCalDate}
            view={calView}
            onView={setCalView}
            onSelectSlot={(slot) => {
              if (!canBook) return;
              setError('');
              // Take the DAY from the click and let the grid supply the time.
              // Carrying the clicked time through was how somebody landed on
              // 14:20 in a window that closes at noon and only found out on
              // pressing Book.
              setBooking({
                child: '', psychologist: '',
                date: format(slot.start, 'yyyy-MM-dd'), time: '',
                purpose: 'session', duration: 60, notes: '',
              });
            }}
            style={{ fontFamily: 'var(--font-sans)', fontSize: 13, height: '100%' }}
          />
        </div>
      </div>

      <Card eyebrow={isPsych ? 'Your availability' : 'Psychologist availability'} title="Availability blocks" padding="20px">
        {myBlocks.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {isPsych ? 'No availability yet — add the times you accept bookings.' : 'No availability blocks defined yet.'}
          </div>
        ) : isPsych ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {myBlocks.map((b) => (
              <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 'var(--radius-lg)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
                <Icon name="clock" size={16} style={{ color: 'var(--blue-600)' }} />
                <div style={{ flex: 1 }}>
                  <span style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>
                    {b.date || WEEKDAYS[b.weekday]}s · {String(b.start_time).slice(0, 5)}–{String(b.end_time).slice(0, 5)}
                  </span>
                </div>
                <Badge tone="neutral" size="sm">{b.capacity} slot{b.capacity === 1 ? '' : 's'}</Badge>
                <button title="Edit" onClick={() => openEditBlock(b)} style={iconBtn('var(--blue-600)')}><Icon name="pencil" size={14} /></button>
                <button title="Remove" onClick={() => removeBlock(b)} style={iconBtn('var(--red-700)')}><Icon name="trash-2" size={14} /></button>
              </div>
            ))}
          </div>
        ) : (
          <>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 12 }}>
              Grouped per psychologist — open a card to see and manage their full schedule.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {psyGroups.map((g) => (
                <div key={g.id} role="button" tabIndex={0}
                  onClick={() => setOpenPsy({ id: g.id, name: g.name })}
                  onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); setOpenPsy({ id: g.id, name: g.name }); } }}
                  {...hoverLift()}
                  style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)', background: 'var(--surface)', boxShadow: 'var(--shadow-xs)', cursor: 'pointer', transition: 'var(--transition-base)' }}>
                  <Avatar name={g.name} tone="brand" />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-strong)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.name}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {g.blocks.length} block{g.blocks.length === 1 ? '' : 's'}{daysLabel(g.blocks) ? ` · ${daysLabel(g.blocks)}` : ''}
                    </div>
                  </div>
                  <Icon name="chevron-right" size={16} style={{ color: 'var(--text-faint)' }} />
                </div>
              ))}
            </div>
          </>
        )}
      </Card>
      </>
      )}

      {/* Booking drawer */}
      {booking && (
        <div onClick={() => setBooking(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70 }}>
          <form onSubmit={book} onClick={(e) => e.stopPropagation()} style={{ width: 420, maxWidth: '92%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', background: 'var(--ink-50)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>
              {booking.id ? 'Move appointment' : 'Book appointment'}
            </div>
            <div className="racco-scroll" style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{String(error)}</Alert>}
              <FormField label="Child" required>
                <Select value={booking.child} onChange={(e) => {
                  const childId = e.target.value;
                  const c = children.find((x) => String(x.id) === childId);
                  setBooking({ ...booking, child: childId, psychologist: booking.psychologist || (c?.psychologist ?? '') });
                }}>
                  <option value="">— Select child —</option>
                  {children.map((c) => <option key={c.id} value={c.id}>{c.fullname}</option>)}
                </Select>
              </FormField>
              {!isPsych && (
                <FormField label="Psychologist" required hint="Bookings must fall inside their availability.">
                  <Select value={booking.psychologist} onChange={(e) => setBooking({ ...booking, psychologist: e.target.value })}>
                    <option value="">— Select psychologist —</option>
                    {psychologists.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </Select>
                </FormField>
              )}
              {/* Purpose and length come BEFORE the day, because both change
                  which times are free. Asking for a time first and letting the
                  length invalidate it afterwards is how the old form produced
                  a refusal only once you pressed Book. */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <FormField label="Purpose">
                  <Select value={booking.purpose} onChange={(e) => setBooking({ ...booking, purpose: e.target.value })}>
                    {PURPOSES.map((p) => <option key={p.v} value={p.v}>{p.label}</option>)}
                  </Select>
                </FormField>
                <FormField label="Length">
                  <Select
                    value={booking.duration}
                    onChange={(e) => setBooking({ ...booking, duration: Number(e.target.value), time: '' })}
                  >
                    {DURATIONS.map((d) => <option key={d.v} value={d.v}>{d.label}</option>)}
                  </Select>
                </FormField>
              </div>
              <FormField label="Day" required>
                <Input
                  type="date" value={booking.date} min={todayIso()}
                  onChange={(e) => setBooking({ ...booking, date: e.target.value, time: '' })}
                />
              </FormField>

              {/* The time is chosen, not typed. Every option here has been put
                  through the same check the booking endpoint runs, so a slot
                  on this grid cannot come back refused. */}
              <FormField label="Time" required>
                {(!bookingPsy || !booking.date) ? (
                  <div style={SLOT_EMPTY}>
                    Choose {isPsych ? 'a day' : 'a psychologist and a day'} to see open times.
                  </div>
                ) : slotsBusy ? (
                  <div style={SLOT_EMPTY}>Checking that day&hellip;</div>
                ) : daySlots?.slots?.length ? (
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {daySlots.slots.map((s) => {
                      const on = booking.time === s.start;
                      return (
                        <button
                          key={s.start} type="button" aria-pressed={on}
                          title={`${s.start} to ${s.end}`}
                          onClick={() => setBooking({ ...booking, time: s.start })}
                          style={{
                            padding: '7px 12px', borderRadius: 'var(--radius-control)',
                            border: `1px solid ${on ? 'var(--blue-600)' : 'var(--border)'}`,
                            background: on ? 'var(--blue-600)' : 'var(--surface)',
                            color: on ? '#fff' : 'var(--text-strong)',
                            fontFamily: 'var(--font-sans)', fontWeight: 700,
                            fontSize: 12.5, cursor: 'pointer', minWidth: 66,
                          }}
                        >
                          {s.start}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  /* Never a blank panel. "They do not work Wednesdays", "the
                     day is full" and "that length does not fit" send somebody
                     to three different next actions. */
                  <Alert tone="amber" icon={<Icon name="calendar" size={17} />}>
                    {daySlots?.reason || 'No open times that day.'}
                  </Alert>
                )}
              </FormField>
              <FormField label="Notes">
                <Input value={booking.notes} onChange={(e) => setBooking({ ...booking, notes: e.target.value })} />
              </FormField>
            </div>
            <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
              {/* Says what is still missing rather than sitting there grey. */}
              <Button
                type="submit" variant="primary" fullWidth
                disabled={!booking.child || !booking.date || !booking.time || (!isPsych && !booking.psychologist)}
                title={!booking.child ? 'Choose a child'
                  : (!isPsych && !booking.psychologist) ? 'Choose a psychologist'
                    : !booking.date ? 'Choose a day'
                      : !booking.time ? 'Choose a time' : 'Book it'}
                iconLeft={<Icon name="calendar" size={16} />}
              >
                {booking.time ? `Book ${booking.time}` : 'Book'}
              </Button>
            </div>
          </form>
        </div>
      )}

      {/* Availability drawer */}
      {blockForm && (
        <div onClick={() => setBlockForm(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70 }}>
          <form onSubmit={saveBlock} onClick={(e) => e.stopPropagation()} style={{ width: 400, maxWidth: '92%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', background: 'var(--ink-50)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>
              {blockForm.id ? 'Edit Availability' : 'Add Availability'}
            </div>
            <div className="racco-scroll" style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
              {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{String(error)}</Alert>}
              {!isPsych && (
                blockForm.id ? (
                  <FormField label="Psychologist">
                    <Input value={blockForm.psychologist_name || ''} disabled />
                  </FormField>
                ) : (
                  <FormField label="Psychologist" required hint="Who this availability belongs to.">
                    <Select value={blockForm.psychologist} onChange={(e) => setBlockForm({ ...blockForm, psychologist: e.target.value })}>
                      <option value="">— Select psychologist —</option>
                      {psychologists.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </Select>
                  </FormField>
                )
              )}
              <FormField label="Repeat">
                <Select value={blockForm.mode} onChange={(e) => setBlockForm({ ...blockForm, mode: e.target.value })}>
                  <option value="weekly">Every week</option>
                  <option value="date">Single date</option>
                </Select>
              </FormField>
              {blockForm.mode === 'weekly' && !blockForm.id ? (
                <FormField label="Weekdays" required hint="Tick every day this window repeats — one block is created per day.">
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {WEEKDAYS.map((d, i) => {
                      const on = blockForm.weekdays.includes(i);
                      return (
                        <label key={d} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 11px', borderRadius: 'var(--radius-pill)', border: `1px solid ${on ? 'var(--blue-500)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : 'var(--surface)', fontSize: 12.5, fontWeight: 700, color: on ? 'var(--blue-700)' : 'var(--text-body)', cursor: 'pointer' }}>
                          <input type="checkbox" checked={on} style={{ accentColor: 'var(--blue-600)' }}
                            onChange={() => setBlockForm((f) => ({ ...f, weekdays: on ? f.weekdays.filter((x) => x !== i) : [...f.weekdays, i] }))} />
                          {d.slice(0, 3)}
                        </label>
                      );
                    })}
                  </div>
                </FormField>
              ) : blockForm.mode === 'weekly' ? (
                <FormField label="Weekday">
                  <Select value={blockForm.weekday} onChange={(e) => setBlockForm({ ...blockForm, weekday: e.target.value })}>
                    {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                  </Select>
                </FormField>
              ) : (
                <FormField label="Date">
                  <Input type="date" value={blockForm.date} onChange={(e) => setBlockForm({ ...blockForm, date: e.target.value })} />
                </FormField>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <FormField label="From"><Input type="time" value={blockForm.start_time} onChange={(e) => setBlockForm({ ...blockForm, start_time: e.target.value })} /></FormField>
                <FormField label="To"><Input type="time" value={blockForm.end_time} onChange={(e) => setBlockForm({ ...blockForm, end_time: e.target.value })} /></FormField>
              </div>
              <FormField label="Capacity" hint="How many appointments fit in this block per day.">
                <Input type="number" min="1" value={blockForm.capacity} onChange={(e) => setBlockForm({ ...blockForm, capacity: e.target.value })} />
              </FormField>
            </div>
            <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
              <Button type="submit" variant="primary" fullWidth
                disabled={!isPsych && !blockForm.id && !blockForm.psychologist}
                iconLeft={<Icon name="save" size={16} />}>
                {blockForm.id ? 'Save Changes' : 'Save Availability'}
              </Button>
            </div>
          </form>
        </div>
      )}

      {/* Appointment detail */}
      {sel && (
        <div onClick={() => setSel(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 80 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 420, maxWidth: '92%', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', padding: 22, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{sel.child_name}</div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{new Date(sel.start).toLocaleString()} · {sel.duration_minutes} min</div>
              </div>
              <Badge tone={STATUS_TONE[sel.status]} dot>{sel.status.replace('_', '-')}</Badge>
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-body)' }}>
              {PURPOSES.find((p) => p.v === sel.purpose)?.label || sel.purpose} with {sel.psychologist_name || '—'}
              {sel.notes ? ` · ${sel.notes}` : ''}
            </div>
            {sel.status === 'scheduled' && (() => {
              // An outcome is a claim about something that happened, so the
              // server refuses one ahead of time. Offer it disabled with the
              // reason rather than letting the click earn an error.
              const started = new Date(sel.start) <= new Date();
              const outcomeTitle = started ? undefined : 'This session has not happened yet.';
              return (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {(isPsych || role === 'Administrator') && (
                    <Button variant="primary" disabled={!started} title={outcomeTitle}
                      onClick={() => setStatus(sel, 'complete')} iconLeft={<Icon name="check" size={15} />}>
                      Completed
                    </Button>
                  )}
                  {(isPsych || role === 'Administrator') && (
                    <Button variant="secondary" disabled={!started} title={outcomeTitle}
                      onClick={() => setStatus(sel, 'no_show')} iconLeft={<Icon name="alert-triangle" size={15} />}>
                      No-show
                    </Button>
                  )}
                  {canBook && (
                    <Button variant="secondary" onClick={() => openReschedule(sel)}
                      iconLeft={<Icon name="calendar" size={15} />}>
                      Reschedule
                    </Button>
                  )}
                  <Button variant="danger" onClick={() => setStatus(sel, 'cancel')} iconLeft={<Icon name="x" size={15} />}>Cancel</Button>
                </div>
              );
            })()}
          </div>
        </div>
      )}

    </div>
  );
}
