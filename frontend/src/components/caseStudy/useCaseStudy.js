import { useCallback, useEffect, useState } from 'react';
import api from '../../api/client';
import { getAgencyProfile } from '../../api/agency';
import { getCaseStudy } from '../../api/caseStudy';

/* The child's case study, loaded for the child's page.
 *
 * The page owns this rather than the tab, because three things outside the tab
 * depend on it: whether the Case study tab exists at all (a 404 means this
 * reader may not know there is one), the Print button (which prints the case
 * study while that tab is open), and the print element itself.
 *
 *   phase  'idle'     not an Adoption record, or nothing asked yet
 *          'loading'  asked, not answered
 *          'ready'    `study` holds the answer (its shape depends on the role)
 *          'none'     404: no tab
 *          'error'    anything else: the tab shows, with a way to try again
 *
 * Replies are keyed to the child they were asked for, so a slow answer cannot
 * fill another child's page when the route is re-used.
 */
export function useCaseStudy(childId, enabled) {
  const [result, setResult] = useState(null); // { childId, phase, study }
  // Boxes and header fields changed on the screen and not yet saved: read by
  // the Print button, which warns that the printout is what is SAVED.
  const [unsaved, setUnsaved] = useState(0);
  const wanted = String(childId);

  const load = useCallback(() => {
    if (!enabled) return Promise.resolve();
    return getCaseStudy(wanted)
      .then((study) => setResult({ childId: wanted, phase: 'ready', study }))
      .catch((err) => setResult({
        childId: wanted, phase: err.response?.status === 404 ? 'none' : 'error', study: null,
      }));
  }, [wanted, enabled]);

  useEffect(() => {
    setUnsaved(0);
    load();
  }, [load]);

  // The editor and the Start button replace the study as they save.
  const setStudy = useCallback((next) => setResult((r) => {
    if (!r || r.childId !== wanted) return r;
    return { ...r, phase: 'ready', study: typeof next === 'function' ? next(r.study) : next };
  }), [wanted]);

  const mine = result && result.childId === wanted ? result : null;
  const phase = !enabled ? 'idle' : (mine ? mine.phase : 'loading');
  return { phase, study: mine?.study || null, setStudy, reload: load, unsaved, setUnsaved };
}

/* What the printed case study needs beyond the case study: the agency's name,
 * address and Head of Office, and the social worker's own license. Both are
 * read only once the social worker has a case study to print, and a failure
 * leaves blank lines to complete by hand rather than blocking the print. */
export function usePrintExtras(enabled) {
  const [extras, setExtras] = useState({ agency: null, license: null });
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    getAgencyProfile().then((agency) => { if (live) setExtras((e) => ({ ...e, agency })); }).catch(() => {});
    api.get('/auth/me/profile/').then((r) => { if (live) setExtras((e) => ({ ...e, license: r.data })); }).catch(() => {});
    return () => { live = false; };
  }, [enabled]);
  return extras;
}
