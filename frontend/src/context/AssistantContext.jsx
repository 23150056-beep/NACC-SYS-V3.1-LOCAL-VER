import { createContext, useContext, useEffect, useMemo, useState } from 'react';
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
 * signed-in user. That request feeds both the panel's empty state and every
 * drafting button (`drafting`), so the two cannot disagree. */
const AssistantCtx = createContext({
  open: false, openAssistant: () => {}, closeAssistant: () => {},
  caps: null, drafting: true, brief: null, caseBriefWriting: false,
});

export function AssistantProvider({ children }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [caps, setCaps] = useState(null);

  // A different person gets a different answer. Keyed on the id, not the
  // object: updateUser() replaces the object when someone saves their own
  // profile, and the same person's answer must not flash back to "drafting".
  const userId = user?.id;
  useEffect(() => { setCaps(null); }, [userId]);

  // Once per user, and again on each panel open while there is still no
  // answer (the panel's old retry). Silent on purpose: without it the panel
  // opens without its suggestions and every drafting button stays, which is
  // what happens when the assistant is switched off. Under a forced password
  // change the first call is refused (401) and caps stays null; changing the
  // password signs the person out (PasswordChangeGate calls logout(), never
  // updateUser), so the answer is fetched again at their next sign-in, when
  // `user` and the access token are set afresh.
  useEffect(() => {
    if (!user || caps || !getAccess()) return undefined;
    let live = true;
    getAssistantCapabilities().then((c) => { if (live) setCaps(c); }).catch(() => {});
    return () => { live = false; };
  }, [user, open, caps]);

  const value = useMemo(() => ({
    open,
    openAssistant: () => setOpen(true),
    closeAssistant: () => setOpen(false),
    caps,
    // Only an explicit `false` hides anything: no answer yet, a failed
    // request, or an older API without the key all leave the buttons as they
    // were, and the server's 503 stays the authority.
    drafting: caps?.drafting !== false,
    // Which brief this person gets: 'clinical' (the psychologist's, facts and
    // a written brief) or 'case' (facts alone). null until the answer
    // arrives; the screens fall back to the role meanwhile.
    brief: caps?.brief ?? null,
    // Whether this person is offered a WRITTEN case brief: a social worker, on
    // a deployment that drafts. Unlike `drafting` this fails closed - until the
    // server says so there is no button, because it is new, it is one role's,
    // and a brief modal that opens before the answer is simply facts alone.
    caseBriefWriting: caps?.case_brief_writing === true,
  }), [open, caps]);
  return <AssistantCtx.Provider value={value}>{children}</AssistantCtx.Provider>;
}

export const useAssistant = () => useContext(AssistantCtx);
