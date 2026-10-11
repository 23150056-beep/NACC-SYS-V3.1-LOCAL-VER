import { ADOPTION, PAP_ROWS } from '../../config/scsr';
import { clock } from '../../utils/time';
import {
  PAP_SIDES, TICK_SENTENCES, ageAtText, blockTitle, blocksFor, blocksForCopy, canonical,
  isAdoptionRecord, isBlank, longDate, partOneRows, partialDate, recordNotes, todayIso,
} from './model';

/* What Print puts on paper while the Case study tab is open: the Social Case
 * Study Report, in the template's order and numbering
 * (docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx).
 *
 * It prints one of two things:
 *
 *   a DRAFT   the case study as it is SAVED now (not what is half-typed in a
 *             box), read live from the record and the profiles, marked DRAFT;
 *   a FINAL   one `copy` of a final, whole: Part I, every box, the preparer's
 *             name and license and the agency's Head of Office as they were the
 *             day it was made final. Nothing is read from the live record or
 *             the profiles then, and there is no DRAFT marker - a license
 *             renewed or a Head of Office changed since must not alter a copy
 *             that was signed.
 *
 * Conventions are PsychReportPrint's:
 * a standard layout, a serif page, no <header>/<footer> elements (index.css
 * hides those when printing), and anything the record does not hold printed
 * as ruled lines to complete by hand. Headings stay with the text that follows
 * them (break-after: avoid) but a long box is allowed to run over a page:
 * keeping whole sections together would leave half-empty pages.
 *
 * A record that is not an adoption prints block A alone: the same agency
 * header, title and signature block, no adoption-only subtitle, and Part I with
 * "Case type" where an adoption has its type of adoption. A copy decides this
 * from the case type it was made with, so a copy signed while the record was an
 * adoption reprints as the adoption report even after the record has moved.
 *
 * Only the social worker who holds the record gets this print. The
 * psychologist and the ISA keep the page's ordinary Print.
 */

const S = {
  page: { fontFamily: "Georgia, 'Times New Roman', serif", fontSize: '11pt', lineHeight: 1.5, color: '#000' },
  block: { fontFamily: 'inherit', color: '#000', fontSize: '12pt', fontWeight: 700, textTransform: 'uppercase', margin: '20pt 0 6pt', letterSpacing: '0.03em', breakAfter: 'avoid', pageBreakAfter: 'avoid', borderBottom: '1.5px solid #000' },
  heading: { fontFamily: 'inherit', color: '#000', fontSize: '11pt', fontWeight: 700, margin: '14pt 0 4pt', breakAfter: 'avoid', pageBreakAfter: 'avoid' },
  cellK: { padding: '3pt 8pt', border: '1px solid #000', width: '34%', fontWeight: 700, verticalAlign: 'top' },
  cellV: { padding: '3pt 8pt', border: '1px solid #000', verticalAlign: 'top', overflowWrap: 'anywhere' },
  th: { padding: '3pt 8pt', border: '1px solid #000', textAlign: 'left', fontWeight: 700 },
  line: { borderBottom: '1px solid #000', height: '18pt' },
  p: { margin: '0 0 6pt', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
};

function Lines({ n = 4 }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: n }, (_, i) => <div key={i} style={S.line} />)}
    </div>
  );
}

function Box({ ticked }) {
  return (
    <span aria-hidden="true" style={{ display: 'inline-block', width: '9pt', height: '9pt', border: '1px solid #000', marginRight: '6pt', textAlign: 'center', lineHeight: '9pt', fontSize: '8pt', fontWeight: 700, verticalAlign: 'baseline', flex: 'none' }}>
      {ticked ? 'X' : ''}
    </span>
  );
}

const cellText = (column, value) => {
  if (!value) return '';
  if (column.type === 'choice') return column.options.find((o) => o.value === value)?.label || value;
  if (column.type === 'partial_date') return partialDate(value);
  if (column.type === 'date') return longDate(value);
  return value;
};

function Paragraphs({ text }) {
  return text.split(/\n{2,}/).map((chunk, i) => <p key={i} style={S.p}>{chunk}</p>);
}

function Table({ entry, rows }) {
  // An empty table prints its columns and a few empty rows to write in.
  const body = rows.length ? rows : Array.from({ length: 3 }, () => ({}));
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
      <thead>
        <tr>{entry.columns.map((c) => <th key={c.key} style={{ ...S.th, fontSize: entry.columns.length > 4 ? '8.5pt' : '10pt' }}>{c.label}</th>)}</tr>
      </thead>
      <tbody>
        {body.map((row, i) => (
          <tr key={i} style={{ breakInside: 'avoid' }}>
            {entry.columns.map((c) => (
              <td key={c.key} style={{ ...S.cellV, height: rows.length ? undefined : '20pt', fontSize: entry.columns.length > 4 ? '9pt' : undefined }}>
                {cellText(c, row[c.key])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PapTable({ value }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
      <thead>
        <tr>
          <th style={{ ...S.th, width: '34%' }}><span style={{ position: 'absolute', left: '-9999px' }}>Detail</span></th>
          {PAP_SIDES.map((s) => <th key={s.side} style={S.th}>{s.label}</th>)}
        </tr>
      </thead>
      <tbody>
        {PAP_ROWS.map((r) => (
          <tr key={r.id} style={{ breakInside: 'avoid' }}>
            <td style={{ ...S.cellK, fontSize: '9.5pt' }}>{r.label}</td>
            {PAP_SIDES.map((s) => (
              <td key={s.side} style={{ ...S.cellV, height: '18pt', fontSize: '10pt' }}>
                {r.id === 'date_of_birth' && value[s.side][r.id] ? longDate(value[s.side][r.id]) : (value[s.side][r.id] || '')}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Pairs({ rows }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} style={{ breakInside: 'avoid' }}><td style={S.cellK}>{k}</td><td style={{ ...S.cellV, height: '18pt' }}>{v}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

/* One box's body, by kind. */
function Body({ entry, row, facts }) {
  if (row?.not_applicable && entry.may_be_na) return <p style={S.p}>Not applicable.</p>;
  const blank = isBlank(entry, row?.value);
  const v = canonical(entry, row?.value);

  switch (entry.kind) {
    case 'prose':
      return blank ? <Lines n={4} /> : <Paragraphs text={v} />;
    case 'list':
      return blank ? <Lines n={3} /> : (
        <ol style={{ margin: 0, paddingLeft: '20pt', listStyle: 'decimal' }}>{v.map((line, i) => <li key={i} style={{ overflowWrap: 'anywhere' }}>{line}</li>)}</ol>
      );
    case 'table':
      return <Table entry={entry} rows={v} />;
    case 'pap_table':
      return <PapTable value={v} />;
    case 'date':
      return blank ? <div style={{ ...S.line, width: '60%' }} aria-hidden="true" /> : <p style={S.p}>{longDate(v)}</p>;
    case 'tick':
      return (
        <p style={{ ...S.p, display: 'flex' }}>
          <Box ticked={v === true} />
          <span>{TICK_SENTENCES[entry.key] || entry.title}</span>
        </p>
      );
    case 'measurements':
      return (
        <Pairs rows={[
          ['Height', v.height_cm ? `${v.height_cm} cm` : ''],
          ['Weight', v.weight_kg ? `${v.weight_kg} kg` : ''],
          ['Date measured', v.measured_on ? longDate(v.measured_on) : ''],
        ]} />
      );
    case 'placement':
      return (
        <Pairs rows={[
          ['Date of matching', v.matching_date ? longDate(v.matching_date) : ''],
          ['RACCO / CPA', v.racco_cpa || ''],
          ['Date accepted by the adopters', v.accepted_date ? longDate(v.accepted_date) : ''],
          ['Date of entrustment', v.entrustment_date ? longDate(v.entrustment_date) : ''],
          ['Age at entrustment', ageAtText(facts.birth_date, v.entrustment_date)],
        ]} />
      );
    default:
      return null;
  }
}

const text = (value) => (value || '').trim();

// A moment as the reader's calendar and clock say it, in the print's own style.
function momentText(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return `${longDate(day)}, ${clock(d)}`;
}

/* Everything the page needs, from the live case study (a draft) ... */
function liveView({ child, study, agency, license, preparedBy }) {
  const stored = new Map((study.sections || []).map((s) => [s.key, s]));
  return {
    draft: true,
    adoption: isAdoptionRecord(child),
    facts: study.record_facts,
    datePrepared: study.date_prepared,
    ageNote: study.date_prepared ? 'the date prepared' : `${longDate(todayIso())}, because the date prepared is not set`,
    blocks: blocksFor(child, study),
    rowOf: (key) => stored.get(key),
    agency: agency || {},
    preparer: {
      name: preparedBy || '',
      license_number: license?.license_number,
      license_valid_until: license?.license_valid_until,
    },
  };
}

/* ... or from one final copy, which holds all of it. */
function copyView(copy) {
  const snap = copy.snapshot;
  // A copy made before block A was every case type's has no case type in its
  // Part I, but its child block says: and none was anything but an adoption.
  const caseType = snap.part_one?.case_type || snap.child?.case_type || ADOPTION;
  return {
    draft: false,
    adoption: caseType === ADOPTION,
    facts: { ...snap.part_one, case_type: caseType },
    datePrepared: snap.date_prepared,
    ageNote: 'the date prepared',
    blocks: blocksForCopy(snap),
    rowOf: (key) => snap.sections[key],
    agency: snap.agency || {},
    preparer: snap.preparer || {},
    madeAt: copy.finalized_at,
  };
}

export default function ScsrPrint({ child, study, copy = null, agency, license, preparedBy, className = 'racco-print-only' }) {
  const view = copy ? copyView(copy) : liveView({ child, study, agency, license, preparedBy });
  const { draft, facts, blocks } = view;
  const heads = text(view.agency.agency_name);
  const licenseNumber = text(view.preparer.license_number);
  const licenseUntil = view.preparer.license_valid_until ? longDate(view.preparer.license_valid_until) : '';
  const headName = text(view.agency.head_of_office_name);
  const headTitle = text(view.agency.head_of_office_title);

  return (
    <div className={className} style={S.page}>
      {draft && (
        <div style={{ border: '2px solid #000', padding: '5pt 10pt', textAlign: 'center', fontWeight: 700, letterSpacing: '0.2em', marginBottom: '10pt' }}>
          DRAFT
          <div style={{ fontSize: '8.5pt', fontWeight: 400, letterSpacing: 0 }}>This case study has not been finalized. It prints what is saved so far.</div>
        </div>
      )}

      {/* Not <header>/<footer>: index.css hides those when printing. */}
      <div style={{ textAlign: 'center', marginBottom: '12pt' }}>
        {heads ? <div style={{ fontWeight: 700, fontSize: '12pt' }}>{heads}</div> : <div style={{ ...S.line, width: '60%', margin: '0 auto' }} aria-hidden="true" />}
        {text(view.agency.office_address) && <div style={{ fontSize: '10pt', whiteSpace: 'pre-line' }}>{text(view.agency.office_address)}</div>}
        {text(view.agency.contact_details) && <div style={{ fontSize: '10pt', whiteSpace: 'pre-line' }}>{text(view.agency.contact_details)}</div>}
        <div style={{ fontSize: '14pt', fontWeight: 700, marginTop: '12pt', letterSpacing: '0.06em' }}>SOCIAL CASE STUDY REPORT</div>
        {view.adoption && (
          <div style={{ fontSize: '9pt', marginTop: '2pt' }}>(Applicable for all categories: Regular, relative, step-parent, adult, independent placement.)</div>
        )}
        <div style={{ fontSize: '9pt', fontWeight: 700, marginTop: '2pt' }}>CONFIDENTIAL</div>
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: '8pt', marginBottom: '4pt' }}>
        <strong>Date prepared:</strong>
        {view.datePrepared
          ? <span>{longDate(view.datePrepared)}</span>
          : <span style={{ ...S.line, width: '40%', height: '14pt' }} aria-hidden="true" />}
      </div>

      {blocks.map((block) => (
        <div key={block.block}>
          <h2 style={S.block}>{blockTitle(block.block, view.adoption).toUpperCase()}</h2>

          {block.block === 'A' && (
            <div>
              <h3 style={S.heading}>I. Identifying Information</h3>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  {partOneRows(facts, longDate).map((r) => (
                    <tr key={r.short} style={{ breakInside: 'avoid' }}><td style={S.cellK}>{r.label}</td><td style={S.cellV}>{r.value || '—'}</td></tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontSize: '8.5pt', marginTop: '2pt' }}>
                Age is worked out as of {view.ageNote}.
              </div>
            </div>
          )}

          {block.entries.map((entry) => {
            const row = view.rowOf(entry.key);
            const notes = recordNotes(entry.key, facts, longDate);
            return (
              <div key={entry.key}>
                <h3 style={S.heading}>{entry.number}. {entry.title}</h3>
                {notes.length > 0 && (
                  <p style={{ ...S.p, fontSize: '10pt' }}>
                    {notes.map(([k, v]) => `${k}: ${v}`).join(' · ')}
                  </p>
                )}
                <Body entry={entry} row={row} facts={facts} />
              </div>
            );
          })}
        </div>
      ))}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '28pt', marginTop: '36pt', breakInside: 'avoid-page' }}>
        <div>
          <div>Prepared by:</div>
          <div style={{ ...S.line, marginTop: '28pt' }} />
          <div style={{ fontWeight: 700 }}>{text(view.preparer.name) || 'Social Worker'}</div>
          <div>Social Worker</div>
          <div>
            License No. {licenseNumber || '__________'}, valid until {licenseUntil || '__________'}
          </div>
        </div>
        <div>
          <div>Approved by:</div>
          <div style={{ ...S.line, marginTop: '28pt' }} />
          <div style={{ fontWeight: 700 }}>{headName || '______________________'}</div>
          <div>{headTitle || '______________________'}</div>
        </div>
      </div>

      <div style={{ marginTop: '24pt', fontSize: '8.5pt', borderTop: '1px solid #000', paddingTop: '4pt' }}>
        Confidential. {draft
          ? `Printed from the records of ${heads || 'the agency'} on ${longDate(todayIso())} as a draft; sections left as lines are for the social worker to complete.`
          : `Final copy made on ${momentText(view.madeAt)}, printed from the records of ${heads || 'the agency'} on ${longDate(todayIso())}.`}
        {' '}Release only to persons authorized under the Data Privacy Act of 2012.
      </div>
    </div>
  );
}
