import { useState } from 'react';
import { Alert, Button, Card, Icon } from '../../ui';
import api from '../../api/client';
import { useConfirm, useNotice } from '../../context/ConfirmContext';
import { finalizeCaseStudy, reopenCaseStudy } from '../../api/caseStudy';
import { caseRef } from '../../utils/child';
import { exactDate, shortDate } from '../../utils/time';
import { sentence } from './model';

/* Final and Reopen for the social worker who holds the record (owner's
 * decisions, 8 Oct 2026: nobody in the system approves - the SW makes it final
 * and the Head of Office signs the printed copy).
 *
 *   MarkAsFinal   the button in the header, and why it is not available yet
 *   FinalBanner   "Final since ..." with Reopen, while the case study is final
 *   FinalsOnFile  every final that was made, each printable
 *
 * Every write asks first (useConfirm) and ends with a dialog saying what
 * happened and what comes next (useNotice). The server decides everything;
 * this only reports its sentences. */

const MUTED = { fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.5 };

// What the printed copy will say about the preparer, read just now rather than
// remembered: the license is the person's own and may have changed this week.
async function licenseLine() {
  try {
    const { data } = await api.get('/auth/me/profile/');
    const number = (data.license_number || '').trim();
    if (!number) {
      return 'Your profile has no PRC license number - the print will leave that line blank. Add it on your profile first if it should show.';
    }
    return `License No. ${number}${data.license_valid_until ? `, valid until ${shortDate(data.license_valid_until)}` : ''}`;
  } catch {
    return 'Your license number could not be checked just now. The print carries whatever your profile holds when you confirm.';
  }
}

/* ----------------------------- the header button ----------------------------- */
export function MarkAsFinal({ child, cs, pending }) {
  const confirm = useConfirm();
  const notice = useNotice();
  const { study } = cs;
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null); // { text, missing, stale }

  const missing = study.missing || [];
  // Named from the server's own list, which is the one the refusal would give:
  // the screen's list above is built from the record as it loaded.
  const why = pending > 0
    ? 'Save or discard your unsaved changes first.'
    : !study.can_finalize
      ? `Still to complete: ${missing.slice(0, 3).join('; ')}${missing.length > 3 ? `; and ${missing.length - 3} more, listed above` : ''}.`
      : 'Everything is complete. Making it final locks it; you can reopen it to revise.';
  const ready = study.can_finalize && pending === 0;

  const makeFinal = async () => {
    setProblem(null);
    const license = await licenseLine();
    if (!(await confirm({
      description: `This makes ${child.fullname}'s case study final.`,
      confirmLabel: 'Yes, make it final',
      details: [
        ['It locks', 'No box can be changed until you reopen it.'],
        ['Printed copy', 'It carries your name and license as they are today.'],
        ['Your license', license],
        ['Revising', 'You can reopen it to revise it. The final you printed stays on file.'],
        ['Signature', 'The Head of Office signs the printed copy.'],
      ],
    }))) return;
    setBusy(true);
    let out;
    try {
      out = await finalizeCaseStudy(child.id, study.updated_at);
    } catch (err) {
      const data = err.response?.data;
      if (err.response?.status === 400 && Array.isArray(data?.missing)) {
        setProblem({ text: data.detail, missing: data.missing });
        cs.reload();
      } else if (err.response?.status === 409) {
        setProblem({ text: sentence(err, 'The case study could not be made final.'), stale: true });
      } else {
        setProblem({ text: sentence(err, 'The case study could not be made final.') });
      }
      setBusy(false);
      return;
    }
    setBusy(false);
    cs.setStudy(out);
    await notice({
      title: 'Case study final',
      description: "Print it from this tab for the Head of Office's signature.",
      icon: 'file-check',
      details: [
        ['Child', child.fullname],
        ['Case number', caseRef(child.id)],
        ['Date prepared', shortDate(out.date_prepared)],
      ],
    });
  };

  const reload = async () => {
    setProblem(null);
    await cs.reload();
  };

  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Button variant="primary" disabled={!ready || busy} onClick={makeFinal}
          aria-describedby="cs-final-why" iconLeft={<Icon name="file-check" size={17} />}>
          {busy ? 'Making it final…' : 'Mark as final'}
        </Button>
        <span id="cs-final-why" style={{ ...MUTED, flex: 1, minWidth: 220 }}>{why}</span>
      </div>
      {problem && (
        <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>
          <div role="alert">
            <div style={{ fontWeight: 700 }}>{problem.text}</div>
            {problem.missing && problem.missing.length > 0 && (
              <ul style={{ margin: '6px 0 0', paddingLeft: 20 }}>
                {problem.missing.map((title) => <li key={title}>{title}</li>)}
              </ul>
            )}
            {problem.stale && (
              <div style={{ marginTop: 8 }}>
                <Button size="sm" variant="secondary" onClick={reload}>Reload</Button>
              </div>
            )}
          </div>
        </Alert>
      )}
    </div>
  );
}

/* ----------------------------- while it is final ----------------------------- */
export function FinalBanner({ child, cs, canReopen }) {
  const confirm = useConfirm();
  const notice = useNotice();
  const { study } = cs;
  const latest = (study.finals || [])[0];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const reopen = async () => {
    setError('');
    if (!(await confirm({
      description: `This reopens ${child.fullname}'s case study. It goes back to draft. The final you printed stays on file and can still be printed.`,
      confirmLabel: 'Yes, reopen it',
      tone: 'warning',
    }))) return;
    setBusy(true);
    let out;
    try {
      out = await reopenCaseStudy(child.id);
    } catch (err) {
      setError(sentence(err, 'The case study could not be reopened.'));
      if (err.response?.status === 409) cs.reload();
      setBusy(false);
      return;
    }
    setBusy(false);
    cs.setStudy(out);
    await notice({
      title: 'Case study reopened',
      description: 'It is a draft again, so its boxes can be changed. Mark it as final when it is complete: that adds a new final copy, and the earlier one stays on file.',
      icon: 'undo-2',
      details: [
        ['Child', child.fullname],
        ['Case number', caseRef(child.id)],
      ],
    });
  };

  return (
    <Alert tone="success" icon={<Icon name="file-check" size={18} />}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 240 }}>
          <div style={{ fontWeight: 800, color: 'var(--text-strong)' }}>
            Final since {latest ? exactDate(latest.finalized_at) : 'an earlier date'}
            {latest?.finalized_by_name ? `, by ${latest.finalized_by_name}` : ''}
          </div>
          <div style={MUTED}>
            It is locked. Print it for the Head of Office&apos;s signature, or reopen it to revise it.
          </div>
        </div>
        {canReopen && (
          <Button variant="secondary" disabled={busy} onClick={reopen} iconLeft={<Icon name="undo-2" size={16} />}>
            {busy ? 'Reopening…' : 'Reopen to revise'}
          </Button>
        )}
      </div>
      {error && <p role="alert" style={{ margin: '8px 0 0', color: 'var(--red-700)', fontSize: 13.5, fontWeight: 600 }}>{error}</p>}
    </Alert>
  );
}

/* ----------------------------- the copies kept ----------------------------- */
export function FinalsOnFile({ finals, print }) {
  if (!finals || finals.length === 0) return null;
  return (
    <Card title="Finals on file" padding="4px 15px 12px">
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {finals.map((f, i) => (
          <li key={f.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', padding: '9px 0', borderBottom: i < finals.length - 1 ? '1px solid var(--ink-50)' : 'none' }}>
            <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-strong)' }}>
              {exactDate(f.finalized_at)}{f.finalized_by_name ? ` - ${f.finalized_by_name}` : ''}
              {i === 0 && <span style={{ ...MUTED, fontWeight: 400 }}> (newest)</span>}
            </span>
            <Button size="sm" variant="outline" disabled={print.busyId != null}
              onClick={() => print.printVersion(f.id)}
              aria-label={`Print this version, made ${exactDate(f.finalized_at)}`}
              iconLeft={<Icon name="printer" size={14} />}>
              {print.busyId === f.id ? 'Getting it ready…' : 'Print this version'}
            </Button>
          </li>
        ))}
      </ul>
      {print.error && <p role="alert" style={{ margin: '8px 0 0', color: 'var(--red-700)', fontSize: 13.5, fontWeight: 600 }}>{print.error}</p>}
      <p style={{ ...MUTED, margin: '8px 0 0', fontSize: 12.5 }}>
        Each copy prints as it was the day it was made final, with the license and Head of Office of that day.
      </p>
    </Card>
  );
}
