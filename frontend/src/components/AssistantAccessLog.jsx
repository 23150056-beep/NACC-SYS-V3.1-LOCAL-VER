import { useEffect, useState } from 'react';
import {
  Alert, Badge, Card, Icon, TD, TH, THEAD_ROW, TR, roleLabel,
} from '../ui';
import { getChildAssistantLog } from '../api/assistant';
import { exactDate } from '../utils/time';

const KIND_LABELS = {
  brief: 'Pre-session brief',
  case_brief: 'Case brief',
  report_summary: 'Report summary',
  referral_summary: 'Referral summary',
  survey_check: 'Self-report check',
};

// [label, tone]
const STATUS = {
  failed: ['Did not complete', 'danger'],
  pending: ['Drafted', 'neutral'],
  accepted: ['Accepted', 'success'],
  edited: ['Edited, then used', 'success'],
  discarded: ['Discarded', 'neutral'],
  read: ['Read', 'neutral'],
  partly_read: ['Partly read', 'warning'],
};

const muted = { color: 'var(--text-muted)' };

/* Every time the assistant's model was given this child's record: when, who,
 * what and how it ended. The ISA's tab on the child's page - never the text of
 * what was drafted. `active` reloads it each time the tab is opened, so a
 * brief drafted a moment ago is there. Every hook lives here, so none lands
 * below the page's early returns. */
export default function AssistantAccessLog({ childId, active }) {
  const [log, setLog] = useState(null); // null while loading, 'error', or the response

  // Another child's page re-uses this component. The last child's rows go at
  // once, so they are never shown under this one while the new log loads.
  useEffect(() => { setLog(null); }, [childId]);

  useEffect(() => {
    if (!active) return undefined;
    let live = true;
    getChildAssistantLog(childId)
      .then((data) => { if (live) setLog(data); })
      .catch(() => { if (live) setLog('error'); });
    return () => { live = false; };
  }, [childId, active]);

  const who = (e) => {
    if (e.by) {
      return (
        <>
          {e.by.name}
          {e.by.role && <span style={muted}>{` · ${roleLabel(e.by.role)}`}</span>}
        </>
      );
    }
    return e.kind === 'survey_check' ? 'Automatic, when the survey was submitted' : '—';
  };

  const status = (e) => {
    const [label, tone] = STATUS[e.status] || [e.status, 'neutral'];
    return <Badge size="sm" tone={tone}>{label}</Badge>;
  };

  return (
    <Card eyebrow="Assistant" title="Who had the assistant read this record" padding="16px">
      {log === 'error' ? (
        <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>
          The assistant log could not load. Refresh to try again.
        </Alert>
      ) : !log ? (
        <p style={{ margin: 0, fontSize: 13, ...muted }}>Loading…</p>
      ) : log.entries.length === 0 ? (
        <p style={{ margin: 0, fontSize: 13, ...muted }}>
          The assistant has not read anything of this child&apos;s yet.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={THEAD_ROW}>
                <th scope="col" style={TH}>When</th>
                <th scope="col" style={TH}>Who</th>
                <th scope="col" style={TH}>What</th>
                <th scope="col" style={TH}>Status</th>
              </tr>
            </thead>
            <tbody>
              {log.entries.map((e) => (
                <tr key={e.key} style={TR}>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>{exactDate(e.at)}</td>
                  <td style={TD}>{who(e)}</td>
                  <td style={TD}>
                    {KIND_LABELS[e.kind] || e.kind}
                    {(e.document || e.document_deleted || e.kind === 'survey_check') && (
                      <div style={{ fontSize: 11.5, ...muted }}>
                        {[
                          e.document_deleted ? 'Document since deleted' : e.document,
                          e.kind === 'survey_check'
                            ? `${e.reads} answer check${e.reads === 1 ? '' : 's'}` : null,
                        ].filter(Boolean).join(' · ')}
                      </div>
                    )}
                  </td>
                  <td style={TD}>{status(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {log.total > log.entries.length && (
            <p style={{ margin: '8px 0 0', fontSize: 12, ...muted }}>
              Showing the latest {log.entries.length} of {log.total}.
            </p>
          )}
        </div>
      )}
      <p style={{ margin: '12px 0 0', fontSize: 12, lineHeight: 1.5, ...muted }}>
        Every time the assistant&apos;s model was given this child&apos;s notes, documents or
        survey answers through the app: pre-session briefs, case briefs, summaries and the
        self-report check.
        Developer commands such as ai_eval are not listed. Chatbot questions are not listed: the chatbot&apos;s model only picks
        which lookup to run and never reads a record. Remark polishing is not listed: it reads
        only the words being typed, which are not tied to a child. A summary of a document
        that was deleted before this log existed cannot be traced and is not listed.
      </p>
    </Card>
  );
}
