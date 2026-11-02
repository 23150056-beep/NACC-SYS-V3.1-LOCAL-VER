import { useRef, useState } from 'react';
import { Alert, Button, Icon } from '../ui';
import { useToast } from '../context/ToastContext';
import { polishRecordText, sendFeedback } from '../api/assistant';

/* "Polish writing" for the free text of a child's record.

   Polish began in the psychologist's remark box and nowhere else, so the
   fields a social worker writes (referral reason, medical notes,
   recommendation, closing summary) had none. This is the same flow for them,
   kept in one place because four fields share it. What the model returns is
   checked server-side (assistant/guards.py) before it reaches this file: it
   never sees the child's record, only the text being typed, and a draft that
   names anything the writer did not is refused there.

   One hook per field. It cannot be shared between fields: each needs its own
   "my text" to go back to and its own job to report on. */

export function usePolish(value, setValue) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null); // { id, draft }
  // The writer's own words, kept from the FIRST polish only. Polishing a draft
  // again must not make the previous draft what "Revert" returns to - that
  // would lose the original for good. A ref, not state: nothing renders from it.
  const original = useRef(null);

  const polish = async () => {
    const raw = (value || '').trim();
    if (!raw) return;
    setBusy(true);
    try {
      const { draft, job_id } = await polishRecordText(raw);
      if (original.current === null) original.current = value;
      setValue(draft);
      setJob({ id: job_id, draft });
    } catch (err) {
      // 503: the assistant is off or the runtime is down - a normal state, not
      // something the writer caused. 422: the server refused the draft and
      // says why in plain words, so show that rather than a generic message.
      toast.error(err.response?.status === 503
        ? 'The writing assistant is unavailable right now.'
        : err.response?.data?.detail || 'Could not polish the text.');
    } finally {
      setBusy(false);
    }
  };

  const clear = () => {
    original.current = null;
    setJob(null);
  };

  const revert = () => {
    if (original.current !== null) setValue(original.current);
    if (job) sendFeedback(job.id, 'discarded').catch(() => {});
    clear();
  };

  // Called only once the record is really saved, so a failed save leaves the
  // draft, the way back and the report of what happened all still in place.
  const settle = (saved) => {
    if (job) {
      const outcome = (saved || '').trim() === job.draft.trim() ? 'accepted' : 'edited';
      sendFeedback(job.id, outcome).catch(() => {});
    }
    clear();
  };

  return { busy, job, polish, revert, settle };
}

export function PolishBar({ polisher, value }) {
  // Always shown. `drafting` false means drafting FROM CASE RECORDS is refused
  // (hosted), which does not apply here: this endpoint sends only the typed
  // text and may use the hosted model (owner, 29 Oct 2026). If the assistant is
  // off, the 503 is toasted by usePolish.
  const { busy, job, polish, revert } = polisher;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button variant="ghost" size="sm" onClick={polish}
                disabled={!(value || '').trim() || busy}
                iconLeft={<Icon name="sparkles" size={16} />}>
          {busy ? 'Polishing…' : 'Polish writing'}
        </Button>
      </div>
      {job && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Alert tone="info" disclaimer style={{ flex: 1, marginTop: 0 }}>
            AI-drafted decision support, not a diagnosis. Review and edit before saving.
          </Alert>
          <Button variant="ghost" size="sm" onClick={revert}>Revert to my text</Button>
        </div>
      )}
    </div>
  );
}
