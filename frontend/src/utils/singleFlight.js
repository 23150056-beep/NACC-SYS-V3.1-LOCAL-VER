import { useCallback, useRef, useState } from 'react';

/* One write at a time, from the press to the end of it.
 *
 *   const [once, saving] = useSingleFlight();
 *   const add = () => once(async () => {
 *     if (!(await confirm({ ... }))) return;
 *     await api.post(...);
 *   });
 *   <Button onClick={add} disabled={saving}>Add</Button>
 *
 * Most writes here ask first (useConfirm) and then await the request, with the
 * button still live behind both. A press while the request was going started a
 * second confirmation and a second request: on 9 Oct 2026 that added a child
 * twice. The ref is the guard - it is set in the same tick as the press, before
 * any await, where state would still say "idle" to a second click. `saving` is
 * only what the button shows.
 *
 * A job that returns early (the confirmation answered "no") or throws releases
 * the guard like one that finishes. */
export function useSingleFlight() {
  const flying = useRef(false);
  const [saving, setSaving] = useState(false);
  const once = useCallback(async (job) => {
    if (flying.current) return undefined;
    flying.current = true;
    setSaving(true);
    try {
      return await job();
    } finally {
      flying.current = false;
      setSaving(false);
    }
  }, []);
  return [once, saving];
}
