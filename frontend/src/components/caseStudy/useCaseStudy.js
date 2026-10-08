import { useCallback, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import api from '../../api/client';
import { getAgencyProfile } from '../../api/agency';
import { getCaseStudy, getCaseStudyFinal } from '../../api/caseStudy';
import { sentence } from './model';

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

// A paint, or a moment if the tab is in the background and none comes.
const nextPaint = () => new Promise((resolve) => {
  const timer = setTimeout(resolve, 150);
  requestAnimationFrame(() => { clearTimeout(timer); resolve(); });
});

/* Which case study the print element holds, and printing it.
 *
 *   DRAFT   the live case study, as saved (`copy` is null);
 *   FINAL   the newest final copy, fetched as soon as the tab shows a final
 *           case study, so the page's Print button - and the browser's own
 *           Ctrl+P - print what was signed, never the live record;
 *   a chosen version (Finals on file, "Print this version") for as long as it
 *           takes to print it.
 *
 * A copy is immutable, so each is fetched once and kept. Printing waits for
 * the chosen copy to be on the page: `flushSync` commits it before
 * `window.print()` runs, then a paint is allowed for - printing an element
 * that the browser has not drawn yet gives a blank or the previous page.
 */
export function useScsrPrint(childId, study, enabled) {
  const [copies, setCopies] = useState({}); // final id -> { id, finalized_at, snapshot, ... }
  const [chosen, setChosen] = useState(null); // a version picked to print, by id
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState('');
  const kept = useRef(copies);
  kept.current = copies;

  const isFinal = study?.status === 'final';
  const latestId = isFinal && study?.finals?.length ? study.finals[0].id : null;

  // The newest final, ready before anybody presses Print.
  useEffect(() => {
    if (!enabled || latestId == null || kept.current[latestId]) return undefined;
    let live = true;
    getCaseStudyFinal(childId, latestId)
      .then((record) => { if (live) setCopies((c) => ({ ...c, [record.id]: record })); })
      .catch((err) => { if (live) setError(sentence(err, 'Could not load the final copy to print.')); });
    return () => { live = false; };
  }, [enabled, childId, latestId]);

  // A chosen version is a one-off: the print dialog closing puts the page back
  // on the default.
  useEffect(() => {
    const done = () => setChosen(null);
    window.addEventListener('afterprint', done);
    return () => window.removeEventListener('afterprint', done);
  }, []);

  const copy = chosen != null ? copies[chosen] || null : (isFinal ? copies[latestId] || null : null);

  const run = useCallback(async (id) => {
    flushSync(() => setChosen(id));
    await nextPaint();
    window.print();
  }, []);

  /** "Print this version". */
  const printVersion = useCallback(async (finalId) => {
    setBusyId(finalId);
    setError('');
    try {
      let record = kept.current[finalId];
      if (!record) {
        record = await getCaseStudyFinal(childId, finalId);
        flushSync(() => setCopies((c) => ({ ...c, [finalId]: record })));
      }
      await run(finalId);
    } catch (err) {
      setError(sentence(err, 'Could not open that version to print.'));
    } finally {
      setBusyId(null);
    }
  }, [childId, run]);

  /** The page's own Print button: the newest final, or the draft. If the
   *  newest final could not be fetched when the tab opened, this is the retry. */
  const printDefault = useCallback(
    () => (isFinal && latestId != null && !kept.current[latestId] ? printVersion(latestId) : run(null)),
    [isFinal, latestId, printVersion, run],
  );

  return {
    copy,
    mode: isFinal ? 'final' : 'draft',
    // A draft always prints; a final prints once its copy has arrived - or
    // once fetching it has failed, so that pressing Print tries again.
    ready: !isFinal || !!copies[latestId] || !!error,
    busyId,
    error,
    printDefault,
    printVersion,
  };
}
