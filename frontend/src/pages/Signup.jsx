import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Input, PasswordInput, FormField, Alert, Icon, ROLE_META } from '../ui';
import AuthLayout, { AuthLink } from '../components/AuthLayout';
import GoogleSignInButton from '../components/GoogleSignInButton';
import { useAuth } from '../context/AuthContext';
import api from '../api/client';

/* Requesting access, which is not the same as getting it.
 *
 * Whatever is submitted here becomes a PENDING account with no role and no
 * access — a request in a queue that an administrator approves. That is the
 * same thing the Google button has always done on a first sign-in, and this
 * page is the route for someone without a Google address.
 *
 * The page says so plainly rather than implying an account appears. Someone
 * who expects to be let straight in and is not will assume it failed, try
 * again, and end up with a second request in the queue.
 */

// What a person may say they are. Administrator is absent on purpose: that
// account is the agency's recovery path and is created only by another
// administrator, so a public queue for it is not a queue but a target. The
// server refuses it too — this list is the explanation, not the enforcement.
const REQUESTABLE = ['Staff', 'Psychologist'];

// Mirrors Django's default validators so the form can say what is wrong
// before a round trip. The server remains the authority; this only spares
// someone a rejected submission.
function passwordProblems(pw) {
  const out = [];
  if (pw.length < 8) out.push('at least 8 characters');
  if (/^\d+$/.test(pw)) out.push('not only numbers');
  return out;
}

function strengthOf(pw) {
  if (!pw) return null;
  let score = 0;
  if (pw.length >= 8) score += 1;
  if (pw.length >= 12) score += 1;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score += 1;
  if (/\d/.test(pw)) score += 1;
  if (/[^A-Za-z0-9]/.test(pw)) score += 1;
  if (score <= 2) return { label: 'Weak', tone: 'var(--red-600)', pct: 33 };
  if (score === 3) return { label: 'Fair', tone: 'var(--amber-500)', pct: 66 };
  return { label: 'Strong', tone: 'var(--success-600)', pct: 100 };
}

export default function Signup() {
  const navigate = useNavigate();
  const { loginWithGoogle } = useAuth();
  const [form, setForm] = useState({
    first_name: '', last_name: '', email: '', password: '',
  });
  // Kept out of `form` and asked ABOVE both routes, because both carry it —
  // the Google button sends it too. An earlier arrangement had this as a
  // select inside the form, below the Google button, defaulting to Staff:
  // every psychologist who clicked Google first was silently filed as
  // claiming to be Staff.
  const [role, setRole] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [done, setDone] = useState(false);

  const set = (k) => (e) => {
    const value = k === 'password' ? e.target.value.replace(/\s/g, '') : e.target.value;
    setForm((f) => ({ ...f, [k]: value }));
    setFieldErrors((fe) => ({ ...fe, [k]: undefined }));
  };

  const problems = passwordProblems(form.password);
  const strength = strengthOf(form.password);
  const complete = form.first_name.trim() && form.last_name.trim()
    && form.email.trim() && form.password && problems.length === 0;

  const submit = async (e) => {
    e.preventDefault();
    if (!complete || busy) return;
    setBusy(true);
    setError('');
    setFieldErrors({});
    try {
      // requested_role is optional server-side. An unanswered question is
      // sent as blank and recorded as "none stated" rather than guessed at.
      await api.post('/auth/signup/', { ...form, requested_role: role || '' });
      setDone(true);
    } catch (err) {
      const data = err.response?.data;
      if (data && typeof data === 'object' && !data.detail) {
        // Field errors from the serializer, shown against their own inputs.
        const mapped = {};
        Object.entries(data).forEach(([k, v]) => {
          mapped[k] = Array.isArray(v) ? v.join(' ') : String(v);
        });
        setFieldErrors(mapped);
        if (!Object.keys(mapped).length) setError('Please check the form and try again.');
      } else {
        setError(data?.detail || 'Could not send your request. Please try again.');
      }
    } finally {
      setBusy(false);
    }
  };

  const submitGoogle = async (credential) => {
    setError('');
    setBusy(true);
    try {
      await loginWithGoogle(credential, role);
      // An existing, approved account: Google is a sign-in as much as a
      // sign-up, and someone who lands here by mistake should just go in.
      navigate('/');
    } catch (err) {
      // 403 + pending_approval is the success case for this page — Google
      // verified them and the request is now in the queue, stated role or not.
      if (err.response?.status === 403
          && err.response?.data?.state === 'pending_approval') {
        setDone(true);
        return;
      }
      setError(err.response?.data?.detail
        || 'Google sign-up failed. Please try again or use the form below.');
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <AuthLayout
        title="Request sent"
        heading="Request sent"
        subheading="An administrator has to approve your access before you can sign in."
        footer={<AuthLink to="/login">Back to sign in</AuthLink>}
      >
        <div className="racco-auth-stack"
             style={{ marginTop: 'clamp(12px, 2vh, 22px)', display: 'flex', flexDirection: 'column' }}>
          {/* The same waiting panel the Google path shows on the sign-in page.
              Two doors, one outcome — it should look like one outcome. */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
                        textAlign: 'center', gap: 12, padding: 'clamp(16px, 3vh, 26px) 20px',
                        background: 'var(--blue-50)', border: '1px solid var(--blue-100)',
                        borderRadius: 'var(--radius-lg)' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                           width: 'clamp(42px, 5.6vh, 52px)', height: 'clamp(42px, 5.6vh, 52px)', flex: 'none', borderRadius: '50%',
                           background: 'var(--surface)', color: 'var(--blue-600)' }}>
              <Icon name="hourglass" size={24} />
            </span>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>
              Waiting for approval
            </div>
            <p style={{ margin: 0, fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-body)', maxWidth: 320 }}>
              Your request has been sent to the RACCO I administrator. Once they
              approve it and set your role, sign in and you will go straight in.
            </p>
          </div>
          {/* Said plainly, because the natural next move for someone who is
              not let in is to fill the form again and put a second request in
              the same queue. */}
          <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text-muted)', textAlign: 'center' }}>
            Nothing else is needed from you now. If it is urgent, contact your
            administrator directly — they can approve the request or create the
            account for you.
          </div>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Request access"
      heading="Request access"
      subheading="An administrator approves every new account before first use."
      footer={<>Already have an account? <AuthLink to="/login">Sign in</AuthLink></>}
    >
      <div className="racco-auth-stack"
           style={{ marginTop: 'clamp(12px, 2vh, 22px)', display: 'flex', flexDirection: 'column' }}>
        {error && (
          <div role="alert" aria-live="assertive">
            <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>
          </div>
        )}

        {/* Asked first because it governs both routes below. Optional: the
            server records "none stated" happily, and an administrator picks
            the real role either way. Same two cards as the Google role prompt
            on the sign-in page, so the question looks like the same question. */}
        <fieldset style={{ border: 'none', padding: 0, margin: 0, minWidth: 0 }}>
          <legend style={{ fontFamily: 'var(--font-sans)', fontWeight: 700,
                           fontSize: 'var(--text-sm)', color: 'var(--text-strong)',
                           padding: 0, marginBottom: 8 }}>
            What do you do at RACCO I?{' '}
            <span style={{ fontWeight: 600, color: 'var(--text-faint)' }}>(optional)</span>
          </legend>
          <div className="racco-auth-duo" style={{ gap: 8 }}>
            {REQUESTABLE.map((name) => {
              const meta = ROLE_META[name];
              const on = role === name;
              return (
                <button
                  key={name}
                  type="button"
                  aria-pressed={on}
                  // Clicking the chosen one again clears it. The question is
                  // optional, and a set of buttons with no way back is not.
                  onClick={() => setRole(on ? null : name)}
                  style={{ display: 'flex', alignItems: 'center', gap: 9, padding: 'clamp(8px, 1.4vh, 11px) 12px',
                           textAlign: 'left', cursor: 'pointer', minWidth: 0,
                           background: on ? meta.soft : 'var(--surface)',
                           border: `1px solid ${on ? meta.color : 'var(--border)'}`,
                           boxShadow: on ? `inset 0 0 0 1px ${meta.color}` : 'none',
                           borderRadius: 'var(--radius-md)', transition: 'var(--transition-base)' }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                 width: 30, height: 30, flex: 'none', borderRadius: 'var(--radius-sm)',
                                 background: meta.soft, color: meta.color }}>
                    <Icon name={meta.icon} size={16} />
                  </span>
                  <span style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 14,
                                 color: 'var(--text-strong)', minWidth: 0 }}>
                    {name}
                  </span>
                </button>
              );
            })}
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
            {role
              ? ROLE_META[role].desc
              : 'A claim — an administrator decides your role.'}
          </div>
        </fieldset>

        {/* Google first, as on the sign-in page: for staff and psychologists
            it is the route most will take, and one button both registers and
            signs in. The divider underneath is the button's own, so the whole
            block — button and rule — disappears together on an agency with no
            Google client configured. */}
        <GoogleSignInButton onCredential={submitGoogle} disabled={busy}
                            dividerLabel="or request with an email" />

        <form onSubmit={submit} className="racco-auth-stack-sm"
              style={{ display: 'flex', flexDirection: 'column' }}>
          {/* No autoFocus. The browser scrolls a focused field into view on
              load, which on a short window scrolled the heading and the role
              question out of sight before anyone had read them — and the
              first thing on this page is the Google button anyway. */}
          <div className="racco-auth-duo" style={{ gap: 12 }}>
            <FormField label="First name" error={fieldErrors.first_name} style={{ minWidth: 0 }}>
              <Input value={form.first_name} onChange={set('first_name')}
                     placeholder="Maria" autoComplete="given-name" required />
            </FormField>
            <FormField label="Last name" error={fieldErrors.last_name} style={{ minWidth: 0 }}>
              <Input value={form.last_name} onChange={set('last_name')}
                     placeholder="Santos" autoComplete="family-name" required />
            </FormField>
          </div>

          <FormField label="Work email" error={fieldErrors.email}>
            <Input type="email" value={form.email} onChange={set('email')}
                   placeholder="you@racco1.gov.ph" autoComplete="email"
                   leading={<Icon name="mail" size={16} />} required />
          </FormField>

          <FormField label="Password" error={fieldErrors.password}>
            <PasswordInput
              value={form.password} onChange={set('password')}
              placeholder="••••••••"
              autoComplete="new-password"
              required
            />
          </FormField>

          {/* Says what is still missing rather than only that it is wrong. */}
          {form.password && (
            <div style={{ marginTop: -6 }}>
              <div style={{ height: 4, borderRadius: 3, background: 'var(--divider-row)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${strength?.pct || 0}%`,
                              background: strength?.tone, transition: 'width var(--dur-fast) var(--ease-out)' }} />
              </div>
              <div style={{ fontSize: 11.5, marginTop: 5, color: 'var(--text-muted)' }}>
                {problems.length
                  ? <>Needs {problems.join(' and ')}.</>
                  : <>Password strength: <strong style={{ color: strength?.tone }}>{strength?.label}</strong></>}
              </div>
            </div>
          )}

          <Button type="submit" variant="primary" size="lg" fullWidth
                  style={{ height: 'clamp(42px, 5.8vh, 50px)' }}
                  disabled={!complete || busy}
                  iconRight={busy ? null : <Icon name="arrow-right" size={18} />}>
            {busy ? 'Sending…' : 'Request access'}
          </Button>
        </form>

        {/* Hidden on a short window (index.css): it restates the subheading,
            so it is the first thing that can go. */}
        <div className="racco-auth-fineprint"
             style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text-faint)' }}>
          This system holds children&rsquo;s records. Requests are reviewed before
          any access is granted.
        </div>
      </div>
    </AuthLayout>
  );
}
