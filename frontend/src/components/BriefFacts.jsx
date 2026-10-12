import { Badge, Button, Icon, Note, Skeleton } from '../ui';
import { exactDate, shortDate } from '../utils/time';

// The facts above a pre-session brief (GET /assistant/brief/child/:id/facts/).
// Counted from the record by plain queries, so they are here when the
// assistant is off, hosted or still drafting. The API sends no question or
// answer from the child's self-reports - only how many wait - and this never
// asks for one.
//
// A social worker's and the ISA's is the "case" kind: the same rows beneath a
// block of where the case stands on the paperwork (referral, psychologist,
// consent, custodian texts, survey). Each is a sentence, not a status code,
// and none carries the custodian's name or number or a word of a summary
// nobody has confirmed - the API does not send them, and this does not ask.
//
// The record's own social worker also gets "What to do next" under the facts:
// an ordered list the SERVER works out by fixed rules from the same facts
// (assistant/brief_facts.py next_steps), each a plain sentence and, where the
// thing is done on a screen, a button that goes there. The ISA and a
// psychologist are sent no list, so none is drawn for them. Nothing in it is
// written by the assistant.

const SEVERITY_COLOUR = {
  danger: 'var(--red-600)',
  warning: 'var(--amber-500)',
  info: 'var(--text-muted)',
};

const ago = (n) => (n === 0 ? 'Today' : n === 1 ? '1 day ago' : `${n} days ago`);

const CONSENT = { signed: 'Signed', pending: 'Pending', declined: 'Declined' };
const SURVEY = {
  answered: (d) => `Answered ${d}`,
  sent: (d) => `Sent ${d}, no answer yet`,
  expired: (d) => `Sent ${d}, the link has expired`,
};

// "asked M. Bulan 3 days ago" - the psychologist row, in its four states.
function psychologistLine(p) {
  switch (p.state) {
    case 'assigned': return `Assigned to ${p.name}`;
    case 'asked': return `Asked ${p.name} ${p.days_ago === 0 ? 'today' : ago(p.days_ago)}`;
    case 'declined': return `Declined by ${p.name}${p.reason ? `: ${p.reason}` : ''}`;
    default: return 'None yet';
  }
}

function CaseRows({ f }) {
  const referral = f.case_referral;
  const consent = f.consent;
  return (
    <>
      <Row label="Case referral">
        {referral ? (
          <>
            {referral.count} on file, latest {shortDate(referral.latest_uploaded_on)}
            <div style={{ marginTop: 2, fontWeight: 500, color: referral.summary ? 'var(--text-body)' : 'var(--text-muted)', whiteSpace: 'pre-wrap' }}>
              {referral.summary || 'Not summarised yet'}
            </div>
          </>
        ) : 'None on file'}
      </Row>
      <Row label="Psychologist">{psychologistLine(f.psychologist || {})}</Row>
      <Row label="Consent">
        {consent ? `${CONSENT[consent.status] || consent.status}, ${shortDate(consent.date)}` : 'None on file'}
      </Row>
      {/* Not asked for every case type: no row where there is no custodian. */}
      {f.custodian_texts && <Row label="Custodian texts">{f.custodian_texts}</Row>}
      <Row label="Survey">
        {f.survey ? (SURVEY[f.survey.state]?.(shortDate(f.survey.date)) ?? `Sent ${shortDate(f.survey.date)}`) : 'None sent'}
      </Row>
    </>
  );
}

function Row({ label, children }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', padding: '6px 0', borderBottom: '1px solid var(--ink-50)' }}>
      <dt style={{ width: 140, flex: 'none', fontWeight: 700, fontSize: 12, color: 'var(--text-muted)' }}>{label}</dt>
      <dd style={{ margin: 0, flex: '1 1 220px', minWidth: 0, fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>{children}</dd>
    </div>
  );
}

function FactRows({ f }) {
  const next = f.next_session;
  const last = f.last_session;
  const plan = f.treatment_plan;
  const waiting = f.unreviewed_self_reports;
  return (
    <dl style={{ margin: 0 }}>
      {f.kind === 'case' && <CaseRows f={f} />}
      <Row label="Next session">
        {next ? `${exactDate(next.start)} · ${next.purpose}` : 'None booked'}
      </Row>
      <Row label="Last session">
        {last ? `${ago(last.days_ago)} · ${exactDate(last.start)} · ${last.purpose}` : 'None held yet'}
      </Row>
      <Row label="Open problems">
        {f.open_problems.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {f.open_problems.map((p, i) => (
              <li key={`${p.identified_on}-${i}`}>
                {p.description}
                {p.category && <> <Badge tone="neutral" size="sm">{p.category}</Badge></>}
              </li>
            ))}
          </ul>
        ) : 'None open'}
      </Row>
      <Row label="Treatment plan">
        {plan ? (
          <>
            <span style={{ whiteSpace: 'pre-wrap' }}>{plan.objectives}</span>
            {plan.review_date && (
              <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}> · Review {plan.review_date}</span>
            )}
          </>
        ) : 'No active plan'}
      </Row>
      <Row label="Self-reports to read">
        {waiting > 0 ? (
          <>
            <Badge tone="danger" size="sm">{waiting} answer{waiting === 1 ? '' : 's'} awaiting review</Badge>
            {' '}
            <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>Their words are on the Overview tab.</span>
          </>
        ) : 'None waiting'}
      </Row>
      <Row label="Care gaps">
        {f.care_gaps.length > 0 ? (
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
            {f.care_gaps.map((g) => (
              <li key={g.type} style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                <Icon name="alert-triangle" size={13}
                      style={{ color: SEVERITY_COLOUR[g.severity] || SEVERITY_COLOUR.info, flex: 'none', marginTop: 2 }} />
                <span>{g.message}</span>
              </li>
            ))}
          </ul>
        ) : 'None'}
      </Row>
    </dl>
  );
}

// One line per step: the sentence, and "Open" where it can be done on a screen.
// `onGo` is the page's: it closes the modal first, or it would stay over the
// screen the button just opened.
function NextSteps({ steps, onGo }) {
  return (
    <section aria-label="What to do next" style={{ marginTop: 18 }}>
      <div className="racco-eyebrow">What to do next</div>
      <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '2px 0 8px' }}>
        Worked out from the record by fixed rules - not drafted by the assistant.
      </p>
      {steps.length === 0 ? (
        <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>
          Nothing waiting on you for this child.
        </p>
      ) : (
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {steps.map((step) => (
            <li key={step.key} style={{ padding: '6px 0', borderBottom: '1px solid var(--ink-50)' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '6px 12px' }}>
                <span style={{ flex: '1 1 240px', minWidth: 0, fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>
                  {step.text}
                </span>
                {step.link && (
                  <Button size="sm" variant="secondary" aria-label={`Open: ${step.text}`}
                          onClick={() => onGo(step.link)}>
                    Open
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function BriefFacts({ facts, failed, onGo }) {
  return (
    <>
      <section aria-label="Facts from the record">
        <div className="racco-eyebrow">From the record</div>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '2px 0 8px' }}>
          Counted from the case file just now - not drafted by the assistant.
        </p>
        {failed ? (
          <Note tone="warning" icon="alert-triangle">
            The facts could not be loaded. Close this and try again.
          </Note>
        ) : !facts ? (
          <div role="status" aria-label="Loading the facts">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} style={{ padding: '7px 0' }}><Skeleton height={12} /></div>
            ))}
          </div>
        ) : <FactRows f={facts} />}
      </section>
      {Array.isArray(facts?.next_steps) && <NextSteps steps={facts.next_steps} onGo={onGo} />}
    </>
  );
}
