import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  askAssistant, getAssistantCapabilities, runFollowup, sendFeedback,
} from '../api/assistant';
import { useAssistant } from '../context/AssistantContext';
import { Icon, roleLabel } from '../ui';

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
 */

/* Fallback only. The real list is served per role by
 * /api/assistant/capabilities/, from the same source as the assistant's own
 * refusal text; these are what shows if that request fails, and they are
 * deliberately the psychologist's, who is the majority of users. */
const SUGGESTIONS = [
  'Who am I seeing tomorrow?',
  'How many children am I handling?',
  'Who still needs a follow-up?',
];

/* Server-side cap. Mirrored so the user is stopped by a counter rather than a
 * 400 they cannot see coming. */
const MAX_QUESTION = 150;

function Line({ children, muted = false }) {
  return (
    <div style={{
      fontSize: 13.5,
      lineHeight: 1.55,
      color: muted ? 'var(--text-muted)' : 'var(--text-body)',
      padding: '2px 0',
    }}>{children}</div>
  );
}

/* Three dots, the gesture everyone already reads as "it is working". */
function Typing() {
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center', padding: '4px 2px' }}
         aria-label="The assistant is answering">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
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
        color: 'var(--text-body)', font: 'inherit', fontSize: small ? 12 : 12.5,
        opacity: disabled ? 0.6 : 1,
        transition: 'background var(--dur-fast) var(--ease-out)',
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = 'var(--surface-brand-soft)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--surface)'; }}
    >{children}</button>
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
    borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--text-muted)',
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

function Answer({ result }) {
  const { kind } = result || {};

  if (kind === 'breakdown') {
    // get_statistics. A plain total is a figure, styled like the headcount
    // answer so every "how many" reads as the same kind of answer. A breakdown
    // is a table with a thin bar per row: the table carries every value and is
    // what a screen reader gets; the bars are decorative and only help the eye
    // compare. One hue for one series, --blue-500 because it passes the
    // palette checks against this surface where --brand's darker step does not.
    const rows = result.rows || [];
    const max = Math.max(1, ...rows.map((r) => r.count));
    // A figure, when the server sends one, is what the question asked for
    // when that is not a count — "how long do children wait?" is in days.
    const figure = result.figure || { value: result.total, label: result.subject };
    return (
      <div>
        {rows.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
            <span style={{
              fontFamily: 'var(--font-display)', fontSize: 26,
              fontWeight: 700, color: 'var(--text-strong)', lineHeight: 1.1,
            }}>{figure.value}</span>
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{figure.label}</span>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <caption style={{
              captionSide: 'top', textAlign: 'left', padding: '0 0 6px',
              fontSize: 13, fontWeight: 700, color: 'var(--text-strong)',
            }}>{result.title}</caption>
            <tbody>
              {rows.map((r) => (
                <tr key={r.label}>
                  <th scope="row" style={{
                    textAlign: 'left', fontWeight: 400, color: 'var(--text-strong)',
                    padding: '3px 8px 3px 0', verticalAlign: 'middle', wordBreak: 'break-word',
                  }}>{r.label}</th>
                  <td aria-hidden="true" style={{ width: '38%', padding: '3px 0', verticalAlign: 'middle' }}>
                    {r.count > 0 && (
                      <div style={{
                        height: 8, minWidth: 2, width: `${(100 * r.count) / max}%`,
                        background: 'var(--blue-500)', borderRadius: '0 4px 4px 0',
                      }} />
                    )}
                  </td>
                  <td style={{
                    textAlign: 'right', padding: '3px 0 3px 8px', verticalAlign: 'middle',
                    color: 'var(--text-strong)', fontVariantNumeric: 'tabular-nums',
                    whiteSpace: 'nowrap',
                  }}>{r.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {result.note && <Line muted>{result.note}</Line>}
        {/* The screen this number comes from, when the reader can open it —
            the check on a number that looks wrong is one click away. */}
        {result.screen && (
          <div style={{ marginTop: 6 }}>
            <Link to={result.screen.path} style={{
              fontSize: 12, fontWeight: 600, color: 'var(--brand)',
            }}>Open {result.screen.label}</Link>
          </div>
        )}
      </div>
    );
  }

  if (kind === 'availability') {
    if (!result.items.length) return <Line muted>No free slots in that period.</Line>;
    return result.items.map((s, i) => (
      <Line key={i}>
        <strong>{s.psychologist}</strong>
        <span style={{ color: 'var(--text-muted)' }}>
          {' '}· {s.weekday} {s.date} · {s.start}–{s.end}
        </span>
        {/* Places left, not just "free": a window with one place is a
            different answer from a window with four. */}
        <span style={{
          marginLeft: 6, padding: '1px 6px', borderRadius: 'var(--radius-pill)',
          fontSize: 11, fontWeight: 700,
          background: 'var(--ink-50)', color: 'var(--text-muted)',
        }}>{s.remaining} left</span>
      </Line>
    ));
  }

  if (kind === 'people_count') {
    // Colleagues, not children. Rendered like the child count so the two read
    // as the same kind of answer — the bug this tool fixes was one being
    // served as the other.
    return (
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{
          fontFamily: 'var(--font-display)', fontSize: 26,
          fontWeight: 700, color: 'var(--text-strong)', lineHeight: 1.1,
        }}>{result.count}</span>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          {result.role === 'anyone' ? 'active accounts' : `active ${roleLabel(result.role)} accounts`}
        </span>
      </div>
    );
  }

  if (kind === 'appointments') {
    if (!result.items.length) {
      // "Nothing scheduled" is the wrong sentence for "who did I see
      // yesterday?" — nothing was scheduled is not nothing happened.
      const past = ['yesterday', 'last_week', 'last_month', 'last_year']
        .includes(result.when);
      return <Line muted>{past ? 'Nothing recorded.' : 'Nothing scheduled.'}</Line>;
    }
    // Staff and administrators hold no sessions, so they get the agency's
    // calendar — said out loud, with whose session each row is, because the
    // same question answers differently for a psychologist.
    const agency = result.scope === 'agency';
    return (
      <>
        {agency && <Line muted>Across the agency:</Line>}
        {result.items.map((a, i) => (
          <Line key={i}>
            <strong>{a.child}</strong>
            <span style={{ color: 'var(--text-muted)' }}>
              {' '}· {a.when} · {a.purpose}{agency && a.psychologist ? ` · ${a.psychologist}` : ''}
            </span>
            {a.status && a.status !== 'scheduled' && (
              <span style={{
                marginLeft: 6, padding: '1px 6px', borderRadius: 'var(--radius-pill)',
                fontSize: 11, fontWeight: 700, textTransform: 'capitalize',
                background: 'var(--ink-50)', color: 'var(--text-muted)',
              }}>{a.status.replace('_', ' ')}</span>
            )}
          </Line>
        ))}
        {/* A month across the agency is hundreds of rows. A cut-off list must
            never read as the whole calendar. */}
        {result.total > result.items.length && (
          <Line muted>
            Showing {result.items.length} of {result.total}. Open the Calendar
            to see the rest.
          </Line>
        )}
      </>
    );
  }

  if (kind === 'children') {
    if (result.items.length) {
      return result.items.map((c) => <Line key={c.id}>{c.name}</Line>);
    }
    // No match. Rather than a bare "none", show what the agency actually
    // records — the model's clinical vocabulary and this agency's do not always
    // use the same words, and a dead end tells the user nothing.
    return (
      <>
        {/* The tool says what "none" means for the question that was asked.
            This branch renders more than one tool's list, and the concern
            search's sentence answered "which children have no psychologist?"
            with "no open concern matches that wording". */}
        <Line muted>{result.empty || 'No open concern matches that wording.'}</Line>
        {result.available?.length > 0 && (
          <div style={{ marginTop: 6 }}>
            <Line muted>Concerns recorded in your caseload:</Line>
            {result.available.map((d) => <Line key={d}>· {d}</Line>)}
          </div>
        )}
      </>
    );
  }

  if (kind === 'care_gaps') {
    if (!result.items.length) return <Line muted>Nobody is overdue.</Line>;
    return result.items.map((g, i) => (
      <Line key={i}>
        <strong>{g.child}</strong>
        {/* The sentence the Monitoring screen shows, not the internal slug. */}
        <span style={{ color: 'var(--text-muted)' }}> · {g.message || g.type}</span>
      </Line>
    ));
  }

  if (kind === 'summary') {
    if (result.match === 'none') {
      return <Line muted>No child of yours matches “{result.name}”.</Line>;
    }
    if (result.match === 'several') {
      return (
        <>
          <Line muted>Several children match — which one?</Line>
          {result.items.map((c) => <Line key={c.id}>· {c.name}</Line>)}
        </>
      );
    }
    return (
      <>
        <Line><strong>{result.child.name}</strong>
          <span style={{ color: 'var(--text-muted)' }}> · {result.child.status}</span>
        </Line>
        {result.gaps?.length > 0 && (
          <Line muted>Needs attention: {result.gaps.join(', ')}</Line>
        )}
        {result.remarks?.length > 0 && (
          <div style={{ marginTop: 6 }}>
            {result.remarks.map((r, i) => (
              <Line key={i} muted>{r.date}: {r.text}</Line>
            ))}
          </div>
        )}
      </>
    );
  }

  if (kind === 'self_report_flags') {
    if (!result.items.length) return <Line muted>No flagged self-reports.</Line>;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {result.items.map((f, i) => (
          <div key={i} style={{
            padding: '10px 12px', borderRadius: 'var(--radius-md)',
            background: 'var(--amber-50)', border: '1px solid var(--border)',
          }}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>
              {f.child}
              {f.reviewed && (
                <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 600, color: 'var(--text-muted)' }}>
                  reviewed
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              {f.question}
            </div>
            {/* The child's own words, quoted. A flag without them is not
                something anyone can act on. */}
            <div style={{ fontSize: 13, marginTop: 4, fontStyle: 'italic' }}>
              “{f.answer}”
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 4 }}>
              {f.date}
            </div>
          </div>
        ))}
        {/* Never let a truncated list read as the whole list — these are
            children reporting distress. */}
        {result.total > result.items.length && (
          <Line muted>
            Showing {result.items.length} of {result.total}. Open Progress
            Monitoring to read the rest.
          </Line>
        )}
      </div>
    );
  }

  if (kind === 'message') return <Line muted>{result.text}</Line>;
  return null;
}

export default function AssistantPanel() {
  // Open state lives in the context so a quick action can open this panel;
  // nothing outside the component could reach a useState here.
  const { open, openAssistant, closeAssistant } = useAssistant();
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [turns, setTurns] = useState([]);
  const [caps, setCaps] = useState(null);
  const scroller = useRef(null);
  const input = useRef(null);

  // Fetched once, on first open. Someone who arrived by clicking a button has
  // typed nothing and needs a starting point.
  // Silent on purpose: without these the panel simply opens without its
  // suggestions, which is the same thing it does when the assistant is
  // switched off. Every other part of it still works.
  useEffect(() => {
    if (open && !caps) getAssistantCapabilities().then(setCaps).catch(() => {});
  }, [open, caps]);

  const toBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    });
  }, []);

  // Keep the newest turn in view while the dots are showing, not only after
  // the answer lands.
  useEffect(() => { if (open) toBottom(); }, [open, turns, busy, toBottom]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);

  // One path for a typed question and a follow-up chip: the turn appears at
  // once, the answer replaces the dots, and a failure never breaks the panel.
  const submit = useCallback(async (asked, run) => {
    if (!asked || busy) return;
    setBusy(true);
    // The question appears immediately, the way a message does everywhere
    // else. Waiting for the round trip to show it makes the app feel broken.
    setTurns((prev) => [...prev, { asked, pending: true }]);

    let answered;
    try {
      answered = await run();
    } catch (err) {
      // 503 is the assistant switched off or the runtime down. Neither is an
      // error the user caused, and neither may break the panel.
      const off = err?.response?.status === 503;
      answered = {
        ok: false,
        message: off
          ? 'The assistant is unavailable right now. Everything else still works.'
          : 'Something went wrong on the way to the assistant.',
      };
    }
    setTurns((prev) => prev.map((t, i) =>
      i === prev.length - 1 ? { ...t, ...answered, pending: false } : t));
    setBusy(false);
    input.current?.focus();
  }, [busy]);

  const send = useCallback((text) => {
    const asked = (text ?? '').trim();
    if (!asked || busy) return;
    setQuestion('');
    submit(asked, () => askAssistant(asked));
  }, [busy, submit]);

  // A chip runs the call it was offered, without the model. The user's side of
  // the conversation shows the chip's own words, as if they had typed them —
  // and anything half-typed in the box is left alone.
  const follow = useCallback((f) => submit(f.label, () => runFollowup(f)), [submit]);

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

  if (!open) {
    return (
      <button
        type="button"
        onClick={openAssistant}
        className="racco-no-print"
        aria-label="Open the assistant"
        title="Ask the assistant"
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
        <Icon name="message-square" size={26} />
        {/* Green means the runtime answered when the panel last asked. */}
        <span style={{ position: 'absolute', top: 0, right: 0, width: 14, height: 14, borderRadius: '50%', background: 'var(--success-500)', border: '2.5px solid var(--bg-app)' }} />
      </button>
    );
  }

  const empty = turns.length === 0;

  return (
    <div
      className="racco-no-print"
      style={{
        position: 'fixed', right: 20, bottom: 20, zIndex: 60,
        width: 'min(386px, calc(100vw - 32px))',
        height: 'min(600px, calc(100vh - 40px))',
        display: 'flex', flexDirection: 'column',
        background: 'var(--surface)',
        border: '1px solid var(--border-strong)',
        borderRadius: 16,
        boxShadow: '0 26px 60px rgba(20,34,94,0.3)',
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
        <div style={{
          width: 32, height: 32, borderRadius: 10,
          background: 'var(--blue-50)', color: 'var(--blue-600)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flex: 'none',
        }}>
          <Icon name="sparkles" size={18} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontFamily: 'var(--font-sans)', fontWeight: 800,
            fontSize: 14, color: 'var(--text-strong)', lineHeight: 1.2,
          }}>Assistant</div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontWeight: 600, fontSize: 11, color: 'var(--success-700)' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--success-500)' }} />
            {busy ? 'Answering…' : 'Answers read from your own records'}
          </div>
        </div>
        {turns.length > 0 && (
          <button
            type="button"
            onClick={() => setTurns([])}
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

      {/* Conversation */}
      <div
        ref={scroller}
        className="racco-scroll"
        style={{
          flex: 1, minHeight: 0, overflowY: 'auto', padding: '14px 13px',
          display: 'flex', flexDirection: 'column', gap: 15,
          background: 'var(--ink-25)',
        }}
      >
        {empty && (
          <div className="racco-chat-turn">
            <div style={{
              background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: '4px 14px 14px 14px', padding: '11px 13px',
            }}>
              <Line muted>
                {/* Served, not hardcoded: this sentence and the assistant's own
                    refusal text read one source, so they cannot drift apart —
                    and it names what THIS role can reach. */}
                {caps ? caps.can_ask : 'I answer from your own records.'}
                {' '}English or Tagalog, whichever you prefer.
              </Line>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 12 }}>
              {(caps?.examples ?? SUGGESTIONS).map((s) => (
                <Chip key={s} onClick={() => send(s)}>{s}</Chip>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 9, marginTop: 12, padding: '10px 12px', background: 'var(--surface)', borderLeft: '3px solid var(--border-strong)', borderRadius: '0 8px 8px 0' }}>
              <Icon name="lock" size={16} style={{ color: 'var(--text-muted)', flex: 'none', marginTop: 1 }} />
              <p style={{ fontStyle: 'italic', fontSize: 11.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                It looks things up &mdash; it cannot book, edit or approve anything. Every answer is read from your
                own records, under your own access.
              </p>
            </div>
          </div>
        )}

        {turns.map((t, i) => (
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
              }}>{t.asked}</div>
            </div>

            {/* The reply, on the assistant's side. */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <div style={{
                width: 24, height: 24, borderRadius: '50%', flex: 'none',
                background: 'var(--surface-brand-soft)', color: 'var(--brand)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginTop: 2,
              }}>
                <Icon name="sparkles" size={12} />
              </div>
              <div style={{
                flex: 1, minWidth: 0,
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: '4px 14px 14px 14px',
                padding: '10px 12px',
              }}>
                {t.pending ? <Typing /> : (
                  <>
                    {/* What the system understood, above the answer it gives.
                        This is the mitigation for a silently dropped filter. */}
                    {t.echo && (
                      <div style={{
                        fontSize: 11, color: 'var(--text-muted)',
                        marginBottom: 7, paddingBottom: 7,
                        borderBottom: '1px solid var(--border)',
                        display: 'flex', alignItems: 'center', gap: 5,
                      }}>
                        <Icon name="search" size={11} />
                        {t.echo}
                      </div>
                    )}
                    {t.ok ? <Answer result={t.result} /> : <Line muted>{t.message}</Line>}
                    {t.ok && t.job && t.result?.reason !== 'greeting_or_closing' && (
                      <Feedback rated={t.rated} onRate={(v) => rate(i, t.job, v)} />
                    )}
                  </>
                )}
              </div>
            </div>

            {/* Refinements of the latest answer. Offered by the server, run
                without the model — they cannot misroute, and they answer at
                once. Only under the newest turn: that is where refining happens. */}
            {i === turns.length - 1 && !t.pending && t.ok && t.followups?.length > 0 && (
              <div role="group" aria-label="Follow-up questions"
                   style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginLeft: 32 }}>
                {t.followups.map((f) => (
                  <Chip key={f.label} small disabled={busy} onClick={() => follow(f)}>
                    {f.label}
                  </Chip>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Composer */}
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
            placeholder="Ask about your caseload…"
            disabled={busy}
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
          disabled={busy || !question.trim()}
          aria-label="Send"
          style={{
            flex: 'none', display: 'flex', alignItems: 'center',
            justifyContent: 'center', width: 38, height: 38,
            cursor: busy || !question.trim() ? 'default' : 'pointer',
            opacity: busy || !question.trim() ? 0.4 : 1,
            background: 'var(--brand)', color: 'var(--text-on-brand)',
            border: 'none', borderRadius: '50%',
            transition: 'opacity var(--dur-fast) var(--ease-out)',
          }}
        >
          <Icon name="arrow-up" size={17} />
        </button>
      </form>

      {question.length > MAX_QUESTION - 30 && (
        <div style={{
          fontSize: 11, color: 'var(--text-muted)', textAlign: 'right',
          padding: '0 14px 8px',
        }}>{MAX_QUESTION - question.length} characters left</div>
      )}
    </div>
  );
}
