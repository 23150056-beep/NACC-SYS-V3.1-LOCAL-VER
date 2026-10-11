import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Icon, Note } from '../ui';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { generateCaseBrief, getLatestCaseBrief, sendFeedback } from '../api/assistant';
import { useSingleFlight } from '../utils/singleFlight';
import { clock } from '../utils/time';

// The written part of a social worker's case brief (owner, 8 Oct 2026), under
// the facts it was drafted from. Social workers only: the ISA and the
// psychologist never reach this component, and the server refuses them anyway.
//
// On open it asks for today's draft (GET .../latest/). That answers 404 when
// nothing was drafted today OR when the facts have changed since - a session
// booked, a consent recorded - so a draft is never shown against a case it no
// longer describes. Drafting is the worker's own press: the model is CPU-bound
// and a draft takes up to a minute, so it is never started by opening a modal.

const FALLBACK_DISCLAIMER =
  'Drafted by the assistant from the facts above. Check it against them before relying on it.';

// Drafts still being written, by `${user}:${child}`. The modal unmounts this
// component when it is closed, and a draft takes up to a minute: reopening
// mid-draft waits for the request already out instead of asking the model a
// second time (the psychologist's brief does the same on the page).
const inFlight = new Map();

const muted = { fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 };

const ready = (data) => ({
  phase: 'ready',
  draft: data.draft,
  generatedAt: data.generated_at,
  jobId: data.job_id,
  disclaimer: data.disclaimer || FALLBACK_DISCLAIMER,
});
// 503 is the assistant being off or the runtime down: a normal state, said as such.
const failure = (err) => ({ phase: err.response?.status === 503 ? 'unavailable' : 'failed' });

export default function WrittenCaseBrief({ childId, onClose }) {
  const { user } = useAuth();
  const toast = useToast();
  const key = `${user?.id}:${childId}`;
  // phase: checking | idle | drafting | ready | unavailable | failed
  const [state, setState] = useState(() => ({ phase: inFlight.has(key) ? 'drafting' : 'checking' }));
  // One press at a time, from the press to the end of the request.
  const [once, busy] = useSingleFlight();
  // The parent keys this component by child, so a reply can only ever belong to
  // the child it was opened for; this stops one landing after the modal closed.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  useEffect(() => {
    let ignore = false;
    const pending = inFlight.get(key);
    const land = (data) => { if (!ignore) setState(ready(data)); };
    const fail = (err) => { if (!ignore) setState(failure(err)); };
    if (pending) {
      pending.then(land, fail);
    } else {
      getLatestCaseBrief(childId).then(land, (err) => {
        // 404 just means there is no current draft: offer to write one.
        if (ignore) return;
        setState(err.response?.status === 404 ? { phase: 'idle' } : failure(err));
      });
    }
    return () => { ignore = true; };
  }, [key, childId]);

  const draft = (again = false) => once(async () => {
    if (inFlight.has(key)) return;
    const request = generateCaseBrief(childId);
    inFlight.set(key, request);
    // Regenerating keeps the draft on screen until the new one lands.
    if (!again) setState({ phase: 'drafting' });
    try {
      const data = await request;
      if (live.current) setState(ready(data));
    } catch (err) {
      if (!live.current) return;
      if (again) {
        toast.error(err.response?.status === 503
          ? 'The assistant is unavailable right now.' : 'Could not prepare the written brief.');
      } else {
        setState(failure(err));
      }
    } finally {
      if (inFlight.get(key) === request) inFlight.delete(key);
    }
  });

  const rate = (outcome) => {
    sendFeedback(state.jobId, outcome).catch(() => {});
    onClose();
  };

  const { phase } = state;
  return (
    <>
      <section aria-label="Written brief" style={{ borderTop: '1px solid var(--border)', paddingTop: 14 }}>
        <div className="racco-eyebrow">Written brief</div>
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {phase === 'checking' && (
            <p role="status" style={muted}>Looking for today&apos;s written brief…</p>
          )}
          {phase === 'idle' && (
            <>
              <p style={muted}>
                No written brief is current for this case yet. The assistant can write a short one
                from the facts above.
              </p>
              <div>
                <Button variant="secondary" onClick={() => draft()} disabled={busy}
                        iconLeft={<Icon name="sparkles" size={16} />}>
                  Draft a written brief
                </Button>
              </div>
            </>
          )}
          {phase === 'drafting' && (
            <p role="status" style={muted}>Drafting… this can take up to a minute.</p>
          )}
          {phase === 'unavailable' && (
            <Note icon="info">
              The written brief is unavailable right now. The facts above come straight from the record.
            </Note>
          )}
          {phase === 'failed' && (
            <Note tone="warning" icon="alert-triangle">Could not prepare the written brief.</Note>
          )}
          {phase === 'ready' && (
            <>
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.6 }}>{state.draft}</div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                Drafted {clock(state.generatedAt)}
              </div>
              <Alert tone="info" disclaimer>{state.disclaimer}</Alert>
            </>
          )}
        </div>
      </section>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 2 }}>
        {phase === 'ready' ? (
          <>
            <Button variant="ghost" onClick={() => rate('discarded')}>Not useful</Button>
            <Button variant="ghost" onClick={() => draft(true)} disabled={busy}>
              {busy ? 'Drafting…' : 'Regenerate (slow)'}
            </Button>
            <Button variant="primary" onClick={() => rate('accepted')}>Useful</Button>
          </>
        ) : (
          <>
            {(phase === 'failed' || phase === 'unavailable') && (
              <Button variant="ghost" onClick={() => draft()} disabled={busy}>Try again</Button>
            )}
            <Button variant="primary" onClick={onClose}>Close</Button>
          </>
        )}
      </div>
    </>
  );
}
