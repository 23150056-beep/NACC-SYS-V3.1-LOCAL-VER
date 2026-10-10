import { useEffect, useState } from 'react';
import {
  Alert, Badge, Button, Card, FormField, Icon, Input, Note, PAGE, PageHeader, Switch, TD, TH, THEAD_ROW, TR,
} from '../ui';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import {
  getAssistantSettings, saveAssistantSettings, getAssistantMetrics, checkAssistant,
  getUnansweredQuestions,
} from '../api/assistant';
import { getAgencyProfile, saveAgencyProfile } from '../api/agency';
import { testEmailDelivery } from '../api/email';
import { checkSmsGateway, testSmsDelivery } from '../api/sms';

const FEATURE_LABELS = {
  brief: 'Pre-session briefs',
  case_brief: 'Case briefs',
  doc_intelligence: 'Document summaries',
  remark_polish: 'Remark polishing',
  census_narrative: 'Census narrative',
  // Both ran unlabelled — the table printed the raw job type.
  chat: 'Chatbot',
  self_report: 'Self-report check',
};

// Why a question is on the unanswered list. Plain words: the reader is an
// administrator deciding what the assistant should learn next.
const WHY_LABELS = {
  declined: 'No tool for it',
  not_understood: "Didn't understand",
  empty: 'Found nothing',
  not_helpful: 'Marked not helpful',
};

// The agency's own details, in the order they print. `max` mirrors the model's
// lengths so the box stops where the server would refuse.
const AGENCY_FIELDS = [
  ['agency_name', 'Agency name', 200, 'Regional Alternative Child Care Office No. 1'],
  ['office_address', 'Office address', 500, 'Street, barangay, city or municipality, province'],
  ['contact_details', 'Contact details', 300, 'Telephone and email'],
  ['head_of_office_name', 'Head of Office', 150, 'Full name as it should be printed'],
  ['head_of_office_title', 'Head of Office title', 150, 'Regional Director'],
];
const NO_AGENCY = Object.fromEntries(AGENCY_FIELDS.map(([key]) => [key, '']));

const textarea = {
  width: '100%', resize: 'vertical', padding: '10px 13px', borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.5,
};

export default function Settings() {
  const toast = useToast();
  const confirm = useConfirm();
  // What the server holds, and what is being typed. The draft is its own state
  // so an unsaved edit never reads as the agency's details.
  const [agency, setAgency] = useState(null);   // null = loading, 'error' = failed
  const [agencyDraft, setAgencyDraft] = useState(NO_AGENCY);
  const [agencySaving, setAgencySaving] = useState(false);
  const [sync, setSync] = useState(true);
  const [cfg, setCfg] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [unanswered, setUnanswered] = useState(null);
  const [saving, setSaving] = useState(false);
  const [check, setCheck] = useState(null);   // { ok, detail }
  const [checking, setChecking] = useState(false);
  // Runtime URL/model edits stay local until "Save runtime settings" is
  // pressed. They never live on cfg, so an unrelated switch's save (which
  // sends the whole cfg as its payload) can never ship a half-typed value.
  const [draft, setDraft] = useState({ ollama_url: '', model_name: '' });
  const [mailTesting, setMailTesting] = useState(false);
  const [smsTesting, setSmsTesting] = useState(false);
  const [smsChecking, setSmsChecking] = useState(false);
  const [smsResult, setSmsResult] = useState(null);   // { ok, detail, provider, sender, recipient }
  const [mailResult, setMailResult] = useState(null);   // { ok, detail, sender, recipient }

  useEffect(() => {
    getAgencyProfile().then((data) => {
      setAgency(data);
      setAgencyDraft(Object.fromEntries(AGENCY_FIELDS.map(([key]) => [key, data[key] || ''])));
    }).catch(() => setAgency('error'));
    getAssistantSettings().then((data) => {
      setCfg(data);
      setDraft({ ollama_url: data.ollama_url, model_name: data.model_name });
    }).catch(() => setCfg('error'));
    getAssistantMetrics().then(setMetrics).catch(() => setMetrics(null));
    getUnansweredQuestions().then(setUnanswered).catch(() => setUnanswered(null));
  }, []);

  const save = async (patch) => {
    // Agency-wide: every account feels it the moment it saves.
    const ok = await confirm('enabled' in patch ? {
      description: patch.enabled
        ? 'This turns the assistant on for everyone in the agency.'
        : 'This turns the assistant off for everyone in the agency — briefs, summaries, remark polishing and the chat panel stop until it is turned on again.',
      confirmLabel: patch.enabled ? 'Yes, turn it on' : 'Yes, turn it off',
      tone: patch.enabled ? 'brand' : 'warning',
    } : {
      description: 'This changes the model runtime the assistant uses, for everyone in the agency.',
      confirmLabel: 'Yes, save the runtime settings',
      details: [['Runtime URL', patch.ollama_url], ['Model', patch.model_name]],
    });
    if (!ok) return null;
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

  const agencyChanged = agency && agency !== 'error'
    && AGENCY_FIELDS.some(([key]) => agencyDraft[key].trim() !== (agency[key] || ''));

  const saveAgency = async () => {
    // Printed on every report, so it is asked like the other agency-wide saves.
    const ok = await confirm({
      description: 'This changes the agency details printed on reports, for everyone in the agency.',
      confirmLabel: 'Yes, save the agency details',
      details: AGENCY_FIELDS
        .filter(([key]) => agencyDraft[key].trim() !== (agency[key] || ''))
        .map(([key, label]) => [label, agencyDraft[key].trim() || 'Blank']),
    });
    if (!ok) return;
    setAgencySaving(true);
    try {
      const saved = await saveAgencyProfile(agencyDraft);
      setAgency(saved);
      // Agree with what the server stored (it trims); a failure leaves the draft.
      setAgencyDraft(Object.fromEntries(AGENCY_FIELDS.map(([key]) => [key, saved[key] || ''])));
      toast.success('Agency details saved');
    } catch (err) {
      const data = err.response?.data;
      const first = data && typeof data === 'object' && Object.values(data)[0];
      toast.error((Array.isArray(first) ? first[0] : first) || 'Could not save the agency details.');
    } finally {
      setAgencySaving(false);
    }
  };

  return (
    <div style={{ ...PAGE, maxWidth: 780 }}>
      <PageHeader title="Settings" subtitle="Administrator only · agency-wide" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Card eyebrow="Agency" title="Configuration" padding="16px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {agency === 'error' && <Alert tone="warning">Could not load the agency details.</Alert>}
            {/* Real and saved: every printed report reads these. */}
            {agency && agency !== 'error' && (
              <>
                {AGENCY_FIELDS.map(([key, label, max, placeholder]) => (
                  <FormField key={key} label={label}
                             hint={key === 'head_of_office_name'
                               ? "Printed under 'Approved by' on the Social Case Study Report."
                               : null}>
                    {key === 'office_address' || key === 'contact_details' ? (
                      <textarea value={agencyDraft[key]} rows={2} maxLength={max} placeholder={placeholder}
                                disabled={agencySaving} style={textarea}
                                onChange={(e) => setAgencyDraft({ ...agencyDraft, [key]: e.target.value })} />
                    ) : (
                      <Input value={agencyDraft[key]} maxLength={max} placeholder={placeholder}
                             disabled={agencySaving}
                             onChange={(e) => setAgencyDraft({ ...agencyDraft, [key]: e.target.value })} />
                    )}
                  </FormField>
                ))}
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <Button variant="primary" disabled={agencySaving || !agencyChanged} onClick={saveAgency}>
                    {agencySaving ? 'Saving…' : 'Save agency details'}
                  </Button>
                </div>
              </>
            )}
            {/* Display-only. The national office's endpoint and the sync have
                never had a backend here. */}
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
        <Card eyebrow="Notifications" title="Email delivery" padding="16px">
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
                        if (!(await confirm({
                          description: 'This sends a real test email to your own address.',
                          confirmLabel: 'Yes, send it',
                        }))) return;
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
        <Card eyebrow="Notifications" title="Text messages" padding="16px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
              Staff who have verified a mobile number get a text for a new case
              assignment, a temporary password, and the next day&rsquo;s sessions.
              This sends one message to <strong>your own</strong> verified number
              and reports what the gateway replied. Verify yours first under
              your profile: the account menu, top right, then <strong>See your
              profile</strong> &rarr; Mobile number.
            </div>
            <Note icon="info">
              Check the key first. Gateways hand out only a handful of free
              credits to try with and there is no sandbox, so proving the key
              works should not cost one of them &mdash; and a wrong key and an
              unverified number fail in ways that look alike.
            </Note>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Button variant="secondary" disabled={smsTesting || smsChecking}
                      iconLeft={<Icon name="shield-check" size={16} />}
                      onClick={async () => {
                        setSmsChecking(true);
                        setSmsResult(null);
                        try {
                          setSmsResult(await checkSmsGateway());
                        } catch (err) {
                          setSmsResult({
                            ok: false,
                            detail: err.response?.data?.detail
                              || 'The gateway could not be checked.',
                          });
                        } finally {
                          setSmsChecking(false);
                        }
                      }}>
                {smsChecking ? 'Checking…' : 'Check the key'}
              </Button>
              <Button variant="secondary" disabled={smsTesting || smsChecking}
                      iconLeft={<Icon name="message-square" size={16} />}
                      onClick={async () => {
                        if (!(await confirm({
                          description: 'This sends a real text message to your own confirmed number, through the SMS provider.',
                          confirmLabel: 'Yes, send it',
                        }))) return;
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

        <Card eyebrow="Assistant" title="Local writing assistant" padding="16px">
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
          <Card eyebrow="Assistant" title={`Usage — last ${metrics.window_days} days`} padding="16px">
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={THEAD_ROW}>
                    <th scope="col" style={TH}>Feature</th><th scope="col" style={TH}>Runs</th>
                    <th scope="col" style={TH}>Errors</th><th scope="col" style={TH}>Avg</th>
                    <th scope="col" style={TH}>Kept</th><th scope="col" style={TH}>Edited</th>
                    <th scope="col" style={TH}>Discarded</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.features.map((f) => (
                    <tr key={f.job_type} style={TR}>
                      <td style={TD}>{FEATURE_LABELS[f.job_type] || f.job_type}</td>
                      <td style={TD}>{f.runs}</td>
                      <td style={TD}>{f.errors}</td>
                      <td style={TD}>{f.avg_latency_ms === null ? '—' : `${(f.avg_latency_ms / 1000).toFixed(1)}s`}</td>
                      <td style={TD}>{f.accepted}</td>
                      <td style={TD}>{f.edited}</td>
                      <td style={TD}>{f.discarded}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* What to teach the assistant next, from what people actually asked.
            Roles, never names: the point is what the agency needs, not who
            asked it. */}
        {unanswered && (() => {
          const b = unanswered.breakdown;
          // Every question asked lands in exactly one clause, so the sentence
          // adds up to the total a careful reader will check it against.
          const other = b.greeting + b.action + b.failed;
          return (
            <Card eyebrow="Assistant"
                  title={`Questions it couldn’t answer — last ${unanswered.window_days} days`}
                  padding="16px">
              <p style={{ margin: '0 0 12px', fontSize: 13, lineHeight: 1.5, color: 'var(--text-muted)' }}>
                {b.total} {b.total === 1 ? 'question' : 'questions'} asked: {b.answered} answered,{' '}
                {b.empty} found nothing, {b.declined} had no tool, {b.not_understood} not
                understood, {other} greetings, requests to change something or outages
                {b.unmeasured > 0 && `, and ${b.unmeasured} looked something up before answer sizes were recorded — whether ${b.unmeasured === 1 ? 'it' : 'they'} found anything is unknown`}.
                {' '}{b.helpful} marked helpful, {b.not_helpful} not helpful.
              </p>
              {unanswered.questions.length === 0 ? (
                <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)' }}>
                  Nothing yet. Questions the assistant has no tool for, can&rsquo;t follow,
                  answers with nothing, or someone marks not helpful will collect here.
                </p>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={THEAD_ROW}>
                        <th scope="col" style={TH}>Question</th><th scope="col" style={TH}>Why</th>
                        <th scope="col" style={TH}>Times</th><th scope="col" style={TH}>Last asked</th>
                        <th scope="col" style={TH}>Asked by</th>
                      </tr>
                    </thead>
                    <tbody>
                      {unanswered.questions.map((q) => (
                        <tr key={`${q.why}:${q.question}`} style={TR}>
                          <td style={{ ...TD, wordBreak: 'break-word' }}>{q.question}</td>
                          <td style={TD}>{WHY_LABELS[q.why] || q.why}</td>
                          <td style={TD}>{q.times}</td>
                          <td style={TD}>{q.last_asked}</td>
                          <td style={TD}>{q.roles.join(', ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {unanswered.distinct > unanswered.questions.length && (
                    <p style={{ margin: '8px 0 0', fontSize: 12, color: 'var(--text-muted)' }}>
                      Showing the {unanswered.questions.length} most asked of {unanswered.distinct}.
                    </p>
                  )}
                </div>
              )}
            </Card>
          );
        })()}
      </div>
    </div>
  );
}
