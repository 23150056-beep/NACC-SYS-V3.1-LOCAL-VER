import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, Badge, Button, Card, FormField, Icon, Input } from '../../ui';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { useToast } from '../../context/ToastContext';
import { saveCaseStudyHeader, saveCaseStudySection } from '../../api/caseStudy';
import { SCSR_SECTIONS } from '../../config/scsr';
import { shortDate } from '../../utils/time';
import { clearDraft, readDraft, writeDraft } from './drafts';
import { FinalBanner, FinalsOnFile, MarkAsFinal } from './FinalControls';
import {
  FINAL_SENTENCE, blocksFor, canonical, differs, normalise, recordNotes, sentence, todayIso,
  workingCopy,
} from './model';
import PartOne from './PartOne';
import SectionCard from './SectionCard';

/* The social worker's editor for the case study (owner's decisions, 8 Oct
 * 2026; design: docs/superpowers/specs/2026-10-07-scsr-parts-2-5-design.md).
 *
 * What it keeps, and why it is shaped this way:
 *
 *  - `study` (the hook's) is what the SERVER holds: every box with its version.
 *  - `edits[key]` is what is on screen for a box somebody has touched: the
 *    working copy, Not applicable, and `base`, the version it started from.
 *    A save sends `base` as expected_version, so a save that does not know
 *    what it replaces is refused with a 409 instead of overwriting. An
 *    untouched box is simply the saved one.
 *  - Each touched box is mirrored into localStorage (drafts.js) so a closed
 *    tab does not cost the afternoon. It is offered back, never applied.
 *
 * Nothing leaves the screen without useConfirm(); a refusal is the server's
 * sentence under the box it belongs to; and a conflict never discards either
 * text until the writer chooses.
 */

const without = (obj, key) => {
  const next = { ...obj };
  delete next[key];
  return next;
};

const pickSection = (out) => ({
  key: out.key, value: out.value, not_applicable: out.not_applicable, version: out.version,
  updated_by_name: out.updated_by_name, updated_at: out.updated_at, applies: out.applies,
});

// How many of the sections still to complete the header names before "Show all".
const LISTED = 6;

const UNSAVED = (key) => ({ key, value: null, not_applicable: false, version: 0, updated_by_name: null, updated_at: null, applies: true });

function jump(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: 'start' });
  el.querySelector('textarea, input, select, button')?.focus({ preventScroll: true });
}

export default function Editor({ child, cs, print }) {
  const { user } = useAuth();
  const confirm = useConfirm();
  const toast = useToast();
  const study = cs.study;
  const { setStudy, setUnsaved } = cs;
  const readOnly = !!study.read_only;
  const facts = study.record_facts;
  // Final locks every box. "Final" is told apart from "closed" (which can
  // also lock a final one) by the server's own sentence, because a closed
  // case cannot be reopened and the button would only be refused.
  const isFinal = study.status === 'final';
  const lockedByFinal = isFinal && study.read_only_reason === FINAL_SENTENCE;

  const [edits, setEdits] = useState({});
  const [errors, setErrors] = useState({});
  const [conflicts, setConflicts] = useState({});
  const [busy, setBusy] = useState({});
  const [stopped, setStopped] = useState(null);
  const [dateDraft, setDateDraft] = useState(null);
  const [dateError, setDateError] = useState('');
  const [headerBusy, setHeaderBusy] = useState(false);
  const [custodyError, setCustodyError] = useState('');
  const [allMissing, setAllMissing] = useState(false);

  const savedMap = useMemo(() => new Map((study.sections || []).map((s) => [s.key, s])), [study.sections]);
  const savedOf = useCallback((key) => savedMap.get(key) || UNSAVED(key), [savedMap]);

  // Unsaved text kept from an earlier visit, found once when the editor opens
  // and offered back box by box. One that already equals the saved box is
  // simply removed.
  const [kept, setKept] = useState(() => {
    const found = {};
    for (const entry of SCSR_SECTIONS) {
      const d = readDraft(user.id, child.id, entry.key);
      if (!d) continue;
      const saved = savedMap.get(entry.key) || UNSAVED(entry.key);
      if (differs(entry, workingCopy(entry, d.value), d.not_applicable, saved)) found[entry.key] = d;
      else clearDraft(user.id, child.id, entry.key);
    }
    return found;
  });

  const blocks = useMemo(() => blocksFor(child, study), [child, study]);
  const shown = useMemo(() => blocks.flatMap((b) => b.entries), [blocks]);

  const view = (entry) => edits[entry.key] || {
    value: workingCopy(entry, savedOf(entry.key).value),
    notApplicable: savedOf(entry.key).not_applicable,
    base: savedOf(entry.key).version,
  };

  const dirtyKeys = useMemo(() => shown.filter((entry) => {
    const ed = edits[entry.key];
    return ed && differs(entry, ed.value, ed.notApplicable, savedOf(entry.key));
  }).map((entry) => entry.key), [shown, edits, savedOf]);

  const savedDate = study.date_prepared || '';
  const dateChanged = dateDraft !== null && dateDraft !== savedDate;

  // What Print and the browser's leave-the-page warning both need to know.
  const pending = dirtyKeys.length + (dateChanged ? 1 : 0);
  useEffect(() => { setUnsaved(pending); }, [pending, setUnsaved]);
  useEffect(() => () => setUnsaved(0), [setUnsaved]);
  useEffect(() => {
    if (!pending) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [pending]);

  // --- editing --------------------------------------------------------------------

  const change = (entry, patch) => {
    const saved = savedOf(entry.key);
    const next = { ...view(entry), ...patch };
    setEdits((p) => ({ ...p, [entry.key]: next }));
    setErrors((p) => without(p, entry.key));
    if (differs(entry, next.value, next.notApplicable, saved)) {
      writeDraft(user.id, child.id, entry.key, { value: next.value, notApplicable: next.notApplicable, base: next.base });
    } else {
      clearDraft(user.id, child.id, entry.key);
    }
  };

  const restore = (entry) => {
    const d = kept[entry.key];
    if (!d) return;
    const saved = savedOf(entry.key);
    setEdits((p) => ({
      ...p,
      [entry.key]: {
        value: workingCopy(entry, d.value), notApplicable: !!d.not_applicable,
        // The version the text started from, not today's: if the box was saved
        // since, saving this is a conflict the writer is shown, not a silent overwrite.
        base: Number.isInteger(d.base_version) ? d.base_version : saved.version,
      },
    }));
    setKept((p) => without(p, entry.key));
  };

  const discardKept = (entry) => {
    clearDraft(user.id, child.id, entry.key);
    setKept((p) => without(p, entry.key));
  };

  // --- saving a box ---------------------------------------------------------------

  const applySaved = (out) => setStudy((prev) => ({
    ...prev,
    missing: out.missing ?? prev.missing,
    // The save moved the case study's own version, which Mark as final sends
    // back to say what it was looking at.
    updated_at: out.case_study_updated_at ?? prev.updated_at,
    sections: prev.sections.some((s) => s.key === out.key)
      ? prev.sections.map((s) => (s.key === out.key ? { ...s, ...pickSection(out) } : s))
      : [...prev.sections, pickSection(out)],
  }));

  const settle = (entry) => {
    setEdits((p) => without(p, entry.key));
    clearDraft(user.id, child.id, entry.key);
    setKept((p) => without(p, entry.key));
    setErrors((p) => without(p, entry.key));
    setConflicts((p) => without(p, entry.key));
  };

  /* One PUT. Resolves { ok } or { ok: false, message }; a 409 puts the other
   * version on the box instead of failing it. */
  const saveOne = async (entry, edit = edits[entry.key]) => {
    const key = entry.key;
    const saved = savedOf(key);
    setBusy((p) => ({ ...p, [key]: true }));
    const valueChanged = JSON.stringify(normalise(entry, edit.value)) !== JSON.stringify(canonical(entry, saved.value));
    const body = { expected_version: edit.base, not_applicable: edit.notApplicable };
    // Ticking Not applicable on text that has not changed sends only the tick,
    // so the stored text is not touched at all (the server keeps it).
    if (!edit.notApplicable || valueChanged) body.value = normalise(entry, edit.value);
    try {
      const out = await saveCaseStudySection(child.id, key, body);
      applySaved(out);
      settle(entry);
      return { ok: true };
    } catch (err) {
      if (err.response?.status === 409 && err.response.data?.current) {
        setConflicts((p) => ({ ...p, [key]: err.response.data.current }));
        setErrors((p) => without(p, key));
        return { ok: false, message: 'it was saved from another tab or by someone else since you opened it' };
      }
      const message = sentence(err, 'Could not save this section.');
      setErrors((p) => ({ ...p, [key]: message }));
      return { ok: false, message };
    } finally {
      setBusy((p) => without(p, key));
    }
  };

  const onSave = async (entry) => {
    if (!(await confirm({
      description: `This saves “${entry.number}. ${entry.title}” on ${child.fullname}'s case study.`,
      confirmLabel: 'Yes, save this section',
    }))) return;
    setStopped(null);
    const result = await saveOne(entry);
    if (result.ok) toast.success('Section saved');
  };

  const onSaveAll = async () => {
    const todo = shown.filter((e) => dirtyKeys.includes(e.key));
    if (!todo.length) return;
    if (!(await confirm({
      description: `This saves ${todo.length} changed section${todo.length === 1 ? '' : 's'} on ${child.fullname}'s case study, one by one, in this order.`,
      confirmLabel: `Yes, save ${todo.length === 1 ? 'it' : 'all'}`,
      details: todo.map((e) => [`${e.block}.${e.number}`, e.title]),
    }))) return;
    setStopped(null);
    let saved = 0;
    for (const entry of todo) {
      // eslint-disable-next-line no-await-in-loop
      const result = await saveOne(entry);
      if (!result.ok) {
        const rest = todo.length - saved - 1;
        setStopped({
          key: entry.key,
          text: `Stopped at “${entry.number}. ${entry.title}”: ${result.message}${/[.!?]$/.test(result.message) ? '' : '.'} `
            + `${saved} saved${rest > 0 ? `, ${rest} after it not tried` : ''}.`,
        });
        jump(`cs-sec-${entry.key}`);
        return;
      }
      saved += 1;
    }
    toast.success(`${saved} section${saved === 1 ? '' : 's'} saved`);
  };

  // --- a conflict -----------------------------------------------------------------

  const loadSaved = async (entry) => {
    settle(entry);
    // The server's copy is the truth again; a reload brings its `missing` list
    // and every other box's version up to date, and leaves the edits (each has
    // its own base) exactly as they are.
    await cs.reload();
  };

  const keepMine = async (entry) => {
    const current = conflicts[entry.key];
    if (!current) return;
    const who = current.updated_by_name ? `${current.updated_by_name}'s` : 'the saved';
    if (!(await confirm({
      description: `This saves your version of “${entry.number}. ${entry.title}” over ${who} version. Theirs is replaced.`,
      confirmLabel: 'Yes, keep mine', tone: 'warning',
    }))) return;
    const result = await saveOne(entry, { ...view(entry), base: current.version });
    if (result.ok) toast.success('Section saved');
  };

  // --- the header -------------------------------------------------------------------

  const mergeHeader = (out) => setStudy((prev) => ({
    ...prev,
    date_prepared: out.date_prepared,
    custody_over_two_years: out.custody_over_two_years,
    custody_pre_answer: out.custody_pre_answer,
    missing: out.missing,
    record_facts: out.record_facts,
    updated_at: out.updated_at,
    // Only which boxes apply is taken from the answer, never a box's version:
    // a box being edited keeps the version it started from.
    sections: prev.sections.map((s) => {
      const fresh = (out.sections || []).find((o) => o.key === s.key);
      return fresh ? { ...s, applies: fresh.applies } : s;
    }),
  }));

  const saveDate = async () => {
    const value = dateDraft ?? savedDate;
    if (value && value > todayIso()) { setDateError('The date prepared cannot be in the future.'); return; }
    if (!(await confirm({
      description: value
        ? `This sets the date prepared to ${shortDate(value)}. Every age in the report is worked out as of that day.`
        : 'This clears the date prepared. It is needed before the case study can be finalized.',
      confirmLabel: 'Yes, save the date',
    }))) return;
    setHeaderBusy(true);
    setDateError('');
    try {
      mergeHeader(await saveCaseStudyHeader(child.id, { date_prepared: value || null }));
      setDateDraft(null);
      toast.success('Date prepared saved');
    } catch (err) {
      setDateError(sentence(err, 'Could not save the date prepared.'));
    } finally {
      setHeaderBusy(false);
    }
  };

  const answerCustody = async (answer) => {
    if (answer === study.custody_over_two_years) return;
    const text = answer === null ? 'clears the answer' : `records the answer as ${answer ? 'Yes' : 'No'}`;
    if (!(await confirm({
      description: `This ${text} to “Did the prospective adoptive parents have the child in their custody for more than two years?”${answer ? ' A yes hides Placement History.' : ''}`,
      confirmLabel: 'Yes, save the answer',
    }))) return;
    setHeaderBusy(true);
    setCustodyError('');
    try {
      mergeHeader(await saveCaseStudyHeader(child.id, { custody_over_two_years: answer }));
      toast.success('Answer saved');
    } catch (err) {
      setCustodyError(sentence(err, 'Could not save the answer.'));
    } finally {
      setHeaderBusy(false);
    }
  };

  // --- progress -----------------------------------------------------------------------

  const missing = useMemo(() => study.missing || [], [study.missing]);
  const missingSet = useMemo(() => new Set(missing), [missing]);
  const remaining = shown.filter((e) => missingSet.has(e.title)).length;
  const done = shown.length - remaining;
  const dateMissing = missingSet.has('Date prepared');
  const toComplete = [
    ...(dateMissing ? [{ title: 'Date prepared', target: 'cs-date-prepared' }] : []),
    ...shown.filter((e) => missingSet.has(e.title)).map((e) => ({ title: e.title, target: `cs-sec-${e.key}` })),
  ];

  const isDomesticRelative = child.type_of_adoption === 'Domestic Relative';
  const inputBusy = headerBusy;
  const asOfText = study.date_prepared
    ? `as of the date prepared, ${shortDate(facts.age_as_of)}`
    : `as of today, ${shortDate(facts.age_as_of)}, until the date prepared is set`;

  return (
    <div className="racco-stack" style={{ gap: 14 }}>
      {isFinal && <FinalBanner child={child} cs={cs} canReopen={lockedByFinal} />}
      {readOnly && !lockedByFinal && (
        <Alert tone="warning" icon={<Icon name="lock" size={18} />}>
          {study.read_only_reason || 'This case study can no longer be changed.'}
        </Alert>
      )}

      <Card title="Social Case Study Report" padding="14px 15px"
        actions={<Badge tone={study.status === 'final' ? 'success' : 'amber'} size="sm" dot>{study.status === 'final' ? 'Final' : 'Draft'}</Badge>}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ fontWeight: 800, fontSize: 15, color: 'var(--text-strong)' }}>
              {done} of {shown.length} sections complete
            </div>
            <div aria-hidden="true" style={{ flex: '1 1 160px', maxWidth: 360, height: 8, borderRadius: 4, background: 'var(--ink-100)', overflow: 'hidden' }}>
              <div style={{ width: `${shown.length ? Math.round((done / shown.length) * 100) : 0}%`, height: '100%', background: 'var(--blue-600)' }} />
            </div>
          </div>

          <div id="cs-date-prepared" className="racco-cs-section" style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <FormField label="Date prepared" required error={dateError}
              hint="Every age in the report is worked out as of this date." style={{ width: 240 }}>
              <Input
                type="date" max={todayIso()} value={dateDraft ?? savedDate} disabled={readOnly || inputBusy}
                onChange={(e) => { setDateDraft(e.target.value); setDateError(''); }}
              />
            </FormField>
            {!readOnly && (
              <Button variant="secondary" disabled={!dateChanged || inputBusy} onClick={saveDate}>Save date</Button>
            )}
          </div>

          {isDomesticRelative && (
            <fieldset disabled={readOnly || inputBusy} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', padding: '10px 14px', margin: 0, minWidth: 0 }}>
              <legend style={{ padding: '0 6px', fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>
                Did the prospective adoptive parents have the child in their custody for more than two years?
              </legend>
              <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
                {[['Yes', true], ['No', false]].map(([label, value]) => (
                  <label key={label} style={{ display: 'inline-flex', gap: 7, alignItems: 'center', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
                    <input type="radio" name="cs-custody" checked={study.custody_over_two_years === value}
                      onChange={() => answerCustody(value)} style={{ width: 17, height: 17 }} />
                    {label}
                  </label>
                ))}
                {study.custody_over_two_years !== null && !readOnly && (
                  <Button size="sm" variant="ghost" onClick={() => answerCustody(null)}>Clear the answer</Button>
                )}
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 6 }}>
                {study.custody_over_two_years === null
                  ? (study.custody_pre_answer === null
                    ? 'Not answered. The record has no date of placement, so there is nothing to suggest.'
                    : `Suggested from the placement date: ${study.custody_pre_answer ? 'Yes' : 'No'}. Not answered yet.`)
                  : "A yes hides Placement History (C.I): it is not asked when the child had been in the adopters' custody for more than two years."}
              </div>
              {custodyError && <p role="alert" style={{ margin: '6px 0 0', color: 'var(--red-700)', fontSize: 13.5, fontWeight: 600 }}>{custodyError}</p>}
            </fieldset>
          )}

          {toComplete.length > 0 ? (
            <div style={{ fontSize: 13, color: 'var(--text-body)', lineHeight: 1.7 }}>
              <strong>Still to complete:</strong>{' '}
              {(allMissing ? toComplete : toComplete.slice(0, LISTED)).map((m, i, shownList) => (
                <span key={m.title}>
                  <button type="button" onClick={() => jump(m.target)}
                    style={{ background: 'none', border: 'none', padding: 0, color: 'var(--blue-700)', fontFamily: 'inherit', fontSize: 'inherit', textDecoration: 'underline', cursor: 'pointer' }}>
                    {m.title}
                  </button>
                  {i < shownList.length - 1 || (!allMissing && toComplete.length > LISTED) ? '; ' : '.'}
                </span>
              ))}
              {toComplete.length > LISTED && (
                <button type="button" aria-expanded={allMissing} onClick={() => setAllMissing((v) => !v)}
                  style={{ background: 'none', border: 'none', padding: 0, marginLeft: 4, color: 'var(--text-muted)', fontFamily: 'inherit', fontSize: 'inherit', fontWeight: 700, cursor: 'pointer' }}>
                  {allMissing ? 'Show fewer' : `and ${toComplete.length - LISTED} more. Show all`}
                </button>
              )}
            </div>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--success-700)', fontWeight: 700 }}>Every section is complete.</div>
          )}

          {!readOnly && <MarkAsFinal child={child} cs={cs} pending={pending} />}
        </div>
      </Card>

      <FinalsOnFile finals={study.finals} print={print} />

      {/* Block A, Part I: the record's own facts, read from it and never typed here. */}
      <h2 style={{ margin: '6px 0 0', fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 800, color: 'var(--text-strong)' }}>
        A. The Child/Adoptee
      </h2>
      <PartOne facts={facts}
        actions={child.status === 'active'
          ? <Link to={`/children?edit=${child.id}`} style={{ fontSize: 13, fontWeight: 700, color: 'var(--blue-700)' }}>Edit on the record</Link>
          : null}
        footer={`Read from the child's record. Age is worked out ${asOfText}.`} />

      {blocks.map((block, index) => (
        <div key={block.block} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {index > 0 && (
            <h2 style={{ margin: '10px 0 0', fontFamily: 'var(--font-sans)', fontSize: 16, fontWeight: 800, color: 'var(--text-strong)' }}>
              {block.block}. {block.title}
            </h2>
          )}
          {block.entries.map((entry) => {
            const ed = view(entry);
            const saved = savedOf(entry.key);
            return (
              <SectionCard
                key={entry.key}
                entry={entry} saved={saved} edit={ed}
                dirty={dirtyKeys.includes(entry.key)} missing={missingSet.has(entry.title)}
                kept={edits[entry.key] ? null : kept[entry.key]}
                error={errors[entry.key]} conflict={conflicts[entry.key]}
                busy={!!busy[entry.key]} readOnly={readOnly}
                seed={study.seeds?.[entry.key] || null}
                notes={recordNotes(entry.key, facts, shortDate)}
                birthDate={facts.birth_date} custodianName={(child.custodian_name || '').trim()}
                onChange={(value) => change(entry, { value })}
                onNotApplicable={(checked) => change(entry, { notApplicable: checked })}
                onSave={() => onSave(entry)}
                onRestore={() => restore(entry)} onDiscard={() => discardKept(entry)}
                onUseSeed={() => change(entry, { value: study.seeds[entry.key] })}
                onLoadSaved={() => loadSaved(entry)} onKeepMine={() => keepMine(entry)}
              />
            );
          })}
        </div>
      ))}

      {stopped && (
        <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>
          <span role="alert">{stopped.text}</span>{' '}
          <button type="button" onClick={() => jump(`cs-sec-${stopped.key}`)}
            style={{ background: 'none', border: 'none', padding: 0, color: 'var(--blue-700)', fontFamily: 'inherit', fontSize: 'inherit', textDecoration: 'underline', cursor: 'pointer' }}>
            Go to it
          </button>
        </Alert>
      )}

      {!readOnly && dirtyKeys.length > 0 && (
        <div className="racco-no-print" style={{ position: 'sticky', bottom: 12, zIndex: 5, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', padding: '10px 14px', background: 'var(--surface)', border: '1px solid var(--blue-200)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-lg)' }}>
          <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-body)' }}>
            {dirtyKeys.length} section{dirtyKeys.length === 1 ? ' has' : 's have'} changes that are not saved.
          </span>
          <Button variant="primary" disabled={Object.keys(busy).length > 0} onClick={onSaveAll}>
            Save all changes ({dirtyKeys.length})
          </Button>
        </div>
      )}
    </div>
  );
}
