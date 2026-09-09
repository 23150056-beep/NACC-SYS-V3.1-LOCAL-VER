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
  const [slotHints, setSlotHints] = useState(null);
  const [openPsy, setOpenPsy] = useState(null); // { id, name } — full-page availability view (admin/staff)

  const load = useCallback(() => {
    api.get('/appointments/').then((r) => setAppointments(r.data)).catch(() => {});
    api.get('/availability/').then((r) => setBlocks(r.data)).catch(() => {});
    api.get('/children/').then((r) => setChildren(r.data.filter((c) => c.status === 'active'))).catch(() => {});
    if (!isPsych) api.get('/psychologists/').then((r) => setPsychologists(r.data)).catch(() => {});
  }, [isPsych]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!booking?.child) { setSlotHints(null); return; }
    api.get(`/availability/next-slots/?child=${booking.child}`).then((r) => setSlotHints(r.data)).catch(() => setSlotHints(null));
  }, [booking?.child]);
  // Quietly warms today's brief cache in the background. prefetchBriefs()
  // already swallows its own errors (including a 503 when the assistant is
  // off) — this screen must never know or care whether it succeeded.
  useEffect(() => {
    prefetchBriefs();
    /* eslint-disable-next-line */
  }, []);

  const openBooking = () => {
    setError('');
    setBooking({ child: '', psychologist: '', date: '', time: '09:00', purpose: 'session', duration: 60, notes: '' });
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
    const startIso = `${booking.date}T${booking.time}:00`;
    try {
      await api.post('/appointments/', {
        child: booking.child, psychologist: booking.psychologist || undefined,
        start: startIso, duration_minutes: booking.duration || 60,
        purpose: booking.purpose, notes: booking.notes || '',
      });
      toast.success('Appointment booked');
      setBooking(null); load();
    } catch (err) {
      const d = err.response?.data;
      setError(d?.start || d?.psychologist || d?.child || JSON.stringify(d || 'Booking failed'));
    }
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
            defaultView="month"
            popup
            eventPropGetter={eventStyleGetter}
            onSelectEvent={(ev) => setSel(ev.resource)}
            selectable
            onSelectSlot={(slot) => {
              if (!canBook) return;
              setError('');
              const t = format(slot.start, 'HH:mm');
              setBooking({
                child: '', psychologist: isPsych ? '' : '',
                date: format(slot.start, 'yyyy-MM-dd'), time: t === '00:00' ? '09:00' : t,
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
            <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', background: 'var(--ink-50)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>Book Appointment</div>
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
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <FormField label="Date" required>
                  <Input type="date" value={booking.date} onChange={(e) => setBooking({ ...booking, date: e.target.value })} />
                </FormField>
                <FormField label="Time" required>
                  <Input type="time" value={booking.time} onChange={(e) => setBooking({ ...booking, time: e.target.value })} />
                </FormField>
              </div>
              {slotHints?.slots?.length > 0 && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 6 }}>Next openings — {slotHints.psychologist}</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {slotHints.slots.map((s, i) => (
                      <button key={i} type="button" onClick={() => setBooking({ ...booking, date: s.date, time: s.start })}
                        style={{ padding: '5px 10px', borderRadius: 'var(--radius-pill)', border: '1px solid var(--blue-300)', background: 'var(--blue-50)', color: 'var(--blue-700)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 11.5, cursor: 'pointer' }}>
                        {s.weekday.slice(0, 3)} {s.date.slice(5)} · {s.start} ({s.remaining} open)
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <FormField label="Purpose">
                  <Select value={booking.purpose} onChange={(e) => setBooking({ ...booking, purpose: e.target.value })}>
                    {PURPOSES.map((p) => <option key={p.v} value={p.v}>{p.label}</option>)}
                  </Select>
                </FormField>
                <FormField label="Duration (min)">
                  <Input type="number" min="15" step="15" value={booking.duration} onChange={(e) => setBooking({ ...booking, duration: e.target.value })} />
                </FormField>
              </div>
              <FormField label="Notes">
                <Input value={booking.notes} onChange={(e) => setBooking({ ...booking, notes: e.target.value })} />
              </FormField>
            </div>
            <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
              <Button type="submit" variant="primary" fullWidth disabled={!booking.child || !booking.date || (!isPsych && !booking.psychologist)} iconLeft={<Icon name="calendar" size={16} />}>Book</Button>
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
            {sel.status === 'scheduled' && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(isPsych || role === 'Administrator') && <Button variant="primary" onClick={() => setStatus(sel, 'complete')} iconLeft={<Icon name="check" size={15} />}>Completed</Button>}
                {(isPsych || role === 'Administrator') && <Button variant="secondary" onClick={() => setStatus(sel, 'no_show')} iconLeft={<Icon name="alert-triangle" size={15} />}>No-show</Button>}
                <Button variant="danger" onClick={() => setStatus(sel, 'cancel')} iconLeft={<Icon name="x" size={15} />}>Cancel</Button>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
}
