import { useState } from 'react';
import { Alert, Badge, Button, Card, EmptyState, Icon } from '../../ui';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import { startCaseStudy } from '../../api/caseStudy';
import { exactDate } from '../../utils/time';
import { NOT_AN_ADOPTION_NOTE, isAdoptionRecord, sentence } from './model';
import Editor from './Editor';

/* The Case study tab on a child's record, in the shape the reader is allowed.
 * The server decides what each role is given (backend case_study/serializers.py);
 * this chooses the screen to draw it with:
 *
 *   the record's social worker   starts and writes the whole case study
 *   a psychologist               no tab at all: the server answers 404 (owner, 23 Oct 2026)
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

/* ----------------------------- the social worker ----------------------------- */
function Start({ child, cs }) {
  const confirm = useConfirm();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { study } = cs;
  const adoption = isAdoptionRecord(child);

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
        description={adoption
          ? "The Social Case Study Report (SCSR) is written about the child, and for an adoption also about the prospective adoptive parents and the placement. It covers why the child was referred, the child's background and development, the birth family, and the surrender or abandonment. For a regular, IP or foster adoption it is prepared after the Pre-Adoption Placement Authority (PAPA) is issued and the supervised trial custody is done; for a relative, step-parent or adult adoption, before the petition is filed."
          : `The case study is the Social Case Study Report (SCSR) of a child in care. It covers why the child was referred, the child's background and development, the birth family, and the surrender or abandonment. ${NOT_AN_ADOPTION_NOTE}`}
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
  if (!study.exists) return <Start child={child} cs={cs} />;
  return <Editor child={child} cs={cs} print={print} />;
}
