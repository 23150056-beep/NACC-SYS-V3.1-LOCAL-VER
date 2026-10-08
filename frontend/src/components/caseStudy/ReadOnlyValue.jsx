import { PAP_ROWS } from '../../config/scsr';
import { PAP_SIDES, TICK_SENTENCES, ageAtText, canonical, isBlank, longDate, partialDate } from './model';

/* A box as it stands, for reading: the psychologist's view of block A, a social
 * worker's closed or final case study, and "the other version" a conflict
 * puts beside yours. Nothing here can be changed or saved. */

const MUTED = { color: 'var(--text-muted)', fontSize: 13, fontStyle: 'italic' };
const TEXT = { fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-body)', overflowWrap: 'anywhere' };
const TH = { textAlign: 'left', padding: '7px 10px', fontSize: 11, fontWeight: 800, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--text-muted)', background: 'var(--ink-50)', borderBottom: '1px solid var(--border)' };
const TD = { padding: '7px 10px', fontSize: 13, color: 'var(--text-body)', borderBottom: '1px solid var(--divider-row)', verticalAlign: 'top', overflowWrap: 'anywhere' };

function cell(column, value) {
  if (!value) return '—';
  if (column.type === 'choice') return column.options.find((o) => o.value === value)?.label || value;
  if (column.type === 'partial_date') return partialDate(value);
  if (column.type === 'date') return longDate(value);
  return value;
}

export default function ReadOnlyValue({ entry, value, notApplicable = false, birthDate = null }) {
  if (notApplicable) return <p style={{ ...TEXT, margin: 0 }}>Not applicable.</p>;
  if (isBlank(entry, value)) return <p style={{ ...MUTED, margin: 0 }}>Nothing written yet.</p>;
  const v = canonical(entry, value);

  switch (entry.kind) {
    case 'prose':
      return <div style={{ ...TEXT, whiteSpace: 'pre-wrap' }}>{v}</div>;
    case 'list':
      return <ol style={{ ...TEXT, margin: 0, paddingLeft: 22 }}>{v.map((line, i) => <li key={i}>{line}</li>)}</ol>;
    case 'table':
      return (
        <div className="racco-scroll" role="region" tabIndex={0} aria-label={`${entry.title}, table`} style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: entry.columns.length > 4 ? 640 : 0 }}>
            <thead><tr>{entry.columns.map((c) => <th key={c.key} scope="col" style={TH}>{c.label}</th>)}</tr></thead>
            <tbody>
              {v.map((row, i) => (
                <tr key={i}>{entry.columns.map((c) => <td key={c.key} style={TD}>{cell(c, row[c.key])}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'pap_table':
      return (
        <div className="racco-scroll" role="region" tabIndex={0} aria-label="Prospective adoptive parents, table" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 520 }}>
            <thead><tr><th scope="col" style={TH}><span className="racco-sr-only">Detail</span></th>
              {PAP_SIDES.map((s) => <th key={s.side} scope="col" style={TH}>{s.label}</th>)}</tr></thead>
            <tbody>
              {PAP_ROWS.map((r) => (
                <tr key={r.id}>
                  <th scope="row" style={{ ...TD, textAlign: 'left', fontWeight: 700, width: '36%' }}>{r.label}</th>
                  {PAP_SIDES.map((s) => (
                    <td key={s.side} style={TD}>{r.id === 'date_of_birth' && v[s.side][r.id] ? longDate(v[s.side][r.id]) : (v[s.side][r.id] || '—')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'date':
      return <p style={{ ...TEXT, margin: 0 }}>{longDate(v)}</p>;
    case 'tick':
      return (
        <p style={{ ...TEXT, margin: 0, display: 'flex', gap: 8 }}>
          <span aria-hidden="true">&#9745;</span>
          <span>{TICK_SENTENCES[entry.key] || entry.title}</span>
        </p>
      );
    case 'measurements':
      return (
        <dl style={{ ...TEXT, margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 14px' }}>
          <dt style={{ fontWeight: 700 }}>Height</dt><dd style={{ margin: 0 }}>{v.height_cm ? `${v.height_cm} cm` : '—'}</dd>
          <dt style={{ fontWeight: 700 }}>Weight</dt><dd style={{ margin: 0 }}>{v.weight_kg ? `${v.weight_kg} kg` : '—'}</dd>
          <dt style={{ fontWeight: 700 }}>Measured on</dt><dd style={{ margin: 0 }}>{v.measured_on ? longDate(v.measured_on) : '—'}</dd>
        </dl>
      );
    case 'placement':
      return (
        <dl style={{ ...TEXT, margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 14px' }}>
          <dt style={{ fontWeight: 700 }}>Date of matching</dt><dd style={{ margin: 0 }}>{v.matching_date ? longDate(v.matching_date) : '—'}</dd>
          <dt style={{ fontWeight: 700 }}>RACCO / CPA</dt><dd style={{ margin: 0 }}>{v.racco_cpa || '—'}</dd>
          <dt style={{ fontWeight: 700 }}>Date accepted</dt><dd style={{ margin: 0 }}>{v.accepted_date ? longDate(v.accepted_date) : '—'}</dd>
          <dt style={{ fontWeight: 700 }}>Date of entrustment</dt><dd style={{ margin: 0 }}>{v.entrustment_date ? longDate(v.entrustment_date) : '—'}</dd>
          <dt style={{ fontWeight: 700 }}>Age at entrustment</dt><dd style={{ margin: 0 }}>{ageAtText(birthDate, v.entrustment_date) || '—'}</dd>
        </dl>
      );
    default:
      return null;
  }
}
