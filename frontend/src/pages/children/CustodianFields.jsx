import { useState } from 'react';
import api from '../../api/client';
import { Badge, Button, FormField, Icon, Input } from '../../ui';
import { firstError } from '../../utils/errors';
import { shortDate } from '../../utils/time';
import { consentKey } from './recordForm';

/* The custodian on the Present Environment step: who the child lives with
 * now, their mobile number, and whether the system may text them about
 * appointments (owner's decision, 29 Sep 2026; backend children/custodian.py).
 *
 * Texts go only when BOTH are recorded:
 * - consent - the custodian agreed, asked in person or by phone. It belongs to
 *   the person and the number, so changing either unticks it here, and the
 *   server clears it too unless it is given again in the same save;
 * - a confirmed number - a one-time code texted to it and read back by the
 *   custodian. A typed number may be a typo, and a typo is a stranger.
 *
 * Confirming does not save anything: the record's own save puts the
 * confirmed number on the child, and only for whoever confirmed it. */

// The server's reading of a typed number (accounts/phone.py), used here only
// to tell whether the number on screen is the one already confirmed.
function asE164(raw) {
  let n = String(raw || '').replace(/[\s\-().]/g, '');
  if (n.startsWith('+63')) n = n.slice(3);
  else if (n.startsWith('63')) n = n.slice(2);
  else if (n.startsWith('0')) n = n.slice(1);
  return /^9\d{9}$/.test(n) ? `+63${n}` : '';
}

export default function CustodianFields({ form, setForm, fieldError, readOnly = false }) {
  const [sentTo, setSentTo] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  const number = asE164(form.custodian_contact);
  const confirmed = !!number && (
    (form.custodian_contact_verified && number === form._origContact)
    || number === form._confirmedNumber);
  const unchangedPerson = number === (form._origContact || '')
    && (form.custodian_name || '').trim() === (form._origCustodian || '').trim();
  const consentOnFile = form.custodian_sms_consent && form._origConsent && unchangedPerson;

  /* Consent belongs to the person and the number it was given for: change
   * either and it is off; put both back exactly and it is on again. It used
   * to go off for good on any keystroke, so a letter typed and deleted in the
   * name, then saved, withdrew the custodian's consent on the record. */
  const change = (patch) => {
    const next = { ...form, ...patch };
    const same = !!form._consentFor
      && consentKey(next.custodian_name, asE164(next.custodian_contact)) === form._consentFor;
    setForm({ ...next, custodian_sms_consent: same });
  };

  const sendCode = async () => {
    setError(''); setNote(''); setBusy(true);
    try {
      const { data } = await api.post('/custodian-contact/code/', { number: form.custodian_contact });
      setSentTo(data.number);
      setCode('');
      setNote(data.detail);
    } catch (err) {
      setError(firstError(err.response?.data, 'The code could not be sent. Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const confirmCode = async () => {
    setError(''); setBusy(true);
    try {
      const { data } = await api.put('/custodian-contact/code/', { number: sentTo, code });
      setForm({ ...form, _confirmedNumber: data.number });
      setSentTo('');
      setNote(data.detail);
    } catch (err) {
      setError(firstError(err.response?.data, 'That code could not be checked. Try again.'));
    } finally {
      setBusy(false);
    }
  };

  let status;
  if (!form.custodian_contact) status = 'No contact number - the custodian gets no appointment texts.';
  else if (!number) status = 'Enter a Philippine mobile number, starting 09.';
  else if (!form.custodian_sms_consent) status = 'Texts off until the custodian agrees to them.';
  else if (!confirmed) status = 'Texts off until the number is confirmed with a code.';
  else status = 'Appointment texts on: booked, a reminder the day before, moved, cancelled, missed.';
  const on = !!number && form.custodian_sms_consent && confirmed;

  if (readOnly) {
    return (
      <div className="racco-case-grid" style={{ marginBottom: 12 }}>
        <FormField label="Custodian" hint="Recorded by the social worker or the ISA.">
          <Input value={form.custodian_name || '—'} disabled onChange={() => {}} />
        </FormField>
        <FormField label="Contact Number" hint={form.custodian_texts?.status}>
          <Input value={form.custodian_contact || '—'} disabled onChange={() => {}} />
        </FormField>
      </div>
    );
  }

  return (
    <div style={{ marginBottom: 14 }}>
      <div className="racco-case-grid">
        <FormField label="Custodian" required error={fieldError('custodian_name')}
          hint="Who the child lives with now - a name and relationship, or the facility.">
          <Input value={form.custodian_name || ''} maxLength={150}
            placeholder="e.g. Rosa Dela Cruz (foster parent)"
            onChange={(e) => change({ custodian_name: e.target.value })} />
        </FormField>
        <FormField label="Contact Number" error={fieldError('custodian_contact')}
          hint="The custodian's mobile, for appointment texts. Optional.">
          <Input value={form.custodian_contact || ''} maxLength={20} inputMode="tel"
            placeholder="0917 123 4567"
            trailing={confirmed ? <Badge tone="success" size="sm" dot>Confirmed</Badge> : null}
            onChange={(e) => { setSentTo(''); setNote(''); setError(''); change({ custodian_contact: e.target.value }); }} />
        </FormField>
      </div>

      <div style={{ marginTop: 10, padding: '11px 13px', borderRadius: 'var(--radius-md)', background: on ? 'var(--success-50)' : 'var(--ink-50)', border: `1px solid ${on ? 'var(--success-100, var(--border))' : 'var(--border)'}`, display: 'flex', flexDirection: 'column', gap: 9 }}>
        <label style={{ display: 'flex', gap: 9, alignItems: 'flex-start', fontSize: 12.5, color: number ? 'var(--text-strong)' : 'var(--text-muted)', cursor: number ? 'pointer' : 'not-allowed' }}>
          <input type="checkbox" checked={!!form.custodian_sms_consent} disabled={!number}
            onChange={(e) => setForm({
              ...form, custodian_sms_consent: e.target.checked,
              _consentFor: e.target.checked ? consentKey(form.custodian_name, number) : null,
            })}
            style={{ marginTop: 2, accentColor: 'var(--blue-600)' }} />
          <span>
            <strong>The custodian agreed to receive text reminders</strong> about appointments at
            this number - the date, time and place only, nothing about the case.
            {consentOnFile && form.custodian_sms_consent_by_name && (
              <span style={{ display: 'block', color: 'var(--text-muted)', marginTop: 2 }}>
                Recorded by {form.custodian_sms_consent_by_name}
                {form.custodian_sms_consent_at ? ` · ${shortDate(form.custodian_sms_consent_at)}` : ''}
              </span>
            )}
          </span>
        </label>

        {number && !confirmed && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {sentTo === number ? (
              <>
                <div style={{ width: 150 }}>
                  <Input size="sm" value={code} maxLength={8} inputMode="numeric"
                    placeholder="6-digit code" aria-label="Code the custodian read back"
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
                </div>
                <Button type="button" size="sm" variant="primary" disabled={busy || code.length < 4}
                  onClick={confirmCode} iconLeft={<Icon name="check" size={14} />}>Confirm</Button>
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={sendCode}>Send again</Button>
              </>
            ) : (
              <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={sendCode}
                iconLeft={<Icon name="message-square" size={14} />}>
                {busy ? 'Sending…' : 'Send a code to confirm this number'}
              </Button>
            )}
          </div>
        )}
        {note && <span style={{ fontSize: 12, color: 'var(--text-body)' }}>{note}</span>}
        {error && <span role="alert" style={{ fontSize: 12, color: 'var(--red-700)', fontWeight: 600 }}>{error}</span>}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 700, color: on ? 'var(--success-700)' : 'var(--text-muted)' }}>
          <Icon name={on ? 'message-square' : 'message-square-off'} size={14} /> {status}
        </span>
      </div>
    </div>
  );
}
