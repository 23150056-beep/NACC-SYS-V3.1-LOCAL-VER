import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import {
  Card, Button, Badge, Input, FormField, Avatar, RoleBadge, Icon,
  RoleAccessPanel, Skeleton, EmptyState, PAGE, hoverLift,
} from '../ui';
import { eventText, eventDestination } from '../components/Topbar';
import { exactDate, shortDate, timeAgo } from '../utils/time';
import api from '../api/client';
import {
  getMyPhone, requestPhoneCode, confirmPhoneCode, removeMyPhone,
} from '../api/sms';

/* Your own account, on one page.
 *
 * Two columns, the shape most people already know from a social profile: who
 * you are on the left, what you have been doing on the right. That is not
 * decoration — the left column is stable reference (role, contact, how you
 * sign in) and the right is a stream, and they want different reading.
 *
 * Everything on the right is REAL, from endpoints a Staff or Psychologist
 * account can already call: /api/activity/ (already scoped per role by the
 * server), /api/children/ (scoped to a psychologist's assigned children) and
 * /api/appointments/ (scoped to their own). Nothing new was needed on the
 * server to make this page worth opening.
 *
 * The "Links" card is the one thing on this page a person writes rather than
 * reads. It goes to /api/auth/me/profile/, which is bound to the caller and
 * takes no id — so there is no request shape that edits somebody else's. The
 * server normalises what gets typed (a full URL, a bare handle, an @handle
 * all mean the same thing) and refuses another site or a link to a post, so
 * the messages below come from it rather than being guessed at here.
 *
 * There is no home address field. The earlier prototype had one; nothing in
 * the system reads a staff member's home address, and collecting personal
 * data with no purpose is what RA 10173 asks agencies not to do.
 */

const EMPTY = { facebook: '', twitter: '', instagram: '' };

// Short enough not to truncate in a 300px field. The note under the form is
// where "or just your username" is said, once, rather than three times.
const FIELDS = [
  ['facebook', 'Facebook', 'facebook', 'facebook.com/your.name'],
  ['twitter', 'X (Twitter)', 'twitter', 'x.com/yourhandle'],
  ['instagram', 'Instagram', 'instagram', 'instagram.com/yourhandle'],
];

/* One row of the Intro card. Renders the dash rather than collapsing when a
 * value is missing: "not recorded" is information, and a row that disappears
 * makes two accounts look structurally different when they are not. */
function Fact({ icon, label, children, mono = false }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                     width: 30, height: 30, flex: 'none', borderRadius: 'var(--radius-sm)',
                     background: 'var(--blue-50)', color: 'var(--blue-600)' }}>
        <Icon name={icon} size={15} />
      </span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.05em',
                      textTransform: 'uppercase', color: 'var(--text-faint)' }}>{label}</div>
        <div className={mono ? 'racco-mono' : undefined}
             style={{ fontSize: mono ? 12.5 : 13.5, color: 'var(--text-body)',
                      lineHeight: 1.5, overflowWrap: 'anywhere' }}>
          {children || <span style={{ color: 'var(--text-faint)' }}>Not recorded</span>}
        </div>
      </div>
    </div>
  );
}

export default function MyProfile() {
  const { user } = useAuth();
  const toast = useToast();
  const role = user?.role_name;
  const isPsych = role === 'Psychologist';

  const [form, setForm] = useState(EMPTY);
  const [saved, setSaved] = useState(null);   // null until the server answers
  const [fieldErrors, setFieldErrors] = useState({});
  const [busy, setBusy] = useState(false);
  // Mobile number: 'idle' until they ask for a code, 'code' while a code is
  // outstanding. Kept separate from the links form — one is a text box, the
  // other is a two-step exchange with a handset.
  const [phone, setPhone] = useState(null);          // null until the server answers
  const [phoneInput, setPhoneInput] = useState('');
  const [phoneStage, setPhoneStage] = useState('idle');
  const [phoneCode, setPhoneCode] = useState('');
  const [phoneError, setPhoneError] = useState('');
  const [phoneBusy, setPhoneBusy] = useState(false);
  const [children, setChildren] = useState(null);
  const [appointments, setAppointments] = useState(null);
  const [activity, setActivity] = useState(null);

  useEffect(() => {
    api.get('/auth/me/profile/')
      .then((r) => {
        const next = { ...EMPTY, ...r.data };
        setForm(next);
        setSaved(next);
      })
      // An account that has never saved still gets a row back, so a failure
      // here is the network rather than a missing profile. Show the empty
      // form; the save will report properly if it is still down.
      .catch(() => setSaved(EMPTY));
  }, []);

  useEffect(() => {
    getMyPhone().then(setPhone).catch(() => setPhone({ phone: '', phone_verified: false }));
  }, []);

  useEffect(() => {
    // Each fails independently: a psychologist with no caseload yet should
    // still get their activity, and vice versa.
    api.get('/children/').then((r) => setChildren(r.data)).catch(() => setChildren([]));
    api.get('/appointments/').then((r) => setAppointments(r.data)).catch(() => setAppointments([]));
    api.get('/activity/').then((r) => setActivity(r.data)).catch(() => setActivity([]));
  }, []);

  const dirty = useMemo(
    () => !!saved && FIELDS.some(([k]) => (form[k] || '') !== (saved[k] || '')),
    [form, saved]);
  const hasLinks = useMemo(
    () => !!saved && FIELDS.some(([k]) => (saved[k] || '').trim()), [saved]);

  const stats = useMemo(() => {
    if (children === null || appointments === null) return null;
    const active = children.filter((c) => c.status !== 'archived'
      && (c.case_status || 'active') !== 'inactive');
    const now = new Date();
    const weekEnd = new Date(now); weekEnd.setDate(now.getDate() + 7);
    const upcoming = appointments.filter((a) => {
      const at = new Date(a.scheduled_at || a.start || a.date);
      return a.status !== 'cancelled' && at >= now && at <= weekEnd;
    });
    const done = appointments.filter((a) => a.status === 'completed');
    return { active: active.length, upcoming: upcoming.length, done: done.length };
  }, [children, appointments]);

  const write = async (payload, message) => {
    setBusy(true);
    setFieldErrors({});
    try {
      const { data } = await api.patch('/auth/me/profile/', payload);
      const next = { ...EMPTY, ...data };
      setForm(next);
      setSaved(next);
      toast.success(message);
    } catch (err) {
      const body = err.response?.data;
      if (body && typeof body === 'object' && !body.detail) {
        // The server rejected a value — "that looks like a link to a post"
        // and so on. Its wording is better than anything guessed at here.
        const mapped = {};
        Object.entries(body).forEach(([k, v]) => {
          mapped[k] = Array.isArray(v) ? v.join(' ') : String(v);
        });
        setFieldErrors(mapped);
      } else {
        toast.error(body?.detail || 'Could not save. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  const askForCode = async (e) => {
    e.preventDefault();
    setPhoneBusy(true); setPhoneError('');
    try {
      const { detail } = await requestPhoneCode(phoneInput);
      setPhoneStage('code');
      toast.success(detail);
    } catch (err) {
      const body = err.response?.data;
      // The server's wording: it knows the difference between a landline and
      // a mistyped mobile, and between those and a gateway that refused.
      setPhoneError(body?.phone || body?.detail || 'Could not send the code.');
    } finally { setPhoneBusy(false); }
  };

  const confirmCode = async (e) => {
    e.preventDefault();
    setPhoneBusy(true); setPhoneError('');
    try {
      const data = await confirmPhoneCode(phoneCode);
      setPhone(data);
      setPhoneStage('idle'); setPhoneCode(''); setPhoneInput('');
      toast.success(data.detail);
    } catch (err) {
      setPhoneError(err.response?.data?.code || 'That code was not accepted.');
    } finally { setPhoneBusy(false); }
  };

  const dropPhone = async () => {
    setPhoneBusy(true); setPhoneError('');
    try {
      setPhone(await removeMyPhone());
      setPhoneStage('idle'); setPhoneInput(''); setPhoneCode('');
      toast.success('Number removed. Text notifications are off.');
    } catch { toast.error('Could not remove the number.'); }
    finally { setPhoneBusy(false); }
  };

  const save = (e) => { e.preventDefault(); write(form, 'Links saved.'); };
  const clear = () => write(EMPTY, 'Links removed.');

  return (
    <div style={{ ...PAGE, maxWidth: 1080 }}>

      {/* ---------------------------- header ---------------------------- */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-sm)',
                    overflow: 'hidden', marginBottom: 18 }}>
        {/* The same gradient as the sign-in page, so the account screen and the
            door you came through belong to one system. */}
        <div style={{ height: 132, position: 'relative',
                      background: 'linear-gradient(155deg, var(--blue-700), var(--blue-600) 60%, var(--blue-800))' }}>
          <div style={{ position: 'absolute', inset: 0,
                        background: 'radial-gradient(120% 120% at 100% 0%, rgba(255,172,42,0.24), transparent 55%)' }} />
        </div>
        {/* Only the avatar crosses the cover line. An earlier version let the
            whole row overlap, which put dark heading text on the blue and made
            the name unreadable — the thing the header exists to show. */}
        <div style={{ padding: '0 26px 22px' }}>
          <div style={{ marginTop: -46, marginBottom: 12, position: 'relative', zIndex: 1 }}>
            <span style={{ display: 'inline-block', borderRadius: '50%', padding: 4,
                           background: 'var(--surface)', boxShadow: 'var(--shadow-md)' }}>
              <Avatar name={user?.fullname || user?.username || 'User'} tone="brand" size="lg" />
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
                        gap: 16, flexWrap: 'wrap' }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 24,
                            lineHeight: 1.15, color: 'var(--text-strong)' }}>
                {user?.fullname || user?.username}
              </div>
              <div className="racco-mono" style={{ fontSize: 12.5, color: 'var(--text-muted)',
                                                   marginTop: 3, overflowWrap: 'anywhere' }}>
                {user?.email}
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', paddingTop: 3 }}>
              {role && <RoleBadge role={role} solid />}
              <Badge tone={user?.google_linked ? 'brand' : 'neutral'} size="sm">
                <Icon name={user?.google_linked ? 'badge-check' : 'key-round'} size={12} />
                {user?.google_linked ? 'Google' : 'Password'}
              </Badge>
            </div>
          </div>
        </div>
      </div>

      {/* --------------------------- two columns -------------------------- */}
      <div className="racco-profile-grid">

        {/* ----------------------------- left ----------------------------- */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          <Card eyebrow="Account" title="Intro" padding="20px">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 4 }}>
              <Fact icon="mail" label="Email" mono>{user?.email}</Fact>
              <Fact icon="phone" label="Contact number">{user?.contact_details}</Fact>
              <Fact icon={user?.google_linked ? 'badge-check' : 'key-round'} label="Signs in with">
                {user?.google_linked
                  ? 'Google — this address, verified by Google'
                  : 'Email and password'}
              </Fact>
              <Fact icon="clock" label="Last sign-in">
                {user?.last_login
                  ? <span title={exactDate(user.last_login)}>{timeAgo(user.last_login)}</span>
                  : null}
              </Fact>
              <Fact icon="history" label="Member since">
                {user?.created_at ? shortDate(user.created_at) : null}
              </Fact>
            </div>
            {role && (
              <div style={{ marginTop: 16 }}>
                <RoleAccessPanel to={role} />
              </div>
            )}
            <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-faint)', marginTop: 12 }}>
              Your name, email and role are set by an administrator. Ask them if
              any of this is wrong.
            </div>
          </Card>

          {/* Text notifications. Separate card from the links because this
              one has consequences: a verified number starts receiving
              messages, and an unverified one silently receives nothing. The
              state has to be legible at a glance. */}
          <Card padding="20px" eyebrow="Notifications" title="Mobile number">
            {phone === null ? (
              <div style={{ marginTop: 10 }}><Skeleton height={58} radius="var(--radius-md)" /></div>
            ) : phone.phone_verified && phoneStage === 'idle' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                 width: 34, height: 34, flex: 'none', borderRadius: 'var(--radius-sm)',
                                 background: 'var(--success-50)', color: 'var(--success-600)' }}>
                    <Icon name="badge-check" size={17} />
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div className="racco-mono" style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--text-strong)' }}>
                      {phone.phone_display || phone.phone}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--success-600)', fontWeight: 700 }}>
                      Verified — text notifications are on
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                  <Button type="button" variant="secondary" size="sm" disabled={phoneBusy}
                          onClick={() => { setPhoneStage('edit'); setPhoneInput(''); setPhoneError(''); }}
                          iconLeft={<Icon name="pencil" size={15} />}>Change</Button>
                  <Button type="button" variant="ghost" size="sm" disabled={phoneBusy}
                          onClick={dropPhone} style={{ color: 'var(--red-600)' }}
                          iconLeft={<Icon name="trash-2" size={15} />}>Turn off</Button>
                </div>
              </div>
            ) : phoneStage === 'code' ? (
              <form onSubmit={confirmCode} style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
                <FormField label="Code from the text message" error={phoneError}>
                  <Input value={phoneCode} onChange={(e) => { setPhoneCode(e.target.value); setPhoneError(''); }}
                         placeholder="123456" inputMode="numeric" autoComplete="one-time-code"
                         invalid={!!phoneError} disabled={phoneBusy}
                         leading={<Icon name="message-square" size={16} />} />
                </FormField>
                <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                  <Button type="submit" variant="primary" size="sm" disabled={phoneBusy || !phoneCode.trim()}
                          iconLeft={<Icon name="check" size={15} />}>
                    {phoneBusy ? 'Checking…' : 'Verify'}
                  </Button>
                  <Button type="button" variant="ghost" size="sm" disabled={phoneBusy}
                          onClick={() => { setPhoneStage('idle'); setPhoneCode(''); setPhoneError(''); }}>
                    Cancel
                  </Button>
                </div>
              </form>
            ) : (
              <form onSubmit={askForCode} style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
                <FormField label="Mobile number" error={phoneError}
                           hint="We text a code to check it is yours before anything else is sent.">
                  <Input value={phoneInput} onChange={(e) => { setPhoneInput(e.target.value); setPhoneError(''); }}
                         placeholder="0917 123 4567" inputMode="tel" autoComplete="tel"
                         invalid={!!phoneError} disabled={phoneBusy}
                         leading={<Icon name="phone" size={16} />} />
                </FormField>
                <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                  <Button type="submit" variant="primary" size="sm"
                          disabled={phoneBusy || !phoneInput.trim()}
                          iconLeft={<Icon name="send" size={15} />}>
                    {phoneBusy ? 'Sending…' : 'Send me a code'}
                  </Button>
                  {phoneStage === 'edit' && (
                    <Button type="button" variant="ghost" size="sm" disabled={phoneBusy}
                            onClick={() => { setPhoneStage('idle'); setPhoneError(''); }}>Cancel</Button>
                  )}
                </div>
              </form>
            )}
            <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-faint)', marginTop: 13 }}>
              Used for a new case assignment, a temporary password notice, and a
              reminder of the next day&rsquo;s sessions. Messages never contain a
              child&rsquo;s name, case details, or your password — they tell you
              to sign in.
            </div>
          </Card>

          {/* The one thing on this page a person writes. */}
          <Card padding="20px" eyebrow="Optional" title="Links">
            {saved === null ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 10 }}>
                {[0, 1, 2].map((i) => <Skeleton key={i} height={58} radius="var(--radius-md)" />)}
              </div>
            ) : (
              <form onSubmit={save} style={{ display: 'flex', flexDirection: 'column', gap: 13, marginTop: 6 }}>
                {FIELDS.map(([key, label, icon, placeholder]) => (
                  <FormField key={key} label={label} error={fieldErrors[key]}>
                    <Input
                      value={form[key]} placeholder={placeholder} disabled={busy}
                      invalid={!!fieldErrors[key]}
                      leading={<Icon name={icon} size={16} />}
                      onChange={(e) => {
                        setForm({ ...form, [key]: e.target.value });
                        setFieldErrors((fe) => ({ ...fe, [key]: undefined }));
                      }}
                    />
                  </FormField>
                ))}
                <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
                  <Button type="submit" variant="primary" size="sm" disabled={!dirty || busy}
                          iconLeft={<Icon name="save" size={15} />}>
                    {busy ? 'Saving…' : dirty ? 'Save' : 'Saved'}
                  </Button>
                  {hasLinks && (
                    <Button type="button" variant="ghost" size="sm" onClick={clear} disabled={busy}
                            style={{ color: 'var(--red-600)' }}
                            iconLeft={<Icon name="trash-2" size={15} />}>
                      Remove
                    </Button>
                  )}
                </div>
              </form>
            )}
            <div style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-faint)', marginTop: 13 }}>
              Optional, and only you can see them. Paste the link to your
              profile or just your username — either works. Remove takes them
              off your account for good.
            </div>
          </Card>
        </div>

        {/* ----------------------------- right ---------------------------- */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>

          <div>
            <div className="racco-eyebrow" style={{ marginBottom: 10 }}>At a glance</div>
            <div className="racco-profile-stats">
              {stats === null ? (
                [0, 1, 2].map((i) => <Skeleton key={i} height={96} radius="var(--radius-lg)" />)
              ) : (
                <>
                  <StatTile icon="folder-heart" tone="brand" value={stats.active}
                            label={isPsych ? 'Children assigned to you' : 'Active records'}
                            hint={isPsych ? 'Open cases you are responsible for' : 'Cases you can work on'} />
                  <StatTile icon="calendar-clock" tone="amber" value={stats.upcoming}
                            label="Sessions in the next 7 days"
                            hint={isPsych ? 'Booked with you' : 'Across the office'} />
                  <StatTile icon="check" tone="success" value={stats.done}
                            label="Sessions completed"
                            hint={isPsych ? 'Marked complete by you' : 'Marked complete'} />
                </>
              )}
            </div>
          </div>

          <Card eyebrow="Your account" title="Recent activity" padding="0">
            {activity === null && (
              <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
                {[0, 1, 2, 3].map((i) => <Skeleton key={i} height={16} />)}
              </div>
            )}
            {activity !== null && activity.length === 0 && (
              <EmptyState
                icon={<Icon name="history" size={24} />}
                title="Nothing yet"
                description={isPsych
                  ? 'Assignments and updates on your cases will appear here.'
                  : 'Records you add or edit will appear here.'}
              />
            )}
            {activity !== null && activity.length > 0 && (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {activity.slice(0, 12).map((e) => (
                  <li key={e.id} style={{ borderTop: '1px solid var(--ink-100)' }}>
                    <Link
                      to={eventDestination(e, role)}
                      {...hoverLift({ lift: 0 })}
                      style={{ display: 'flex', gap: 12, padding: '13px 20px',
                               textDecoration: 'none', color: 'inherit', alignItems: 'flex-start' }}
                    >
                      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                     width: 30, height: 30, flex: 'none', marginTop: 1,
                                     borderRadius: 'var(--radius-sm)',
                                     background: 'var(--ink-50)', color: 'var(--text-muted)' }}>
                        <Icon name={e.action === 'created' ? 'plus'
                          : e.action === 'archived' ? 'archive'
                            : e.action === 'login' ? 'log-in' : 'pencil'} size={14} />
                      </span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', fontSize: 13.5, fontWeight: 600,
                                       color: 'var(--text-strong)', overflowWrap: 'anywhere' }}>
                          {eventText(e)}
                        </span>
                        <span style={{ display: 'block', fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}
                              title={exactDate(e.created_at)}>
                          {e.actor_label} · {timeAgo(e.created_at)}
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

/* A stat that fits three-up in a column, so StatCard's roomier padding would
 * push the row past the fold on a laptop. */
function StatTile({ icon, tone, value, label, hint }) {
  const colors = { brand: 'var(--blue-600)', amber: 'var(--amber-600)', success: 'var(--success-600)' };
  const soft = { brand: 'var(--blue-50)', amber: 'var(--amber-50)', success: 'var(--success-50)' };
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-sm)',
                  padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                     width: 30, height: 30, borderRadius: 'var(--radius-sm)',
                     background: soft[tone], color: colors[tone] }}>
        <Icon name={icon} size={16} />
      </span>
      <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 27,
                     lineHeight: 1, color: 'var(--text-strong)', fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </span>
      <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-body)', lineHeight: 1.35 }}>
        {label}
      </span>
      <span style={{ fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.4 }}>{hint}</span>
    </div>
  );
}
