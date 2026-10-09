import { useEffect, useRef, useState } from 'react';
import api from '../../api/client';
import { Alert, Button, FormField, Icon, Input, Modal, Select } from '../../ui';
import { firstError } from '../../utils/errors';
import { caseRef } from '../../utils/child';

/* The ISA takes out a record that was made by mistake for a child who already
 * has one (backend children/duplicates.py). It is the only way a child is ever
 * deleted, so it asks for two things beyond a yes: which record this one
 * repeats, and this record's own case number typed out.
 *
 * Which records it can repeat comes from the duplicate check - the same name -
 * narrowed here to the same birth date. The server is the authority on whether
 * the record may go (nothing booked, nothing written, no psychologist
 * accepted), and its refusal is shown in its own words. */
export default function RemoveDuplicateDialog({ child, onClose, onRemoved }) {
  const ref = caseRef(child.id);
  // null while the matches load.
  const [matches, setMatches] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [original, setOriginal] = useState('');
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Set in the same tick as the click, before any await, like Save Record's
  // guard: state would still say "not busy" to a second click.
  const working = useRef(false);

  useEffect(() => {
    let live = true;
    api.get('/children/check-duplicate/', {
      params: { first_name: child.first_name || '', last_name: child.last_name || '', birth_date: child.birth_date || '' },
    })
      .then((r) => {
        if (!live) return;
        const same = (r.data.matches || [])
          .filter((m) => m.id && m.id !== child.id && m.birth_date === child.birth_date);
        setMatches(same);
        if (same.length === 1) setOriginal(String(same[0].id));
      })
      .catch(() => { if (live) setLoadError('The other records could not be loaded. Close this and try again.'); });
    return () => { live = false; };
  }, [child.id, child.first_name, child.last_name, child.birth_date]);

  const typedRight = typed.trim().toLowerCase() === ref.toLowerCase();
  const ready = !!original && typedRight && !busy;

  const remove = async () => {
    if (working.current || !ready) return;
    working.current = true;
    setBusy(true);
    setError('');
    try {
      const { data } = await api.post(`/children/${child.id}/remove-duplicate/`, {
        duplicate_of: Number(original), case_reference: typed.trim(),
      });
      onRemoved(data, matches.find((m) => String(m.id) === original));
    } catch (err) {
      setError(firstError(err.response?.data, 'Could not remove the record. Try again.'));
    } finally {
      working.current = false;
      setBusy(false);
    }
  };

  return (
    <Modal
      onClose={busy ? undefined : onClose} tone="danger" width={520}
      icon={<Icon name="trash-2" size={19} />}
      title={`Remove duplicate record ${ref}?`}
      footer={<>
        <Button variant="ghost" onClick={onClose} disabled={busy}>Keep it</Button>
        <Button variant="danger" onClick={remove} disabled={!ready} iconLeft={<Icon name="trash-2" size={16} />}>
          {busy ? 'Removing…' : 'Remove the record'}
        </Button>
      </>}
    >
      <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6, color: 'var(--text-body)' }}>
        This deletes <strong>{child.fullname}</strong> ({ref}) for good, with its case referral file and
        any open request to a psychologist. Use it only for a record added by mistake when the child
        already has another one. A record that has sessions, notes or any other work cannot be removed.
      </p>
      {loadError && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{loadError}</Alert>}
      {!matches && !loadError && <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-muted)' }}>Looking for the other record…</p>}
      {matches && matches.length === 0 && (
        <Alert tone="info" icon={<Icon name="info" size={18} />}>
          No other record has this child&apos;s name and date of birth, so this is not a duplicate of anything.
        </Alert>
      )}
      {matches && matches.length > 0 && (
        <FormField label="This record duplicates" required htmlFor="duplicate-of">
          <Select id="duplicate-of" value={original} onChange={(e) => setOriginal(e.target.value)} disabled={busy}>
            <option value="">— Choose the record to keep —</option>
            {matches.map((m) => (
              <option key={m.id} value={String(m.id)}>
                {caseRef(m.id)} · {m.fullname} · b. {m.birth_date} · {m.status === 'inactive' ? 'Archived' : 'Active'}
                {m.social_worker_name ? ` · SW ${m.social_worker_name}` : ''}
              </option>
            ))}
          </Select>
        </FormField>
      )}
      <FormField label={<span>Type <span className="racco-mono" style={{ fontWeight: 800, color: 'var(--text-strong)' }}>{ref}</span> to confirm</span>} htmlFor="duplicate-case-number">
        <Input id="duplicate-case-number" value={typed} onChange={(e) => setTyped(e.target.value)}
          placeholder={ref} autoComplete="off" disabled={busy} />
      </FormField>
      {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
    </Modal>
  );
}
