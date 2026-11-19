import { Card, Badge } from '../ui';
import { paProgressLabel, paProgressTone, paProgressDetail, formatStepMoment, formatDuration } from '../utils/preAssessment';

/* The pre-assessment tracker on the child's page: the five wizard steps with
 * when each was started, last edited and done, and the time it took. The data
 * is Child.pre_assessment_progress from the server, already scoped to what
 * this reader may see. */
const DOT = {
  done: { bg: 'var(--success-500)', label: 'Done' },
  current: { bg: 'var(--blue-600)', label: 'Current step' },
  idle: { bg: 'var(--border-strong)', label: 'Not reached' },
};

function Field({ label, value }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: value ? 'var(--text-strong)' : 'var(--text-faint)' }}>{value || 'Not yet'}</div>
    </div>
  );
}

export default function PreAssessmentTracker({ progress }) {
  const state = progress?.state || 'not_started';
  const steps = progress?.steps || [];
  const detail = paProgressDetail(progress);
  return (
    <Card eyebrow="Tracker" title="Pre-assessment tracker" padding="0">
      <div style={{ padding: '10px 15px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', borderBottom: '1px solid var(--divider-row)' }}>
        <Badge tone={paProgressTone(progress)} size="sm" dot>{paProgressLabel(progress)}</Badge>
        {detail && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{detail}</span>}
        {state === 'completed' && progress.reassessment && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>re-assessment</span>}
      </div>
      {state === 'not_started' ? (
        <div style={{ padding: '14px 15px', fontSize: 13, color: 'var(--text-muted)' }}>No pre-assessment started yet.</div>
      ) : (
        <ol style={{ listStyle: 'none', margin: 0, padding: '4px 15px 8px' }}>
          {steps.map((s, i) => {
            const isCurrent = state !== 'completed' && s.step === progress.step;
            const kind = isCurrent ? 'current' : (s.done_at ? 'done' : 'idle');
            const dot = DOT[kind];
            return (
              <li key={s.step} style={{ padding: '10px 0', borderBottom: i === steps.length - 1 ? 'none' : '1px solid var(--ink-50)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 6 }}>
                  <span aria-hidden="true" style={{ width: 10, height: 10, borderRadius: '50%', background: dot.bg, flex: 'none' }} />
                  <span style={{ fontWeight: 800, fontSize: 13, color: 'var(--text-strong)' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-muted)', marginRight: 6 }}>{i + 1}</span>{s.label}
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{dot.label}</span>
                </div>
                <div className="racco-pa-track-grid" style={{ paddingLeft: 19 }}>
                  <Field label="Started" value={formatStepMoment(s.started_at)} />
                  <Field label="Last edited" value={formatStepMoment(s.last_edited_at)} />
                  <Field label="Done" value={formatStepMoment(s.done_at)} />
                  <Field label="Time spent" value={s.done_at && s.seconds != null ? formatDuration(s.seconds) : ''} />
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
