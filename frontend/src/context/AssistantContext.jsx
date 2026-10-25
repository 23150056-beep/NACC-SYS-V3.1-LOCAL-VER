import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { getAssistantCapabilities } from '../api/assistant';
import { getAccess } from '../api/session';
import { useAuth } from './AuthContext';

/* The panel's open state used to live inside AssistantPanel, which meant
 * nothing outside it could open the assistant — the quick actions row could
 * only navigate to a route, and the assistant is not one. It is lifted here so
 * a button anywhere can open it, matching how Auth, Toast and Activity are
 * already shared.
 *
 * This is one more door to the same assistant, not a second one: the same
 * endpoint, the same panel, the same stateless session.
 *
 * It also holds the one /assistant/capabilities/ answer, fetched once per
 * signed-in user and again each time the panel opens (the administrator's
 * switch can change while a page stays open). That request feeds the panel's
 * empty state, its status line and every drafting button (`drafting`), so they
 * cannot disagree.
 *
 * A door can also bring a REQUEST, which the panel takes once it is open:
 *   openAssistant({ child: { id, name } })  chips for that child, nothing typed
 *   openAssistant({ ask: 'text' })          a typed question, sent when chosen
 * The name in a child request is for the screen only; what the panel sends is
 * the child's number. */
const AssistantCtx = createContext({
  open: false, openAssistant: () => {}, closeAssistant: () => {}, toggleAssistant: () => {},
  request: null, takeRequest: () => {},
  caps: null, enabled: true, drafting: true, brief: null,
});

// A click handler passes its event as the first argument; only a real request
// counts.
function asRequest(options) {
  if (options?.child?.id != null) return { child: { id: options.child.id, name: options.child.name || '' } };
  if (typeof options?.ask === 'string' && options.ask.trim()) return { ask: options.ask.trim() };
  return null;
}

export function AssistantProvider({ children }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [caps, setCaps] = useState(null);
  const [request, setRequest] = useState(null);

  // A different person gets a different answer. Keyed on the id, not the
  // object: updateUser() replaces the object when someone saves their own
  // profile, and the same person's answer must not flash back to "drafting".
  const userId = user?.id;
  useEffect(() => { setCaps(null); setRequest(null); }, [userId]);

  // Once per user, and again each time the panel opens. Silent on purpose:
  // without it the panel opens without its suggestions and every drafting
  // button stays, which is what happens when the assistant is switched off. An
  // answer already held stays until the new one lands, so opening never blanks
  // the panel. Under a forced password change the first call is refused (401)
  // and caps stays null; changing the password signs the person out
  // (PasswordChangeGate calls logout(), never updateUser), so the answer is
  // fetched again at their next sign-in, when `user` and the access token are
  // set afresh.
  const signedIn = Boolean(user);
  const loaded = caps !== null;
  useEffect(() => {
    if (!signedIn || !getAccess() || (loaded && !open)) return undefined;
    let live = true;
    getAssistantCapabilities().then((c) => { if (live) setCaps(c); }).catch(() => {});
    return () => { live = false; };
  }, [signedIn, open, loaded]);

  const openAssistant = useCallback((options) => {
    const next = asRequest(options);
    if (next) setRequest({ ...next, at: Date.now() });
    setOpen(true);
  }, []);
  const closeAssistant = useCallback(() => setOpen(false), []);
  const toggleAssistant = useCallback(() => setOpen((v) => !v), []);
  const takeRequest = useCallback(() => setRequest(null), []);

  const value = useMemo(() => ({
    open, openAssistant, closeAssistant, toggleAssistant, request, takeRequest,
    caps,
    // The administrator's switch, as of the last answer. Only an explicit
    // `false` says off: no answer yet, a failed request or an older API
    // without the key all leave the assistant as it was.
    enabled: caps?.enabled !== false,
    // Only an explicit `false` hides anything: no answer yet, a failed
    // request, or an older API without the key all leave the buttons as they
    // were, and the server's 503 stays the authority.
    drafting: caps?.drafting !== false,
    // Which brief this person gets: 'clinical' (the psychologist's, facts and
    // a written brief) or 'case' (facts and, for a social worker, what to do
    // next; no model). null until the answer
    // arrives; the screens fall back to the role meanwhile.
    brief: caps?.brief ?? null,
  }), [open, openAssistant, closeAssistant, toggleAssistant, request, takeRequest, caps]);
  return <AssistantCtx.Provider value={value}>{children}</AssistantCtx.Provider>;
}

export const useAssistant = () => useContext(AssistantCtx);
