import { Link, useNavigate } from 'react-router-dom';
import { Badge, roleLabel } from '../../ui';
import { gapMeta, gapTarget } from '../../config/careGaps';
import { clockRange } from '../../utils/time';
import BriefFacts from '../BriefFacts';

/* What the assistant answered, drawn from the plain data the server sent.
 *
 * Nothing rendered here is model prose - every name, date and count came out of
 * the database under the caller's own scope. That is what makes an invented
 * child name impossible rather than merely unlikely.
 *
 * Lists are lists (ul/li) so a screen reader says how many rows there are, and
 * a child's name is a link to the record wherever the payload carries the id.
 * `onNavigate` runs before any link is followed: below 640px the panel covers
 * the page, so it has to get out of the way of where the link goes. */

const NO_LIST = { listStyle: 'none', margin: 0, padding: 0 };
const NAME_LINK = { fontWeight: 700, color: 'var(--text-strong)' };

export function Line({ children, muted = false }) {
  return (
    <div style={{
      fontSize: 13.5,
      lineHeight: 1.55,
      color: muted ? 'var(--text-muted)' : 'var(--text-body)',
      padding: '2px 0',
    }}>{children}</div>
  );
}

function Pill({ children }) {
  return (
    <span style={{
      marginLeft: 6, padding: '1px 6px', borderRadius: 'var(--radius-pill)',
      fontSize: 12, fontWeight: 700, textTransform: 'capitalize',
      background: 'var(--ink-50)', color: 'var(--text-muted)',
    }}>{children}</span>
  );
}

function ChildLink({ id, name, onNavigate, style = NAME_LINK }) {
  if (!id) return <strong>{name}</strong>;
  return <Link to={`/report/child/${id}`} onClick={onNavigate} style={style}>{name}</Link>;
}

function Items({ children }) {
  return <ul style={NO_LIST}>{children}</ul>;
}

function Row({ children }) {
  return <li style={{ padding: '2px 0' }}><Line>{children}</Line></li>;
}

function Breakdown({ result, onNavigate }) {
  // get_statistics. A plain total is a figure, styled like the headcount
  // answer so every "how many" reads as the same kind of answer. A breakdown
  // is a table with a thin bar per row: the table carries every value and is
  // what a screen reader gets; the bars are decorative and only help the eye
  // compare. One hue for one series, --blue-500 because it passes the palette
  // checks against this surface where --brand's darker step does not.
  const rows = result.rows || [];
  const max = Math.max(1, ...rows.map((r) => r.count));
  // A figure, when the server sends one, is what the question asked for when
  // that is not a count - "how long do children wait?" is in days.
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
      {/* The screen this number comes from, when the reader can open it - the
          check on a number that looks wrong is one click away. */}
      {result.screen && (
        <div style={{ marginTop: 6 }}>
          <Link to={result.screen.path} onClick={onNavigate} style={{
            fontSize: 12, fontWeight: 600, color: 'var(--brand)',
          }}>Open {result.screen.label}</Link>
        </div>
      )}
    </div>
  );
}

function Availability({ result }) {
  if (!result.items.length) return <Line muted>No free slots in that period.</Line>;
  return (
    <Items>
      {result.items.map((s, i) => (
        <Row key={i}>
          <strong>{s.psychologist}</strong>
          <span style={{ color: 'var(--text-muted)' }}>
            {' '}· {s.weekday} {s.date} · {clockRange(s.start, s.end)}
          </span>
          {/* Places left, not just "free": a window with one place is a
              different answer from a window with four. */}
          <span style={{
            marginLeft: 6, padding: '1px 6px', borderRadius: 'var(--radius-pill)',
            fontSize: 12, fontWeight: 700,
            background: 'var(--ink-50)', color: 'var(--text-muted)',
          }}>{s.remaining} left</span>
        </Row>
      ))}
    </Items>
  );
}

function PeopleCount({ result }) {
  // Colleagues, not children. Rendered like the child count so the two read as
  // the same kind of answer - the bug this tool fixes was one being served as
  // the other.
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

function Appointments({ result }) {
  if (!result.items.length) {
    // "Nothing scheduled" is the wrong sentence for "who did I see
    // yesterday?" - nothing was scheduled is not nothing happened.
    const past = ['yesterday', 'last_week', 'last_month', 'last_year'].includes(result.when);
    return <Line muted>{past ? 'Nothing recorded.' : 'Nothing scheduled.'}</Line>;
  }
  // Staff and administrators hold no sessions, so they get the agency's
  // calendar - said out loud, with whose session each row is, because the same
  // question answers differently for a psychologist.
  const agency = result.scope === 'agency';
  return (
    <>
      {agency && <Line muted>Across the agency:</Line>}
      <Items>
        {result.items.map((a, i) => (
          <Row key={i}>
            <strong>{a.child}</strong>
            <span style={{ color: 'var(--text-muted)' }}>
              {' '}· {a.when} · {a.purpose}{agency && a.psychologist ? ` · ${a.psychologist}` : ''}
            </span>
            {a.status && a.status !== 'scheduled' && <Pill>{a.status.replace('_', ' ')}</Pill>}
          </Row>
        ))}
      </Items>
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

function Children({ result, onNavigate }) {
  if (result.items.length) {
    return (
      <Items>
        {result.items.map((c) => (
          <li key={c.id} style={{ padding: '3px 0' }}>
            <Line>
              <ChildLink id={c.id} name={c.name} onNavigate={onNavigate} />
            </Line>
            {/* The problem that matched, so the list can be read without
                opening every record. */}
            {c.problems?.length > 0 && (
              <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-muted)' }}>
                {c.problems.join(' · ')}
              </div>
            )}
          </li>
        ))}
      </Items>
    );
  }
  // No match. The tool says what "none" means for the question that was asked
  // - this branch renders more than one tool's list, and the concern search's
  // sentence answered "which children have no psychologist?" with "no open
  // concern matches that wording". What the agency records arrives as buttons
  // under the answer, which search for exactly that without the model.
  return (
    <>
      <Line muted>{result.empty || 'No open concern matches that wording.'}</Line>
      {result.available?.length > 0 && (
        <Line muted>These concerns are recorded in your caseload. Choose one to search for it.</Line>
      )}
    </>
  );
}

function CareGaps({ result, role, onNavigate }) {
  // A social worker's answer brings its own empty sentence: their rules are
  // not about anything being overdue.
  if (!result.groups.length) return <Line muted>{result.empty || 'Nobody is overdue.'}</Line>;
  return (
    <>
      <Items>
        {result.groups.map((group) => (
          <li key={group.child_id ?? group.child} style={{ padding: '6px 0', borderBottom: '1px solid var(--ink-50)' }}>
            <Line>
              <ChildLink id={group.child_id} name={group.child} onNavigate={onNavigate} />
            </Line>
            <Items>
              {group.gaps.map((g) => {
                const meta = gapMeta(g.type, g.severity, role);
                return (
                  <li key={g.type} style={{ padding: '1px 0 1px 10px', fontSize: 13, lineHeight: 1.5, color: 'var(--text-body)' }}>
                    {/* The sentence the Dashboard shows, not the internal slug. */}
                    {g.message || g.type}
                    {group.child_id && (
                      <>
                        <span style={{ color: 'var(--text-muted)' }}> · </span>
                        <Link to={gapTarget({ ...g, child_id: group.child_id }, role)} onClick={onNavigate}
                              aria-label={`${meta.action}: ${group.child}`}
                              style={{ fontSize: 12, fontWeight: 600, color: 'var(--brand)' }}>{meta.action}</Link>
                      </>
                    )}
                  </li>
                );
              })}
            </Items>
          </li>
        ))}
      </Items>
      {/* The ISA's list is the whole agency. A cut-off list must never read as
          the whole list. */}
      {result.total > result.groups.length && (
        <Line muted>
          Showing {result.groups.length} of {result.total} children
          ({result.gap_total} things to do in all). The Dashboard lists the rest.
        </Line>
      )}
      {result.total <= result.groups.length && result.gap_total > 1 && (
        <Line muted>{result.gap_total} things to do across {result.total} {result.total === 1 ? 'child' : 'children'}.</Line>
      )}
    </>
  );
}

function Summary({ result, onNavigate }) {
  const navigate = useNavigate();
  const go = (to) => { onNavigate?.(); navigate(to); };
  if (result.match === 'none') {
    return (
      <Line muted>
        {result.name ? <>No child of yours matches “{result.name}”.</> : 'That child is not in your records.'}
      </Line>
    );
  }
  if (result.match === 'several') {
    return (
      <>
        <Line muted>Several children match - which one?</Line>
        <Items>
          {result.items.map((c) => (
            <Row key={c.id}>
              <ChildLink id={c.id} name={c.name} onNavigate={onNavigate} />
              {c.ref && <span className="racco-mono" style={{ color: 'var(--text-muted)' }}> · {c.ref}</span>}
            </Row>
          ))}
        </Items>
      </>
    );
  }
  const { child, facts, link } = result;
  return (
    <>
      <Line>
        <strong>{child.name}</strong>
        <span style={{ color: 'var(--text-muted)' }}> · {child.status}</span>
        {link && (
          <>
            <span style={{ color: 'var(--text-muted)' }}> · </span>
            <Link to={link.path} onClick={onNavigate}
                  style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--brand)' }}>{link.label}</Link>
          </>
        )}
      </Line>
      {/* The facts the brief panel opens with: the same dict, the same scope
          and the same carry-history rules, with the record's social worker's
          next steps. */}
      <div style={{ marginTop: 8 }}>
        <BriefFacts facts={facts} onGo={go} badgeSize="md"
                    onOpenOverview={() => go(`/report/child/${child.id}?tab=overview`)} />
      </div>
      {result.remarks?.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div className="racco-eyebrow">Latest case notes</div>
          <Items>
            {result.remarks.map((r, i) => (
              <li key={i}><Line muted>{r.date}: {r.text}</Line></li>
            ))}
          </Items>
        </div>
      )}
    </>
  );
}

function Flags({ result, onNavigate }) {
  if (!result.items.length) return <Line muted>No flagged self-reports.</Line>;
  return (
    <>
      <Items>
        {result.items.map((f, i) => (
          <li key={i} style={{
            padding: '10px 12px', borderRadius: 'var(--radius-md)', marginBottom: 10,
            background: 'var(--amber-50)', border: '1px solid var(--border)',
          }}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>
              <ChildLink id={f.child_id} name={f.child} onNavigate={onNavigate} />
              {f.reviewed && <Badge tone="neutral" style={{ marginLeft: 6 }}>reviewed</Badge>}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              {f.question}
            </div>
            {/* The child's own words, quoted. A flag without them is not
                something anyone can act on. */}
            <div style={{ fontSize: 13, marginTop: 4, fontStyle: 'italic' }}>
              “{f.answer}”
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
              {f.date}
            </div>
          </li>
        ))}
      </Items>
      {/* Never let a truncated list read as the whole list - these are
          children reporting distress. */}
      {result.total > result.items.length && (
        <Line muted>
          Showing {result.items.length} of {result.total}. Open Progress
          Monitoring to read the rest.
        </Line>
      )}
    </>
  );
}

export default function Answer({ result, role, onNavigate }) {
  switch (result?.kind) {
    case 'breakdown': return <Breakdown result={result} onNavigate={onNavigate} />;
    case 'availability': return <Availability result={result} />;
    case 'people_count': return <PeopleCount result={result} />;
    case 'appointments': return <Appointments result={result} />;
    case 'children': return <Children result={result} onNavigate={onNavigate} />;
    case 'care_gaps': return <CareGaps result={result} role={role} onNavigate={onNavigate} />;
    case 'summary': return <Summary result={result} onNavigate={onNavigate} />;
    case 'self_report_flags': return <Flags result={result} onNavigate={onNavigate} />;
    case 'message': return <Line muted>{result.text}</Line>;
    default: return null;
  }
}
