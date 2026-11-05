import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { askAssistant, runFollowup, sendFeedback } from '../api/assistant';
import { useAssistant } from '../context/AssistantContext';
import { useAuth } from '../context/AuthContext';
import { useLayout } from '../context/LayoutContext';
import { Icon, LiveStatus } from '../ui';
import { useDialogBehaviour } from '../ui/useDialogBehaviour';
import Answer, { Line } from './assistant/Answer';

/* The chatbot, docked on every protected screen.
 *
 * The model picks a tool; the server runs the query and sends back plain data.
 * Nothing rendered here is model prose — every name, date and count below came
 * out of the database under the caller's own scope. That is what makes an
 * invented child name impossible rather than merely unlikely.
 *
 * The echo line is not decoration. A turn can go wrong in exactly one way the
 * validator cannot catch: the model hears "this week" as "today" and produces
 * a plausible answer to a question nobody asked. Showing what was understood,
 * above the answer, is the mitigation — so it stays visible no matter how the
 * chrome around it changes.
 *
 * How it behaves for the keyboard and a screen reader:
 *  - The launcher is ONE toggle (aria-expanded / aria-controls) and stays on
 *    the page while the panel is open. The header button and Ctrl+/ are two
 *    more doors to the same panel.
 *  - The panel is a dialog named by its heading. Above 640px it is docked and
 *    leaves the page usable; Esc closes it and focus goes back to whatever
 *    opened it. Below 640px it covers the screen, so it is modal: the shared
 *    dialog behaviour (ui useDialogBehaviour) traps Tab, answers Esc and
 *    returns focus, exactly as a Drawer does.
 *  - The conversation is a polite log. A pending reply is aria-busy, and the
 *    box stays readOnly (not disabled) while it waits, so focus and typing
 *    survive the two to nineteen seconds a model takes.
 *  - Starter questions and "Ask about this child" run through
 *    /assistant/followup/ with NO model, so they answer at once and still work
 *    when the model is stopped.
 */

/* Fallback only. The real list is served per role by
 * /api/assistant/capabilities/ (fetched in AssistantContext), from the same
 * source as the assistant's own refusal text; these are what shows if that
 * request fails, and they are deliberately the psychologist's, who is the
 * majority of users. They are typed questions. */
const SUGGESTIONS = [
  'Who am I seeing tomorrow?',
  'How many children am I handling?',
  'Who still needs a follow-up?',
];

/* Server-side cap. Mirrored so the user is stopped by a counter rather than a
 * 400 they cannot see coming. */
const MAX_QUESTION = 150;

/* The fixed reasons a 503 carries (assistant/services.py *_CODE). */
const UNAVAILABLE = {
  switched_off: 'The assistant is switched off by the ISA. Everything else still works.',
  runtime_unreachable: 'The model is not answering right now, so typed questions cannot be answered. '
    + 'The starter questions and buttons here do not need it and still work.',
  hosted_not_allowed: 'That is not available on this deployment.',
};
const UNAVAILABLE_DEFAULT = 'The assistant is unavailable right now. Everything else still works.';

/* Three dots, the gesture everyone already reads as "it is working". A real
 * status carrying the words, with the dots left to the eye. */
function Typing() {
  return (
    <div role="status" style={{ display: 'flex', gap: 4, alignItems: 'center', padding: '4px 2px' }}>
      <span className="racco-sr-only">The assistant is answering</span>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          aria-hidden="true"
          className="racco-typing-dot"
          style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--text-muted)',
            animationDelay: `${i * 0.16}s`,
          }}
        />
      ))}
    </div>
  );
}

/* A suggestion pill. One component for the empty panel's example questions
 * and for the follow-ups under an answer, so the two cannot drift apart. The
 * small size is still 26px tall — above WCAG 2.2's 24px target minimum. */
function Chip({ children, onClick, disabled = false, small = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        cursor: disabled ? 'default' : 'pointer',
        padding: small ? '5px 11px' : '7px 12px',
        background: 'var(--surface)',
        border: '1px solid var(--border-strong)',
        borderRadius: 'var(--radius-pill)',
        color: 'var(--text-body)', font: 'inherit', fontSize: 12.5,
        opacity: disabled ? 0.6 : 1,
        transition: 'background var(--dur-fast) var(--ease-out)',
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = 'var(--surface-brand-soft)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--surface)'; }}
    >{children}</button>
  );
}

function Chips({ children, label, style }) {
  return (
    <div role="group" aria-label={label} style={{ display: 'flex', flexWrap: 'wrap', gap: 7, ...style }}>
      {children}
    </div>
  );
}

// WCAG 2.2 AA target size: 24px square, not the 13px icon inside it.
const THUMB = {
  width: 24, height: 24, display: 'inline-flex', alignItems: 'center',
  justifyContent: 'center', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)', background: 'var(--surface)',
  color: 'var(--text-muted)', cursor: 'pointer', padding: 0,
};

/* Did this answer help? The verdict lands on the same log row as the answer,
 * which is what lets an administrator see an answer that LOOKED fine and was
 * not — the one failure no amount of logging on the server can detect. */
function Feedback({ rated, onRate }) {
  const row = {
    display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, paddingTop: 7,
    borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text-muted)',
  };
  if (rated === 'helpful' || rated === 'not_helpful') {
    return <div style={row}>Thanks — noted.</div>;
  }
  return (
    <div style={row}>
      <span>{rated === 'error' ? "Couldn't save that — try again?" : 'Did this help?'}</span>
      <button type="button" style={THUMB} aria-label="This answer helped"
              onClick={() => onRate('helpful')}>
        <Icon name="thumbs-up" size={13} />
      </button>
      <button type="button" style={THUMB} aria-label="This answer did not help"
              onClick={() => onRate('not_helpful')}>
        <Icon name="thumbs-down" size={13} />
      </button>
    </div>
  );
}

/* What the header line and the launcher's dot say, from what is known: the
 * administrator's switch (capabilities), and how the last typed question went.
 * Green means the model answered; nothing else is dressed as working. */
function statusOf({ enabled, health, hosted }) {
  if (!enabled || health === 'off') {
    return { key: 'off', text: 'Switched off by the ISA', dot: 'var(--ink-400)', ink: 'var(--text-muted)' };
  }
  if (health === 'down') {
    return {
      key: 'down', dot: 'var(--warning-500)', ink: 'var(--warning-700)',
      text: hosted ? 'The hosted model is not answering' : 'The local model is not running',
    };
  }
  if (health === 'ok') {
    return { key: 'ok', text: 'Answers read from your own records', dot: 'var(--success-500)', ink: 'var(--success-700)' };
  }
  return { key: 'unknown', text: 'Answers read from your own records', dot: 'var(--ink-400)', ink: 'var(--text-muted)' };
}

const bubble = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: '4px 14px 14px 14px', padding: '10px 12px',
};

function AssistantAvatar() {
  return (
    <div aria-hidden="true" style={{
      width: 24, height: 24, borderRadius: '50%', flex: 'none',
      background: 'var(--surface-brand-soft)', color: 'var(--brand)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      marginTop: 2,
    }}>
      <Icon name="sparkles" size={12} />
    </div>
  );
}

export default function AssistantPanel() {
  // Open state lives in the context so a quick action can open this panel;
  // nothing outside the component could reach a useState here.
  const {
    open, closeAssistant, toggleAssistant, caps, enabled, drafting,
    request, takeRequest,
  } = useAssistant();
  const { user } = useAuth();
  const role = user?.role_name;
  const narrow = useLayout().width < 640;
  const panelId = 'assistant-panel';
  const titleId = useId();

  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [turns, setTurns] = useState([]);
  // 'unknown' until a typed question has been tried; then what happened.
  const [health, setHealth] = useState('unknown');
  const scroller = useRef(null);
  const input = useRef(null);
  const launcher = useRef(null);
  const opener = useRef(null);
  const wasOpen = useRef(false);

  const hosted = !drafting;
  const status = statusOf({ enabled, health, hosted });

  // A fresh answer that says it is on again lifts the "switched off" a refused
  // question left behind.
  useEffect(() => { if (enabled) setHealth((h) => (h === 'off' ? 'unknown' : h)); }, [enabled, caps]);

  const toBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    });
  }, []);

  // Keep the newest turn in view while the dots are showing, not only after
  // the answer lands.
  useEffect(() => { if (open) toBottom(); }, [open, turns, busy, toBottom]);

  // Whatever had focus when the panel opened gets it back when it closes (the
  // header button, the launcher, the page's own control) - the launcher when
  // that is gone. Taken in a layout effect: it runs before the dialog
  // behaviour moves focus in.
  useLayoutEffect(() => {
    if (open && !wasOpen.current) {
      const at = document.activeElement;
      opener.current = at && at !== document.body ? at : null;
    }
  }, [open]);
  useEffect(() => {
    if (open) {
      input.current?.focus();
    } else if (wasOpen.current) {
      const back = opener.current && document.contains(opener.current) ? opener.current : launcher.current;
      back?.focus();
      opener.current = null;
    }
    wasOpen.current = open;
  }, [open]);

  // Below 640px the panel is modal: Tab stays inside, Esc closes, and focus
  // returns, from the one place that already does this for Drawer and Modal.
  const dialogRef = useDialogBehaviour({ active: open && narrow, onClose: closeAssistant });
  const onKeyDown = (e) => {
    if (e.key === 'Escape' && !narrow && !e.defaultPrevented) {
      e.preventDefault();
      closeAssistant();
    }
  };

  // One path for a typed question, a follow-up chip and a starter: the turn
  // appears at once, the answer replaces the dots, and a failure never breaks
  // the panel. `byModel` is a typed question, the only kind that proves the
  // model is running.
  const submit = useCallback(async (asked, run, byModel = false) => {
    if (!asked || busy) return;
    setBusy(true);
    // The question appears immediately, the way a message does everywhere
    // else. Waiting for the round trip to show it makes the app feel broken.
    setTurns((prev) => [...prev, { asked, pending: true }]);

    let answered;
    try {
      answered = await run();
      setHealth((h) => (byModel ? 'ok' : h === 'off' ? 'unknown' : h));
    } catch (err) {
      // 503 is the assistant switched off or the runtime down; the reason is
      // a fixed code. Neither is an error the user caused, and neither may
      // break the panel.
      if (err?.response?.status === 503) {
        const reason = err.response.data?.reason;
        setHealth(reason === 'switched_off' ? 'off' : 'down');
        answered = { ok: false, message: UNAVAILABLE[reason] || UNAVAILABLE_DEFAULT };
      } else {
        answered = { ok: false, message: 'Something went wrong on the way to the assistant.' };
      }
    }
    // The newest pending turn is the one that was added above; a context card
    // added meanwhile does not displace it.
    setTurns((prev) => {
      const at = prev.map((t) => t.pending).lastIndexOf(true);
      return prev.map((t, i) => (i === at ? { ...t, ...answered, pending: false } : t));
    });
    setBusy(false);
    input.current?.focus();
  }, [busy]);

  const send = useCallback((text) => {
    const asked = (text ?? '').trim();
    if (!asked || busy) return;
    setQuestion('');
    submit(asked, () => askAssistant(asked), true);
  }, [busy, submit]);

  // A chip runs the call it was offered, without the model. The user's side of
  // the conversation shows the chip's own words, as if they had typed them —
  // and anything half-typed in the box is left alone.
  const follow = useCallback((f) => submit(f.label, () => runFollowup(f)), [submit]);

  // A door that brings a request (the child's page, the header search): the
  // child's chips, or the typed question the person chose to send.
  useEffect(() => {
    if (!open || !request) return;
    takeRequest();
    if (request.child) {
      setTurns((prev) => [...prev, { context: request.child, key: request.at }]);
    } else if (request.ask) {
      if (busy) setQuestion(request.ask.slice(0, MAX_QUESTION));
      else send(request.ask.slice(0, MAX_QUESTION));
    }
  }, [open, request, takeRequest, busy, send]);

  // Optimistic: the thanks shows at once, and only a failed save brings the
  // buttons back. Rating is a courtesy the user does us; making them wait on
  // it is how nobody rates anything.
  const rate = useCallback(async (index, job, verdict) => {
    setTurns((prev) => prev.map((t, i) => (i === index ? { ...t, rated: verdict } : t)));
    try {
      await sendFeedback(job, verdict === 'helpful' ? 'accepted' : 'discarded');
    } catch {
      setTurns((prev) => prev.map((t, i) => (i === index ? { ...t, rated: 'error' } : t)));
    }
  }, []);

  // Below 640px a followed link has to get the panel out of the way.
  const onNavigate = useCallback(() => { if (narrow) closeAssistant(); }, [narrow, closeAssistant]);

  const empty = turns.length === 0;
  const off = status.key === 'off';
  const examples = caps?.examples ?? SUGGESTIONS;
  const lastAnswered = turns.map((t) => Boolean(t.asked && !t.pending)).lastIndexOf(true);
  const sendable = !busy && !off && question.trim().length > 0;

  return (
    <>
      <button
        ref={launcher}
        type="button"
        onClick={toggleAssistant}
        className="racco-no-print racco-assistant-launcher"
        data-open={open}
        aria-expanded={open}
        aria-controls={panelId}
        aria-keyshortcuts="Control+/ Meta+/"
        aria-label={`Assistant, ${status.text.charAt(0).toLowerCase()}${status.text.slice(1)}`}
        title="Ask the assistant (Ctrl+/)"
        style={{
          position: 'fixed', right: 20, bottom: 20, zIndex: 60,
          width: 60, height: 60, cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'var(--brand)', color: 'var(--text-on-brand)',
          border: 'none', borderRadius: '50%',
          boxShadow: '0 14px 30px rgba(20,34,94,0.3)',
          transition: 'transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out)',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = 'translateY(-2px)';
          e.currentTarget.style.boxShadow = 'var(--shadow-xl)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = 'none';
          e.currentTarget.style.boxShadow = 'var(--shadow-lg)';
        }}
      >
        <Icon name={open ? 'x' : 'message-square'} size={26} aria-hidden="true" />
        {/* Green only means the model answered when the panel last asked it;
            grey is "not known" or "off", amber is "not running". The same
            fact is in the button's name, for anyone who cannot see the dot. */}
        <span aria-hidden="true" style={{ position: 'absolute', top: 0, right: 0, width: 14, height: 14, borderRadius: '50%', background: status.dot, border: '2.5px solid var(--bg-app)' }} />
      </button>

      {open && (
        <section
          ref={dialogRef}
          id={panelId}
          role="dialog"
          aria-modal={narrow ? 'true' : 'false'}
          aria-labelledby={titleId}
          tabIndex={-1}
          onKeyDown={onKeyDown}
          className="racco-no-print racco-assistant-panel"
          style={{
            zIndex: 61,
            display: 'flex', flexDirection: 'column',
            background: 'var(--surface)',
            border: '1px solid var(--border-strong)',
            overflow: 'hidden',
            animation: 'racco-chat-panel-in var(--dur-base) var(--ease-out) both',
          }}
        >
          {/* Header */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '11px 13px',
            borderBottom: '1px solid var(--divider)',
            background: 'var(--surface)',
          }}>
            <div aria-hidden="true" style={{
              width: 32, height: 32, borderRadius: 10,
              background: 'var(--blue-50)', color: 'var(--blue-600)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flex: 'none',
            }}>
              <Icon name="sparkles" size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <h2 id={titleId} style={{
                fontFamily: 'var(--font-sans)', fontWeight: 800,
                fontSize: 14, color: 'var(--text-strong)', lineHeight: 1.2, margin: 0,
              }}>Assistant</h2>
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 600, fontSize: 12, color: status.ink }}>
                <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: '50%', background: status.dot, flex: 'none' }} />
                {busy ? 'Answering…' : status.text}
              </div>
            </div>
            {turns.length > 0 && (
              <button
                type="button"
                onClick={() => { setTurns([]); input.current?.focus(); }}
                aria-label="Clear this conversation"
                title="Clear"
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-muted)', display: 'flex', padding: 5,
                  borderRadius: 'var(--radius-sm)',
                }}
              >
                <Icon name="eraser" size={15} />
              </button>
            )}
            <button
              type="button"
              onClick={closeAssistant}
              aria-label="Close the assistant"
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--text-muted)', display: 'flex', padding: 5,
                borderRadius: 'var(--radius-sm)',
              }}
            >
              <Icon name="x" size={16} />
            </button>
          </div>

          {/* Conversation. A polite log: what is added is read out, and the
              pending reply inside it says so while it waits. Focusable so the
              scrolling is reachable from the keyboard. */}
          <div
            ref={scroller}
            role="log"
            aria-live="polite"
            aria-relevant="additions text"
            aria-label="Conversation"
            tabIndex={0}
            className="racco-scroll"
            style={{
              flex: 1, minHeight: 0, overflowY: 'auto', padding: '14px 13px',
              display: 'flex', flexDirection: 'column', gap: 15,
              background: 'var(--ink-25)',
            }}
          >
            {/* Said once, at the top of the conversation, where the model is
                hosted: the typed question leaves this machine, and nothing
                else does. */}
            {hosted && (
              <div style={{ display: 'flex', gap: 9, padding: '10px 12px', background: 'var(--surface)', borderLeft: '3px solid var(--warning-500)', borderRadius: '0 8px 8px 0' }}>
                <Icon name="shield-alert" size={16} aria-hidden="true" style={{ color: 'var(--warning-700)', flex: 'none', marginTop: 1 }} />
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-body)' }}>
                  On this deployment a question you type goes to an outside service to be understood.
                  The names of children in your records, phone numbers and email addresses are masked
                  before it leaves. The starter questions and buttons below do not use it.
                </p>
              </div>
            )}

            {empty && (
              <div className="racco-chat-turn">
                <div style={bubble}>
                  <Line muted>
                    {/* Served, not hardcoded: this sentence and the assistant's
                        own refusal text read one source, so they cannot drift
                        apart — and it names what THIS role can reach. */}
                    {caps ? caps.can_ask : 'I answer from your own records.'}
                    {' '}English or Tagalog, whichever you prefer.
                  </Line>
                </div>
                {off ? (
                  <p style={{ margin: '12px 0 0', fontSize: 13, lineHeight: 1.55, color: 'var(--text-body)' }}>
                    The assistant is switched off by the ISA. Everything else in the system still works.
                  </p>
                ) : (
                  <Chips label="Questions to start with" style={{ marginTop: 12 }}>
                    {examples.map((s) => (typeof s === 'string'
                      ? <Chip key={s} onClick={() => send(s)}>{s}</Chip>
                      : <Chip key={s.label} onClick={() => follow(s)}>{s.label}</Chip>))}
                  </Chips>
                )}
                <div style={{ display: 'flex', gap: 9, marginTop: 12, padding: '10px 12px', background: 'var(--surface)', borderLeft: '3px solid var(--border-strong)', borderRadius: '0 8px 8px 0' }}>
                  <Icon name="lock" size={16} aria-hidden="true" style={{ color: 'var(--text-muted)', flex: 'none', marginTop: 1 }} />
                  <p style={{ margin: 0, fontStyle: 'italic', fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                    It looks things up &mdash; it cannot book, edit or approve anything. Every answer is read from your
                    own records, under your own access.
                  </p>
                </div>
              </div>
            )}

            {turns.map((t, i) => (t.context ? (
              /* "Ask about this child": chips for that child, nothing typed.
                 The name is shown here, on the screen it came from; what is
                 sent is the child's number, and the label says "this child". */
              <div key={t.key} className="racco-chat-turn" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <AssistantAvatar />
                <div style={{ ...bubble, flex: 1, minWidth: 0 }}>
                  <Line>Ask about <strong>{t.context.name || 'this child'}</strong>. These look the child up in your records; nothing is typed or sent to the model.</Line>
                  <Chips label="Questions about this child" style={{ marginTop: 8 }}>
                    <Chip small disabled={busy || off}
                          onClick={() => follow({ label: 'Tell me about this child', tool: 'get_child_summary', args: { child_id: t.context.id } })}>
                      Tell me about this child
                    </Chip>
                    <Chip small disabled={busy || off}
                          onClick={() => follow({ label: 'What is left to do for this child?', tool: 'list_care_gaps', args: { child_id: t.context.id } })}>
                      What is left to do for this child?
                    </Chip>
                  </Chips>
                </div>
              </div>
            ) : (
              <div key={i} className="racco-chat-turn"
                   style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {/* What was asked — a bubble on the user's side, as everywhere. */}
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <div style={{
                    maxWidth: '85%',
                    background: 'var(--brand)', color: 'var(--text-on-brand)',
                    borderRadius: '14px 14px 4px 14px',
                    padding: '8px 12px', fontSize: 13.5, lineHeight: 1.45,
                    wordBreak: 'break-word',
                  }}><span className="racco-sr-only">You asked: </span>{t.asked}</div>
                </div>

                {/* The reply, on the assistant's side. */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <AssistantAvatar />
                  <div
                    aria-busy={t.pending ? 'true' : undefined}
                    style={{ ...bubble, flex: 1, minWidth: 0 }}
                  >
                    {t.pending ? <Typing /> : (
                      <>
                        <span className="racco-sr-only">The assistant answered: </span>
                        {/* What the system understood, above the answer it gives.
                            This is the mitigation for a silently dropped filter. */}
                        {t.echo && (
                          <div style={{
                            fontSize: 12, color: 'var(--text-muted)',
                            marginBottom: 7, paddingBottom: 7,
                            borderBottom: '1px solid var(--border)',
                            display: 'flex', alignItems: 'center', gap: 5,
                          }}>
                            <Icon name="search" size={12} aria-hidden="true" />
                            {t.echo}
                          </div>
                        )}
                        {t.ok
                          ? <Answer result={t.result} role={role} onNavigate={onNavigate} />
                          : <Line muted>{t.message}</Line>}
                        {t.ok && t.job && t.result?.reason !== 'greeting_or_closing' && (
                          <Feedback rated={t.rated} onRate={(v) => rate(i, t.job, v)} />
                        )}
                      </>
                    )}
                  </div>
                </div>

                {/* Refinements of the latest answer. Offered by the server, run
                    without the model — they cannot misroute, and they answer at
                    once. Only under the newest answer: that is where refining
                    happens. */}
                {i === lastAnswered && !t.pending && t.ok && t.followups?.length > 0 && (
                  <Chips label="Follow-up questions" style={{ marginLeft: 32, gap: 6 }}>
                    {t.followups.map((f) => (
                      <Chip key={f.label} small disabled={busy} onClick={() => follow(f)}>
                        {f.label}
                      </Chip>
                    ))}
                  </Chips>
                )}
              </div>
            )))}
          </div>

          {/* Composer. readOnly, not disabled, while an answer is on its way:
              a disabled box drops focus and the typing with it. */}
          <form
            onSubmit={(e) => { e.preventDefault(); send(question); }}
            style={{
              display: 'flex', gap: 8, alignItems: 'flex-end',
              padding: '10px 12px',
              borderTop: '1px solid var(--border)',
              background: 'var(--surface)',
            }}
          >
            <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
              <input
                ref={input}
                value={question}
                onChange={(e) => setQuestion(e.target.value.slice(0, MAX_QUESTION))}
                placeholder={off ? 'The assistant is switched off' : 'Ask about your caseload…'}
                readOnly={busy || off}
                aria-busy={busy ? 'true' : undefined}
                aria-label="Ask the assistant"
                style={{
                  width: '100%', boxSizing: 'border-box',
                  padding: '10px 14px',
                  background: 'var(--surface-sunken)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-pill)',
                  color: 'var(--text-body)', font: 'inherit', fontSize: 13.5,
                  outline: 'none',
                }}
                onFocus={(e) => { e.currentTarget.style.boxShadow = 'var(--shadow-focus)'; }}
                onBlur={(e) => { e.currentTarget.style.boxShadow = 'none'; }}
              />
            </div>
            <button
              type="submit"
              aria-disabled={sendable ? undefined : 'true'}
              aria-label="Send"
              onClick={(e) => { if (!sendable) e.preventDefault(); }}
              style={{
                flex: 'none', display: 'flex', alignItems: 'center',
                justifyContent: 'center', width: 38, height: 38,
                cursor: sendable ? 'pointer' : 'default',
                opacity: sendable ? 1 : 0.4,
                background: 'var(--brand)', color: 'var(--text-on-brand)',
                border: 'none', borderRadius: '50%',
                transition: 'opacity var(--dur-fast) var(--ease-out)',
              }}
            >
              <Icon name="arrow-up" size={17} aria-hidden="true" />
            </button>
          </form>

          {question.length > MAX_QUESTION - 30 && (
            <div style={{
              fontSize: 12, color: 'var(--text-muted)', textAlign: 'right',
              padding: '0 14px 8px',
            }}>{MAX_QUESTION - question.length} characters left</div>
          )}

          {/* Spoken, never drawn: when the model stops answering or the switch
              is thrown, the header line changes, and this says so. The panel
              stays mounted while it is open, so the region is already there
              when the words arrive. */}
          <LiveStatus text={status.key === 'off' || status.key === 'down' ? status.text : ''} />
        </section>
      )}
    </>
  );
}
