import { useState } from 'react';
import { Alert, Badge, Button, Card, EmptyState, Icon } from '../../ui';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import { startCaseStudy } from '../../api/caseStudy';
import { exactDate, shortDate } from '../../utils/time';
import { blocksFor, recordNotes, sentence } from './model';
import Editor from './Editor';
import PartOne from './PartOne';
import ReadOnlyValue from './ReadOnlyValue';

/* The Case study tab on a child's record, in the shape the reader is allowed.
 * The server decides what each role is given (backend case_study/serializers.py);
 * this chooses the screen to draw it with:
 *
 *   the record's social worker   starts and writes the whole case study
 *   the assigned psychologist    reads block A, drafts included, and never writes
 *   the ISA                      a status card, with no text anywhere in it
 */

const SMALL = { fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.55, margin: 0 };

function StatusRow({ label, children }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--ink-50)', alignItems: 'baseline' }}>
      <dt style={{ width: 170, flex: 'none', fontWeight: 700, fontSize: 12.5, color: 'var(--text-muted)' }}>{label}</dt>
      <dd style={{ margin: 0, flex: 1, minWidth: 0, fontWeight: 600, fontSize: 13.5, color: 'var(--text-strong)' }}>{children}</dd>
    </div>
  );
}

/* ----------------------------- the ISA ----------------------------- */
function StatusCard({ study }) {
  if (!study.exists) {
    return (
      <Card title="Case study" padding="20px">
        <p style={SMALL}>No case study has been started for this child. The social worker who holds the record starts it.</p>
      </Card>
    );
  }
  const stranded = !study.holder_active;
  return (
    <Card title="Case study" padding="6px 15px 14px"
      actions={<Badge tone={study.status === 'final' ? 'success' : 'amber'} size="sm" dot>{study.status === 'final' ? 'Final' : 'Draft'}</Badge>}>
      <dl style={{ margin: 0 }}>
        <StatusRow label="Held by">
          {study.holder_name
            ? <>{study.holder_name}{!study.holder_active && <span style={{ color: 'var(--red-700)' }}> (inactive)</span>}</>
            : 'No social worker holds this record yet'}
        </StatusRow>
        <StatusRow label="Last edited">{study.updated_at ? exactDate(study.updated_at) : '—'}</StatusRow>
        <StatusRow label="Last finalized">{study.last_finalized_at ? exactDate(study.last_finalized_at) : 'Not finalized yet'}</StatusRow>
        <StatusRow label="Still to complete">
          {study.missing_count === 0
            ? 'Nothing: every section is complete'
            : `${study.missing_count} item${study.missing_count === 1 ? '' : 's'}`}
        </StatusRow>
      </dl>
      {stranded && (
        <Alert tone="warning" icon={<Icon name="user-x" size={18} />} style={{ marginTop: 12 }}>
          {study.holder_name
            ? 'The social worker who held this record is no longer active, so nobody can finish the case study. '
            : 'Nobody holds this record, so nobody can write the case study. '}
          The record can be transferred to another social worker from the record form (Records, then Edit, Social Worker); the case study moves with it.
        </Alert>
      )}
      <p style={{ ...SMALL, marginTop: 12 }}>
        The ISA sees the status of a case study, never its text. Items still to complete are the sections with nothing in them yet, and the date prepared if it is not set.
      </p>
    </Card>
  );
}

/* ----------------------------- the psychologist ----------------------------- */
function BlockAReader({ child, study }) {
  if (!study.exists) {
    return (
      <Card title="Case study" padding="20px">
        <p style={SMALL}>The social worker has not started a case study for this child yet.</p>
      </Card>
    );
  }
  const stored = new Map(study.sections.map((s) => [s.key, s]));
  const block = blocksFor(child, study).find((b) => b.block === 'A');
  const writer = child.social_worker_name || 'the social worker';
  return (
    <div className="racco-stack" style={{ gap: 12 }}>
      {study.status === 'final' ? (
        <Alert tone="success" icon={<Icon name="file-check" size={18} />}>
          <strong>Final since {study.last_finalized_at ? exactDate(study.last_finalized_at) : 'an earlier date'}.</strong>{' '}
          {writer} can reopen it to revise it.
        </Alert>
      ) : (
        <Alert tone="info" icon={<Icon name="pencil" size={18} />}>
          Draft &ndash; being written by {writer}. What is shown can still change.
        </Alert>
      )}
      <Aside>
        You can read the child&apos;s side of the case study (block A). The adoptive parents and the placement are not shared with psychologists, and only {writer} can change any of it.
      </Aside>
      <h2 style={{ margin: '4px 0 0', fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 800, color: 'var(--text-strong)' }}>A. The Child/Adoptee</h2>
      <PartOne facts={study.record_facts} />
      {(block?.entries || []).filter((entry) => stored.has(entry.key)).map((entry) => {
        const row = stored.get(entry.key);
        const notes = recordNotes(entry.key, study.record_facts, shortDate);
        return (
          <section key={entry.key} aria-label={`${entry.number}. ${entry.title}`}>
            <Card title={`${entry.number}. ${entry.title}`} padding="14px 15px">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {notes.length > 0 && (
                  <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 12px', fontSize: 13 }}>
                    {notes.map(([k, v]) => (
                      <div key={k} style={{ display: 'contents' }}>
                        <dt style={{ fontWeight: 700, color: 'var(--text-muted)' }}>{k}</dt>
                        <dd style={{ margin: 0 }}>{v}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                <ReadOnlyValue entry={entry} value={row?.value} notApplicable={!!row?.not_applicable} birthDate={study.record_facts.birth_date} />
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {row && row.version > 0
                    ? `Saved${row.updated_by_name ? ` by ${row.updated_by_name}` : ''}, ${exactDate(row.updated_at)}`
                    : 'Not written yet'}
                </div>
              </div>
            </Card>
          </section>
        );
      })}
    </div>
  );
}

// A quiet line, in the same voice as the page's other notes.
function Aside({ children }) {
  return (
    <div style={{ display: 'flex', gap: 10, padding: '10px 14px', background: 'var(--ink-50)', borderRadius: 'var(--radius-md)' }}>
      <Icon name="info" size={16} style={{ color: 'var(--text-muted)', marginTop: 2 }} />
      <p style={{ ...SMALL, fontSize: 12.5 }}>{children}</p>
    </div>
  );
}

/* ----------------------------- the social worker ----------------------------- */
function Start({ child, cs }) {
  const confirm = useConfirm();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { study } = cs;

  if (study.read_only) {
    return (
      <Card title="Case study" padding="20px">
        <Alert tone="warning" icon={<Icon name="lock" size={18} />}>{study.read_only_reason}</Alert>
      </Card>
    );
  }

  const start = async () => {
    if (!(await confirm({
      description: `This starts a draft case study for ${child.fullname}. It is saved box by box, so it can be written over several days.`,
      confirmLabel: 'Yes, start the case study',
    }))) return;
    setBusy(true);
    setError('');
    try {
      cs.setStudy(await startCaseStudy(child.id));
      toast.success('Case study started');
    } catch (err) {
      setError(sentence(err, 'Could not start the case study.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Case study" padding="0">
      <EmptyState
        icon={<Icon name="file-text" size={26} />}
        title="No case study yet"
        description="The Social Case Study Report (SCSR) is written about the child, the prospective adoptive parents and the placement. For a regular, IP or foster adoption it is prepared after the Pre-Adoption Placement Authority (PAPA) is issued and the supervised trial custody is done; for a relative, step-parent or adult adoption, before the petition is filed."
        action={(
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'center' }}>
            <Button variant="primary" disabled={busy} onClick={start} iconLeft={<Icon name="file-plus" size={17} />}>
              {busy ? 'Starting…' : 'Start the case study'}
            </Button>
            {error && <p role="alert" style={{ margin: 0, color: 'var(--red-700)', fontSize: 13.5, fontWeight: 600 }}>{error}</p>}
          </div>
        )}
        style={{ maxWidth: 620, margin: '0 auto' }}
      />
    </Card>
  );
}

export default function CaseStudyTab({ child, cs, print }) {
  const { user } = useAuth();
  const { phase, study } = cs;

  if (phase === 'loading' || phase === 'idle') {
    return <div style={{ color: 'var(--text-muted)', padding: 8 }}>Loading the case study…</div>;
  }
  if (phase === 'error' || !study) {
    return (
      <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>
        The case study could not be loaded.{' '}
        <Button size="sm" variant="secondary" onClick={() => cs.reload()}>Try again</Button>
      </Alert>
    );
  }

  const role = user?.role_name;
  if (role === 'Administrator') return <StatusCard study={study} />;
  if (role === 'Psychologist') return <BlockAReader child={child} study={study} />;
  if (!study.exists) return <Start child={child} cs={cs} />;
  return <Editor child={child} cs={cs} print={print} />;
}
