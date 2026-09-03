import { useEffect, useState } from 'react';
import { Card, Badge, Input, FormField, Switch, Button, Alert, Icon, PAGE } from '../ui';
import { useToast } from '../context/ToastContext';
import { getAssistantSettings, saveAssistantSettings, getAssistantMetrics, checkAssistant } from '../api/assistant';
import { testEmailDelivery } from '../api/email';
import { testSmsDelivery } from '../api/sms';

const FEATURE_LABELS = {
  brief: 'Pre-session briefs',
  doc_intelligence: 'Document summaries',
  remark_polish: 'Remark polishing',
  census_narrative: 'Census narrative',
};

const th = { textAlign: 'left', padding: '8px 10px', fontSize: 12, color: 'var(--text-muted)', fontWeight: 600 };
const td = { padding: '8px 10px', fontSize: 13, color: 'var(--text-body)' };

export default function Settings() {
  const toast = useToast();
  const [agency] = useState('St. Joseph Orphanage');
  const [sync, setSync] = useState(true);
  const [cfg, setCfg] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [saving, setSaving] = useState(false);
  const [check, setCheck] = useState(null);   // { ok, detail }
  const [checking, setChecking] = useState(false);
  // Runtime URL/model edits stay local until "Save runtime settings" is
  // pressed. They never live on cfg, so an unrelated switch's save (which
  // sends the whole cfg as its payload) can never ship a half-typed value.
  const [draft, setDraft] = useState({ ollama_url: '', model_name: '' });
  const [mailTesting, setMailTesting] = useState(false);
  const [smsTesting, setSmsTesting] = useState(false);
  const [smsResult, setSmsResult] = useState(null);   // { ok, detail, provider, sender, recipient }
  const [mailResult, setMailResult] = useState(null);   // { ok, detail, sender, recipient }

  useEffect(() => {
    getAssistantSettings().then((data) => {
      setCfg(data);
      setDraft({ ollama_url: data.ollama_url, model_name: data.model_name });
    }).catch(() => setCfg('error'));
    getAssistantMetrics().then(setMetrics).catch(() => setMetrics(null));
  }, []);

  const save = async (patch) => {
    const prev = cfg;
    const next = { ...cfg, ...patch };
    setCfg(next);
    setSaving(true);
    try {
      const saved = await saveAssistantSettings(next);
      setCfg(saved);
      toast.success('Assistant settings saved');
      return saved;
    } catch {
      setCfg(prev);   // the screen must not keep showing a value that never saved
      toast.error('Could not save the assistant settings.');
      return null;
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ ...PAGE, maxWidth: 760 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Card eyebrow="Agency" title="Configuration" padding="22px">
          {/* Display-only. These have never had a backend. */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <FormField label="RCPC" hint="Set by the national office — not editable here yet."><Input value={agency} disabled /></FormField>
            <FormField label="NACC API Endpoint" hint="Managed by the national office.">
              <Input value="https://api.nacc.gov.ph/v1/sync" disabled trailing={<Badge tone="success" size="sm">PROD</Badge>} />
            </FormField>
            <Switch checked={sync} onChange={setSync} disabled label="Auto-sync signed reports to NACC" />
          </div>
        </Card>

        {/* Email is the one feature whose failures are invisible: the send
            happens on a background thread after the response, so a rejected
            message looks exactly like a delivered one. Diagnosing it meant
            reading server logs, which Render's free plan does not give an
            administrator. This asks Brevo and prints the answer. */}
        <Card eyebrow="Notifications" title="Email delivery" padding="22px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
              Temporary passwords are emailed to the person whose account it is.
              This sends a test message to your own address and reports exactly
              what the mail service replied.
            </div>
            <div>
              <Button variant="secondary" disabled={mailTesting}
                      iconLeft={<Icon name="mail" size={16} />}
                      onClick={async () => {
                        setMailTesting(true);
                        setMailResult(null);
                        try {
                          setMailResult(await testEmailDelivery());
                        } catch (err) {
                          setMailResult({
                            ok: false,
                            detail: err.response?.data?.detail
                              || 'The test could not be run.',
                          });
                        } finally {
                          setMailTesting(false);
                        }
                      }}>
                {mailTesting ? 'Sending…' : 'Send a test email'}
              </Button>
            </div>
            {mailResult && (
              <Alert tone={mailResult.ok ? 'success' : 'danger'}
                     icon={<Icon name={mailResult.ok ? 'mail-check' : 'mail-warning'} size={18} />}>
                <div style={{ lineHeight: 1.6 }}>{mailResult.detail}</div>
                {mailResult.sender && (
                  <div style={{ fontSize: 12, marginTop: 6, color: 'var(--text-muted)' }}>
                    Sending from <strong>{mailResult.sender}</strong>
                    {mailResult.recipient ? <> to <strong>{mailResult.recipient}</strong></> : null}
                  </div>
                )}
              </Alert>
            )}
          </div>
        </Card>

        {/* The same reasoning as the email card beside it, and the same
            failure it guards against: every notification send happens on a
            background thread, so a gateway refusing a message looks exactly
            like one delivering it. This asks and prints the answer. */}
        <Card eyebrow="Notifications" title="Text messages" padding="22px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
              Staff who have verified a mobile number get a text for a new case
              assignment, a temporary password, and the next day&rsquo;s sessions.
              This sends one message to <strong>your own</strong> verified number
              and reports what the gateway replied.
            </div>
            <div>
              <Button variant="secondary" disabled={smsTesting}
                      iconLeft={<Icon name="message-square" size={16} />}
                      onClick={async () => {
                        setSmsTesting(true);
                        setSmsResult(null);
                        try {
                          setSmsResult(await testSmsDelivery());
                        } catch (err) {
                          setSmsResult({
                            ok: false,
                            detail: err.response?.data?.detail
                              || 'The test could not be run.',
                          });
                        } finally {
                          setSmsTesting(false);
                        }
                      }}>
                {smsTesting ? 'Sending…' : 'Send a test text'}
              </Button>
            </div>
            {smsResult && (
              <Alert tone={smsResult.ok ? 'success' : 'danger'}
                     icon={<Icon name={smsResult.ok ? 'message-square' : 'alert-triangle'} size={18} />}>
                <div style={{ lineHeight: 1.6 }}>{smsResult.detail}</div>
                {smsResult.provider && (
                  <div style={{ fontSize: 12, marginTop: 6, color: 'var(--text-muted)' }}>
                    Gateway <strong>{smsResult.provider}</strong>
                    {smsResult.sender ? <> · sender <strong>{smsResult.sender}</strong></> : null}
                    {smsResult.recipient ? <> · to <strong>{smsResult.recipient}</strong></> : null}
                  </div>
                )}
              </Alert>
            )}
          </div>
        </Card>

        <Card eyebrow="Assistant" title="Local writing assistant" padding="22px">
          {cfg === 'error' && <Alert tone="warning">Could not load the assistant settings.</Alert>}
          {cfg && cfg !== 'error' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <Alert tone="info">
                Drafts are produced by a model running on this machine. Case text
                is never sent to an outside service. Every draft is reviewed and
                approved by a person before it becomes clinical text.
              </Alert>
              {/* One switch, not one per feature. It is on by default, so the
                  assistant works as soon as the runtime is running; the switch
                  exists so a misbehaving feature can be stopped without waiting
                  for a code deploy. FEATURE_LABELS is still used below to name
                  the rows of the usage table. */}
              <Switch checked={cfg.enabled} disabled={saving}
                      onChange={(v) => save({ enabled: v })}
                      label="Assistant enabled" />
              <FormField label="Runtime URL" hint="The local model runtime. Loopback only.">
                <Input value={draft.ollama_url} disabled={saving}
                       onChange={(e) => setDraft({ ...draft, ollama_url: e.target.value })} />
              </FormField>
              <FormField label="Model" hint="Must already be pulled on this machine.">
                <Input value={draft.model_name} disabled={saving}
                       onChange={(e) => setDraft({ ...draft, model_name: e.target.value })} />
              </FormField>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <Button variant="ghost" disabled={checking}
                        onClick={async () => {
                          setChecking(true);
                          try { setCheck(await checkAssistant()); }
                          catch { setCheck({ ok: false, detail: 'The check could not be run.' }); }
                          finally { setChecking(false); }
                        }}>
                  {checking ? 'Checking…' : 'Test connection'}
                </Button>
                <Button variant="primary" disabled={saving}
                        onClick={async () => {
                          const saved = await save({
                            ollama_url: draft.ollama_url,
                            model_name: draft.model_name,
                          });
                          // Agree with what the server actually stored (it may
                          // normalize the value); leave the draft alone on failure.
                          if (saved) setDraft({ ollama_url: saved.ollama_url, model_name: saved.model_name });
                        }}>Save runtime settings</Button>
              </div>
              {check && (
                <Alert tone={check.ok ? 'success' : 'warning'}>{check.detail}</Alert>
              )}
            </div>
          )}
        </Card>

        {metrics && (
          <Card eyebrow="Assistant" title={`Usage — last ${metrics.window_days} days`} padding="22px">
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={th}>Feature</th><th style={th}>Runs</th>
                    <th style={th}>Errors</th><th style={th}>Avg</th>
                    <th style={th}>Kept</th><th style={th}>Edited</th>
                    <th style={th}>Discarded</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.features.map((f) => (
                    <tr key={f.job_type}>
                      <td style={td}>{FEATURE_LABELS[f.job_type] || f.job_type}</td>
                      <td style={td}>{f.runs}</td>
                      <td style={td}>{f.errors}</td>
                      <td style={td}>{f.avg_latency_ms === null ? '—' : `${(f.avg_latency_ms / 1000).toFixed(1)}s`}</td>
                      <td style={td}>{f.accepted}</td>
                      <td style={td}>{f.edited}</td>
                      <td style={td}>{f.discarded}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
