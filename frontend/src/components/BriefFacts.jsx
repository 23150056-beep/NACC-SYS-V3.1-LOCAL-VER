import { Badge, Icon, Note, Skeleton } from '../ui';
import { exactDate } from '../utils/time';

// The facts above a pre-session brief (GET /assistant/brief/child/:id/facts/).
// Counted from the record by plain queries, so they are here when the
// assistant is off, hosted or still drafting. The API sends no question or
// answer from the child's self-reports - only how many wait - and this never
// asks for one.

const SEVERITY_COLOUR = {
  danger: 'var(--red-600)',
  warning: 'var(--amber-500)',
  info: 'var(--text-muted)',
};

const ago = (n) => (n === 0 ? 'Today' : n === 1 ? '1 day ago' : `${n} days ago`);

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

export default function BriefFacts({ facts, failed }) {
  return (
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
  );
}
