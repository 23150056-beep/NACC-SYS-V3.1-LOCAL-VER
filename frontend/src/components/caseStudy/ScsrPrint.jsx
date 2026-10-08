import { PAP_ROWS } from '../../config/scsr';
import {
  PAP_SIDES, TICK_SENTENCES, ageAtText, blocksFor, canonical, isBlank, longDate, partOneRows,
  partialDate, recordNotes, todayIso,
} from './model';

/* What Print puts on paper while the Case study tab is open: the Social Case
 * Study Report, in the template's order and numbering
 * (docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx).
 *
 * It prints what is SAVED, not what is half-typed in a box, and says DRAFT at
 * the top until the case study is final. Conventions are PsychReportPrint's:
 * a standard layout, a serif page, no <header>/<footer> elements (index.css
 * hides those when printing), and anything the record does not hold printed
 * as ruled lines to complete by hand. Headings stay with the text that follows
 * them (break-after: avoid) but a long box is allowed to run over a page:
 * keeping whole sections together would leave half-empty pages.
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

export default function ScsrPrint({ child, study, agency, license, preparedBy, className = 'racco-print-only' }) {
  const facts = study.record_facts;
  const stored = new Map((study.sections || []).map((s) => [s.key, s]));
  const blocks = blocksFor(child, study);
  const draft = study.status !== 'final';
  const heads = (agency?.agency_name || '').trim();
  const licenseNumber = (license?.license_number || '').trim();
  const licenseUntil = license?.license_valid_until ? longDate(license.license_valid_until) : '';
  const headName = (agency?.head_of_office_name || '').trim();
  const headTitle = (agency?.head_of_office_title || '').trim();

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
        {(agency?.office_address || '').trim() && <div style={{ fontSize: '10pt', whiteSpace: 'pre-line' }}>{agency.office_address.trim()}</div>}
        {(agency?.contact_details || '').trim() && <div style={{ fontSize: '10pt', whiteSpace: 'pre-line' }}>{agency.contact_details.trim()}</div>}
        <div style={{ fontSize: '14pt', fontWeight: 700, marginTop: '12pt', letterSpacing: '0.06em' }}>SOCIAL CASE STUDY REPORT</div>
        <div style={{ fontSize: '9pt', marginTop: '2pt' }}>(Applicable for all categories: Regular, relative, step-parent, adult, independent placement.)</div>
        <div style={{ fontSize: '9pt', fontWeight: 700, marginTop: '2pt' }}>CONFIDENTIAL</div>
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: '8pt', marginBottom: '4pt' }}>
        <strong>Date prepared:</strong>
        {study.date_prepared
          ? <span>{longDate(study.date_prepared)}</span>
          : <span style={{ ...S.line, width: '40%', height: '14pt' }} aria-hidden="true" />}
      </div>

      {blocks.map((block) => (
        <div key={block.block}>
          <h2 style={S.block}>{block.block === 'A' ? 'THE CHILD/ADOPTEE' : block.title.toUpperCase()}</h2>

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
                Age is worked out as of {study.date_prepared ? 'the date prepared' : `${longDate(todayIso())}, because the date prepared is not set`}.
              </div>
            </div>
          )}

          {block.entries.map((entry) => {
            const row = stored.get(entry.key);
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
          <div style={{ fontWeight: 700 }}>{preparedBy || 'Social Worker'}</div>
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
        Confidential. Printed from the records of {heads || 'the agency'} on {longDate(todayIso())}
        {draft ? ' as a draft; sections left as lines are for the social worker to complete' : ''}.
        Release only to persons authorized under the Data Privacy Act of 2012.
      </div>
    </div>
  );
}
