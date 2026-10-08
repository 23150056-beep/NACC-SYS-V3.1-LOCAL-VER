import { useState } from 'react';
import { Badge, Button, Card, Icon } from '../../ui';
import { clock, exactDate } from '../../utils/time';
import { DateInput, ListInput, MeasurementsInput, PapInput, PlacementInput, ProseInput, TableInput, TickInput } from './inputs';
import ReadOnlyValue from './ReadOnlyValue';

/* One box of the case study: the template's guidance beside the input for its
 * kind, a Not applicable tick where the template allows one, who saved it last,
 * and its own Save. Everything that changes state is handed in; this component
 * only draws and reports what was done. */

// "3:40 PM" for typing from today, with the date for anything older.
const sameDay = (iso) => new Date(iso).toDateString() === new Date().toDateString();

const NOTE = { fontSize: 13, color: 'var(--text-muted)', margin: 0, lineHeight: 1.55 };

function Guidance({ hints, titleId }) {
  // Open beside the box on a wide window, folded away under it on a narrow one.
  const [open, setOpen] = useState(() => typeof window !== 'undefined'
    && !!window.matchMedia && window.matchMedia('(min-width: 961px)').matches);
  if (!hints.length) return null;
  return (
    <aside aria-label="Guidance" style={{ background: 'var(--ink-50)', borderRadius: 'var(--radius-md)', padding: '9px 12px' }}>
      <button type="button" aria-expanded={open} aria-controls={titleId} onClick={() => setOpen((o) => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 12, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
        Guidance <span style={{ fontWeight: 600, textTransform: 'none', letterSpacing: 0 }}>({hints.length})</span>
      </button>
      <ul id={titleId} style={{ margin: '8px 0 0', paddingLeft: 18, display: open ? 'flex' : 'none', flexDirection: 'column', gap: 6, fontSize: 12.5, lineHeight: 1.5, color: 'var(--text-body)' }}>
        {hints.map((h) => <li key={h}>{h}</li>)}
      </ul>
    </aside>
  );
}

function KindInput({ entry, edit, onChange, birthDate, custodianName, disabled }) {
  const label = `${entry.number}. ${entry.title}`;
  switch (entry.kind) {
    case 'prose': return <ProseInput label={label} value={edit.value} onChange={onChange} disabled={disabled} />;
    case 'list': return <ListInput entry={entry} value={edit.value} onChange={onChange} disabled={disabled} />;
    case 'table': return <TableInput entry={entry} value={edit.value} onChange={onChange} disabled={disabled} />;
    case 'pap_table': return <PapInput value={edit.value} onChange={onChange} custodianName={custodianName} disabled={disabled} />;
    case 'date': return <DateInput entry={entry} value={edit.value} onChange={onChange} disabled={disabled} />;
    case 'tick': return <TickInput entry={entry} value={edit.value} onChange={onChange} disabled={disabled} />;
    case 'measurements': return <MeasurementsInput value={edit.value} onChange={onChange} disabled={disabled} />;
    case 'placement': return <PlacementInput value={edit.value} onChange={onChange} birthDate={birthDate} disabled={disabled} />;
    default: return null;
  }
}

export default function SectionCard({
  entry, saved, edit, dirty, missing, kept, error, conflict, busy, readOnly,
  seed, notes, birthDate, custodianName,
  onChange, onNotApplicable, onSave, onRestore, onDiscard, onUseSeed, onLoadSaved, onKeepMine,
}) {
  const title = `${entry.number}. ${entry.title}`;
  const hintsId = `cs-hints-${entry.key}`;
  const savedLine = saved.version > 0
    ? `Saved${saved.updated_by_name ? ` by ${saved.updated_by_name}` : ''}, ${exactDate(saved.updated_at)}`
    : 'Not saved yet';
  const state = dirty ? <Badge tone="amber" size="sm" dot>Unsaved changes</Badge>
    : missing ? <Badge tone="neutral" size="sm" dot>To complete</Badge>
      : <Badge tone="success" size="sm" dot>Complete</Badge>;
  const showSeed = !readOnly && seed && !edit.notApplicable
    && (entry.kind === 'prose') && !String(edit.value).trim();

  return (
    <section id={`cs-sec-${entry.key}`} className="racco-cs-section" aria-label={title}>
      <Card title={title} actions={readOnly ? null : state} padding="14px 15px">
        <div className="racco-cs-body">
          <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 11 }}>
            {kept && !readOnly && (
              <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '8px 12px', borderRadius: 'var(--radius-md)', background: 'var(--amber-50)', border: '1px solid var(--amber-200)', fontSize: 13, color: 'var(--text-body)' }}>
                <Icon name="history" size={15} />
                <span style={{ flex: 1, minWidth: 180 }}>
                  Unsaved text from {sameDay(kept.saved_at) ? clock(kept.saved_at) : exactDate(kept.saved_at)}
                  {kept.base_version !== saved.version ? ' (this box has been saved by someone since)' : ''}
                </span>
                <Button size="sm" variant="secondary" onClick={onRestore}>Restore</Button>
                <Button size="sm" variant="ghost" onClick={onDiscard}>Discard</Button>
              </div>
            )}

            {entry.may_be_na && !readOnly && (
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13.5, fontWeight: 600, color: 'var(--text-body)', cursor: 'pointer', width: 'fit-content' }}>
                <input type="checkbox" checked={edit.notApplicable} onChange={(e) => onNotApplicable(e.target.checked)}
                  style={{ width: 17, height: 17 }} />
                Not applicable
              </label>
            )}

            {notes.length > 0 && (
              <div style={{ padding: '8px 12px', background: 'var(--blue-50)', borderRadius: 'var(--radius-md)' }}>
                <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 12px', fontSize: 13 }}>
                  {notes.map(([k, v]) => (
                    <div key={k} style={{ display: 'contents' }}>
                      <dt style={{ fontWeight: 700, color: 'var(--text-muted)' }}>{k}</dt>
                      <dd style={{ margin: 0, color: 'var(--text-strong)' }}>{v}</dd>
                    </div>
                  ))}
                </dl>
                <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 3 }}>From the record. Change it there.</div>
              </div>
            )}

            {readOnly ? (
              <ReadOnlyValue entry={entry} value={saved.value} notApplicable={saved.not_applicable} birthDate={birthDate} />
            ) : edit.notApplicable ? (
              <div style={{ padding: '14px 15px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px dashed var(--border-strong)', color: 'var(--text-muted)', fontSize: 13.5 }}>
                <strong style={{ color: 'var(--text-body)' }}>Not applicable.</strong>{' '}
                The text is kept and comes back if you untick this.
              </div>
            ) : (
              <KindInput entry={entry} edit={edit} onChange={onChange} birthDate={birthDate} custodianName={custodianName} disabled={busy} />
            )}

            {showSeed && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <Button size="sm" variant="outline" onClick={onUseSeed} iconLeft={<Icon name="file-input" size={14} />}>Start from what the record says</Button>
                <span style={{ ...NOTE, fontSize: 12 }}>Copies the record&apos;s text into the box. It is not saved until you save.</span>
              </div>
            )}

            {error && (
              <p role="alert" style={{ margin: 0, color: 'var(--red-700)', fontSize: 13.5, fontWeight: 600 }}>{error}</p>
            )}

            {conflict && (
              <div role="alert" style={{ padding: '12px 14px', borderRadius: 'var(--radius-md)', background: 'var(--red-50)', border: '1px solid var(--red-100)', display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--red-700)' }}>
                  This section was saved from another tab or by someone else since you opened it.
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
                  The saved version{conflict.updated_by_name ? `, by ${conflict.updated_by_name}` : ''}
                  {conflict.updated_at ? `, ${exactDate(conflict.updated_at)}` : ''}:
                </div>
                <div style={{ background: 'var(--surface)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border)', padding: '10px 12px', maxHeight: 320, overflow: 'auto' }}>
                  <ReadOnlyValue entry={entry} value={conflict.value} notApplicable={conflict.not_applicable} birthDate={birthDate} />
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
                  Yours is still in the box above. Neither is lost until you choose.
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <Button size="sm" variant="secondary" disabled={busy} onClick={onLoadSaved}>Load the saved version</Button>
                  <Button size="sm" variant="primary" disabled={busy} onClick={onKeepMine}>Keep mine</Button>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  Load the saved version replaces what you typed. Keep mine saves yours over the saved one.
                </div>
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', paddingTop: 2 }}>
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{savedLine}</span>
              {!readOnly && (
                <Button size="sm" variant="primary" disabled={!dirty || busy || !!conflict} onClick={onSave}>
                  {busy ? 'Saving…' : 'Save section'}
                </Button>
              )}
            </div>
          </div>
          <Guidance hints={entry.hints} titleId={hintsId} />
        </div>
      </Card>
    </section>
  );
}
