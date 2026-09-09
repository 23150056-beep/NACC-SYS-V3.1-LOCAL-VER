import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

/* Open a drawer, or select a tab, because a link said so — then forget it.
 *
 * The Dashboard's quick actions are supposed to be ACTIONS. "Book appointment"
 * that only carries you to the Calendar and leaves you to find the button
 * again is a link wearing an action's label, and it duplicates the Calendar
 * entry sitting in the rail two inches to the left. These let the link finish
 * the job.
 *
 * The parameter is deleted once used, so a refresh — or a back-navigation
 * after the person has moved on — does not reopen the drawer over whatever
 * they are doing now. Children.jsx has done this for `?openCreate=1` since
 * the dashboard first linked to it; this is that pattern, extracted rather
 * than copied a fourth time.
 */
export function useOpenFromLink(param, value, run, isOpen = false) {
  const [searchParams, setSearchParams] = useSearchParams();
  const armed = searchParams.get(param) === value;

  useEffect(() => {
    if (!armed || isOpen) return;
    run();
    const next = new URLSearchParams(searchParams);
    next.delete(param);
    setSearchParams(next, { replace: true });
    // `run` is redefined every render by every caller; depending on it would
    // fire this again the moment the person closed what it opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [armed, isOpen]);
}
