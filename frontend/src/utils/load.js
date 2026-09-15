/* One message per screen, however many of its requests failed.
 *
 * Every screen here opens by firing its data in parallel — Schedule asks for
 * five things at once — and each of those calls used to swallow its own
 * failure with `catch(() => {})`. The screen then rendered as though the
 * answer were "nothing": an empty calendar, an empty results table, a roster
 * reading "No records found. Try a different name, case ID, or status filter."
 * The filter was never the problem, and telling somebody to change it sends
 * them hunting for records that were never fetched.
 *
 * Reporting each failure where it happens trades one silence for five stacked
 * red toasts, so this reports once for the screen.
 *
 * `Promise.allSettled` rather than `Promise.all`, deliberately: `all` is
 * fail-fast, and a screen where four requests of five succeeded should still
 * show the four. Every job runs, every result is kept, and the message is
 * about the screen rather than about any one request.
 *
 * Falsy entries are skipped, so a caller can write a conditional request
 * inline:
 *
 *   loadAll(toast, [
 *     () => api.get('/children/').then(...),
 *     canManage && (() => api.get('/availability/').then(...)),
 *   ])
 *
 * Not for everything. A request that repeats on a timer, or that reports
 * nothing a person is waiting on, should still fail quietly — the presence
 * heartbeat and the assistant's feedback calls keep their bare catch, and say
 * why where they sit.
 */
const DEFAULT_MESSAGE = 'Could not load this screen. Check your connection and refresh.';

export function loadAll(toast, jobs, message = DEFAULT_MESSAGE) {
  const running = jobs
    .filter(Boolean)
    .map((job) => (typeof job === 'function' ? job() : job));
  return Promise.allSettled(running).then((results) => {
    if (results.some((r) => r.status === 'rejected')) toast.error(message);
    return results;
  });
}
