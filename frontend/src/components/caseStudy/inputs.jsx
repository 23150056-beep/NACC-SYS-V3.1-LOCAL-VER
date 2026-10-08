import { useEffect, useLayoutEffect, useRef } from 'react';
import { Button, FormField, Icon, Input, Select, iconBtn } from '../../ui';
import { PAP_ROWS } from '../../config/scsr';
import {
  PAP_SIDES, PROSE_MAX, ROWS_MAX, TICK_SENTENCES, ageAtText, blankRow, todayIso,
} from './model';

/* One input per kind of box. Each is controlled: it is handed the WORKING COPY
 * of the box (model.js, every field a string) and returns the next one through
 * onChange. None of them saves, validates or knows about versions; that is the
 * section card's job, and the server's. */

const CELL = {
  width: '100%', minWidth: 0, height: 34, padding: '0 9px', border: '1px solid var(--border-strong)',
  borderRadius: 'var(--radius-md)', background: 'var(--surface)', color: 'var(--text-strong)',
  fontFamily: 'var(--font-sans)', fontSize: 13,
};
const TH = {
  textAlign: 'left', padding: '7px 8px', fontSize: 11, fontWeight: 800, letterSpacing: '0.04em',
  textTransform: 'uppercase', color: 'var(--text-muted)', background: 'var(--ink-50)',
  borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap',
};
const TD = { padding: '5px 6px', borderBottom: '1px solid var(--divider-row)', verticalAlign: 'top' };

const iconButton = (color, disabled) => ({ ...iconBtn(color, 32), opacity: disabled ? 0.4 : 1, cursor: disabled ? 'not-allowed' : 'pointer' });

const fit = (el) => {
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight + 2}px`;
};

/* ------------------------------- prose ------------------------------- */
export function ProseInput({ value, onChange, label, disabled }) {
  const ref = useRef(null);
  // Grows with what is typed. A box inside the hidden tab has no width, so it
  // is fitted again the moment it gets one.
  useLayoutEffect(() => { if (ref.current) fit(ref.current); }, [value]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    let width = el.clientWidth;
    const watcher = new ResizeObserver(() => {
      if (el.clientWidth !== width) { width = el.clientWidth; fit(el); }
    });
    watcher.observe(el);
    return () => watcher.disconnect();
  }, []);
  return (
    <div>
      <textarea
        ref={ref} value={value} disabled={disabled} maxLength={PROSE_MAX}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        style={{ display: 'block', width: '100%', minHeight: 140, maxHeight: '70vh', resize: 'vertical', overflowY: 'auto', padding: '11px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', background: 'var(--surface)', color: 'var(--text-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.55 }}
      />
      <div style={{ textAlign: 'right', fontSize: 11.5, color: 'var(--text-muted)', marginTop: 3 }}>
        {value.length.toLocaleString()} of {PROSE_MAX.toLocaleString()} characters
      </div>
    </div>
  );
}

/* ------------------------------- list ------------------------------- */
export function ListInput({ entry, value, onChange, disabled }) {
  const refs = useRef([]);
  const focusAfter = useRef(null);
  useEffect(() => {
    if (focusAfter.current != null) {
      refs.current[focusAfter.current]?.focus();
      focusAfter.current = null;
    }
  });
  const rows = value.length ? value : [''];
  const set = (i, text) => onChange(rows.map((r, j) => (j === i ? text : r)));
  const insertAfter = (i) => {
    if (rows.length >= ROWS_MAX) return;
    focusAfter.current = i + 1;
    onChange([...rows.slice(0, i + 1), '', ...rows.slice(i + 1)]);
  };
  const remove = (i) => onChange(rows.length === 1 ? [''] : rows.filter((_, j) => j !== i));
  const move = (i, by) => {
    const next = [...rows];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    focusAfter.current = i + by;
    onChange(next);
  };
  return (
    <div>
      <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {rows.map((line, i) => (
          <li key={i} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span aria-hidden="true" style={{ width: 22, textAlign: 'right', fontSize: 12.5, color: 'var(--text-muted)' }}>{i + 1}.</span>
            <input
              ref={(el) => { refs.current[i] = el; }} type="text" value={line} disabled={disabled}
              aria-label={`${entry.title}, line ${i + 1}`} maxLength={500}
              onChange={(e) => set(i, e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); insertAfter(i); } }}
              style={CELL}
            />
            <button type="button" style={iconButton('var(--text-muted)', disabled || i === 0)} disabled={disabled || i === 0}
              aria-label={`Move line ${i + 1} up`} onClick={() => move(i, -1)}><Icon name="arrow-up" size={14} /></button>
            <button type="button" style={iconButton('var(--text-muted)', disabled || i === rows.length - 1)} disabled={disabled || i === rows.length - 1}
              aria-label={`Move line ${i + 1} down`} onClick={() => move(i, 1)}><Icon name="arrow-down" size={14} /></button>
            <button type="button" style={iconButton('var(--red-600)', disabled)} disabled={disabled}
              aria-label={`Remove line ${i + 1}`} onClick={() => remove(i)}><Icon name="x" size={14} /></button>
          </li>
        ))}
      </ol>
      <div style={{ marginTop: 8 }}>
        <Button size="sm" variant="secondary" disabled={disabled || rows.length >= ROWS_MAX}
          onClick={() => insertAfter(rows.length - 1)} iconLeft={<Icon name="plus" size={14} />}>Add line</Button>
      </div>
    </div>
  );
}

/* ------------------------------- table ------------------------------- */
const COLUMN_WIDTH = { int: 76, date: 150, partial_date: 150, choice: 170, text: 160 };

function CellInput({ column, label, value, onChange, disabled }) {
  if (column.type === 'choice') {
    return (
      <Select size="sm" value={value} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {column.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    );
  }
  if (column.type === 'date') {
    return <input type="date" style={CELL} value={value} max={todayIso()} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.value)} />;
  }
  if (column.type === 'partial_date') {
    return <input type="text" style={CELL} value={value} disabled={disabled} aria-label={label} placeholder="YYYY, YYYY-MM or YYYY-MM-DD" maxLength={10} onChange={(e) => onChange(e.target.value)} />;
  }
  if (column.type === 'int') {
    return <input type="text" inputMode="numeric" style={CELL} value={value} disabled={disabled} aria-label={label} maxLength={3} onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))} />;
  }
  return <input type="text" style={CELL} value={value} disabled={disabled} aria-label={label} maxLength={500} onChange={(e) => onChange(e.target.value)} />;
}

export function TableInput({ entry, value, onChange, disabled }) {
  const rows = value;
  const setCell = (i, key, text) => onChange(rows.map((r, j) => (j === i ? { ...r, [key]: text } : r)));
  const hasPartial = entry.columns.some((c) => c.type === 'partial_date');
  const width = entry.columns.reduce((sum, c) => sum + (COLUMN_WIDTH[c.type] || 160), 44);
  return (
    <div>
      <div className="racco-scroll" style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
        <table style={{ width: '100%', minWidth: width, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {entry.columns.map((c) => <th key={c.key} scope="col" style={TH}>{c.label}</th>)}
              <th scope="col" style={{ ...TH, width: 44 }}><span className="racco-sr-only">Remove row</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={entry.columns.length + 1} style={{ ...TD, padding: 12, color: 'var(--text-muted)', fontSize: 13 }}>No rows yet. Use Add row.</td></tr>
            )}
            {rows.map((row, i) => (
              <tr key={i}>
                {entry.columns.map((c) => (
                  <td key={c.key} style={{ ...TD, minWidth: COLUMN_WIDTH[c.type] || 160 }}>
                    <CellInput column={c} label={`${c.label} row ${i + 1}`} value={row[c.key]} disabled={disabled}
                      onChange={(text) => setCell(i, c.key, text)} />
                  </td>
                ))}
                <td style={TD}>
                  <button type="button" style={iconButton('var(--red-600)', disabled)} disabled={disabled}
                    aria-label={`Remove row ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                    <Icon name="x" size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasPartial && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 5 }}>Year, year-month or full date, for example 2019, 2019-05 or 2019-05-14.</div>}
      <div style={{ marginTop: 8 }}>
        <Button size="sm" variant="secondary" disabled={disabled || rows.length >= ROWS_MAX}
          onClick={() => onChange([...rows, blankRow(entry)])} iconLeft={<Icon name="plus" size={14} />}>Add row</Button>
      </div>
    </div>
  );
}

/* ------------------------------- PAP table ------------------------------- */
export function PapInput({ value, onChange, custodianName, disabled }) {
  const set = (side, id, text) => onChange({ ...value, [side]: { ...value[side], [id]: text } });
  // The first name cell that is still empty, female column first.
  const target = PAP_SIDES.find(({ side }) => !value[side].full_name.trim());
  const useCustodian = () => { if (target) set(target.side, 'full_name', custodianName); };
  return (
    <div>
      {custodianName && (
        <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <Button size="sm" variant="secondary" disabled={disabled || !target} onClick={useCustodian}
            iconLeft={<Icon name="user" size={14} />}>Use the custodian&apos;s name</Button>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {target
              ? `Fills the ${target.label} name with “${custodianName}”, the custodian on the record.`
              : 'Both names are already filled in.'}
          </span>
        </div>
      )}
      <div className="racco-scroll" style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
        <table style={{ width: '100%', minWidth: 600, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th scope="col" style={TH}><span className="racco-sr-only">Detail</span></th>
              {PAP_SIDES.map((s) => <th key={s.side} scope="col" style={TH}>{s.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {PAP_ROWS.map((r) => (
              <tr key={r.id}>
                <th scope="row" style={{ ...TD, textAlign: 'left', fontSize: 12.5, fontWeight: 700, color: 'var(--text-body)', width: '34%', paddingTop: 11 }}>{r.label}</th>
                {PAP_SIDES.map((s) => (
                  <td key={s.side} style={TD}>
                    <input
                      type={r.id === 'date_of_birth' ? 'date' : 'text'} style={CELL} value={value[s.side][r.id]}
                      max={r.id === 'date_of_birth' ? todayIso() : undefined} maxLength={r.id === 'date_of_birth' ? undefined : 500}
                      disabled={disabled} aria-label={`${r.label}, ${s.label}`}
                      onChange={(e) => set(s.side, r.id, e.target.value)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------- date ------------------------------- */
export function DateInput({ entry, value, onChange, disabled }) {
  return (
    <FormField label="Date" style={{ maxWidth: 240 }}>
      <Input type="date" max={todayIso()} value={value} disabled={disabled} aria-label={entry.title} onChange={(e) => onChange(e.target.value)} />
    </FormField>
  );
}

/* ------------------------------- tick ------------------------------- */
export function TickInput({ entry, value, onChange, disabled }) {
  return (
    <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: disabled ? 'default' : 'pointer' }}>
      <input type="checkbox" checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)}
        style={{ width: 18, height: 18, marginTop: 2, flex: 'none' }} />
      <span style={{ fontSize: 13.5, lineHeight: 1.55, color: 'var(--text-body)' }}>{TICK_SENTENCES[entry.key] || entry.title}</span>
    </label>
  );
}

/* ------------------------------- measurements ------------------------------- */
export function MeasurementsInput({ value, onChange, disabled }) {
  const set = (k, text) => onChange({ ...value, [k]: text });
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
      <FormField label="Height (cm)" hint="30 to 250">
        <Input inputMode="decimal" value={value.height_cm} disabled={disabled} maxLength={6} onChange={(e) => set('height_cm', e.target.value.replace(/[^\d.]/g, ''))} />
      </FormField>
      <FormField label="Weight (kg)" hint="2 to 250">
        <Input inputMode="decimal" value={value.weight_kg} disabled={disabled} maxLength={6} onChange={(e) => set('weight_kg', e.target.value.replace(/[^\d.]/g, ''))} />
      </FormField>
      <FormField label="Date measured">
        <Input type="date" max={todayIso()} value={value.measured_on} disabled={disabled} onChange={(e) => set('measured_on', e.target.value)} />
      </FormField>
    </div>
  );
}

/* ------------------------------- placement ------------------------------- */
export function PlacementInput({ value, onChange, birthDate, disabled }) {
  const set = (k, text) => onChange({ ...value, [k]: text });
  const age = ageAtText(birthDate, value.entrustment_date);
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12 }}>
      <FormField label="Date of matching (optional)">
        <Input type="date" max={todayIso()} value={value.matching_date} disabled={disabled} onChange={(e) => set('matching_date', e.target.value)} />
      </FormField>
      <FormField label="RACCO or CPA that placed the child">
        <Input value={value.racco_cpa} maxLength={500} disabled={disabled} onChange={(e) => set('racco_cpa', e.target.value)} />
      </FormField>
      <FormField label="Date accepted by the adopters (optional)">
        <Input type="date" max={todayIso()} value={value.accepted_date} disabled={disabled} onChange={(e) => set('accepted_date', e.target.value)} />
      </FormField>
      <FormField label="Date of entrustment" required>
        <Input type="date" max={todayIso()} value={value.entrustment_date} disabled={disabled} onChange={(e) => set('entrustment_date', e.target.value)} />
      </FormField>
      <div>
        <div style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)', marginBottom: 6 }}>Age at entrustment</div>
        <div data-testid="age-at-entrustment" style={{ minHeight: 'var(--field-h)', display: 'flex', alignItems: 'center', fontSize: 15, color: age ? 'var(--text-strong)' : 'var(--text-muted)' }}>
          {age || 'Worked out from the date of birth once the date of entrustment is set.'}
        </div>
      </div>
    </div>
  );
}
