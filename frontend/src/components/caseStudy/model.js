/* What the Case study screens do with a box's value, apart from drawing it.
 *
 * A saved value comes back from the server in its cleaned shape (see
 * backend/case_study/validation.py). Typing happens on a WORKING COPY, which
 * is the same value with every field a string, so an input is never given
 * null. `normalise` takes a working copy back to the cleaned shape, which is
 * both what a save sends and what two copies are compared in: a box is
 * "changed" only when its normalised copy differs from the saved one, so a
 * trailing space or an empty row typed and removed is not a change.
 */
import { ADOPTION, PAP_ROWS, SCSR_BLOCKS, SCSR_SECTIONS, appliesTo } from '../../config/scsr';

export const PROSE_MAX = 20000;
export const ROWS_MAX = 50;
export const PAP_SIDES = [
  { side: 'female', label: 'Female PAP' },
  { side: 'male', label: 'Male PAP' },
];

const str = (v) => (v == null ? '' : String(v));

// --- working copies ----------------------------------------------------------------

export function blankRow(entry) {
  return Object.fromEntries(entry.columns.map((c) => [c.key, '']));
}

/** The working copy of a saved value: every field a string, nothing null. */
export function workingCopy(entry, saved) {
  switch (entry.kind) {
    case 'prose':
      return str(saved);
    case 'list':
      return Array.isArray(saved) ? saved.map(str) : [];
    case 'table':
      return (Array.isArray(saved) ? saved : []).map((row) => (
        Object.fromEntries(entry.columns.map((c) => [c.key, str(row?.[c.key])]))));
    case 'pap_table':
      return Object.fromEntries(PAP_SIDES.map(({ side }) => [side, Object.fromEntries(
        PAP_ROWS.map((r) => [r.id, str(saved?.[side]?.[r.id])]))]));
    case 'date':
      return str(saved);
    case 'tick':
      return saved === true;
    case 'measurements':
      return { height_cm: str(saved?.height_cm), weight_kg: str(saved?.weight_kg), measured_on: str(saved?.measured_on) };
    case 'placement':
      return {
        matching_date: str(saved?.matching_date), racco_cpa: str(saved?.racco_cpa),
        accepted_date: str(saved?.accepted_date), entrustment_date: str(saved?.entrustment_date),
      };
    default:
      return saved;
  }
}

const measure = (v) => {
  const text = str(v).trim();
  if (!text) return '';
  const n = Number(text);
  return Number.isFinite(n) ? String(Math.round(n * 10) / 10) : text;
};

/** The cleaned shape of a working copy: what a save sends and what is compared. */
export function normalise(entry, working) {
  switch (entry.kind) {
    case 'prose':
      return str(working).trim();
    case 'list':
      return (working || []).map((l) => str(l).trim()).filter(Boolean);
    case 'table':
      return (working || [])
        .map((row) => Object.fromEntries(entry.columns.map((c) => [c.key, str(row?.[c.key]).trim()])))
        .filter((row) => Object.values(row).some(Boolean));
    case 'pap_table':
      return Object.fromEntries(PAP_SIDES.map(({ side }) => [side, Object.fromEntries(
        PAP_ROWS.map((r) => [r.id, str(working?.[side]?.[r.id]).trim()]).filter(([, v]) => v))]));
    case 'date':
      return str(working).trim() || null;
    case 'tick':
      return working === true;
    case 'measurements': {
      const out = {};
      const h = measure(working?.height_cm);
      const w = measure(working?.weight_kg);
      const on = str(working?.measured_on).trim();
      if (h) out.height_cm = h;
      if (w) out.weight_kg = w;
      if (on) out.measured_on = on;
      return out;
    }
    case 'placement': {
      const out = {};
      for (const k of ['matching_date', 'racco_cpa', 'accepted_date', 'entrustment_date']) {
        const v = str(working?.[k]).trim();
        if (v) out[k] = v;
      }
      return out;
    }
    default:
      return working;
  }
}

/** A saved value in the same canonical shape as `normalise` gives, for comparing. */
export const canonical = (entry, saved) => normalise(entry, workingCopy(entry, saved));

/** Does this working copy (and Not applicable) differ from what is saved? */
export function differs(entry, working, notApplicable, saved) {
  if (!!notApplicable !== !!saved.not_applicable) return true;
  return JSON.stringify(normalise(entry, working)) !== JSON.stringify(canonical(entry, saved.value));
}

/** Is this box empty? (The server's `missing` list is still what the screen
 *  reports as left to do; this is for the printed page's lines to fill in.) */
export const isBlank = (entry, saved) =>
  JSON.stringify(canonical(entry, saved)) === JSON.stringify(canonical(entry, null));

// --- the catalogue, by block -------------------------------------------------------

/** Is this an Adoption record? Blocks B (the prospective adoptive parents) and
 *  C (the placement) are an adoption's; every case type has block A. Accepts
 *  the child record or the case study's `record_facts`. */
export const isAdoptionRecord = (child) => child?.case_type === ADOPTION;

/* The one line a record that is not an adoption carries under the header. */
export const NOT_AN_ADOPTION_NOTE = 'Blocks B and C (the prospective adoptive parents and the placement) are for adoption records.';

/** A block's title. Block A is "The Child/Adoptee" in the template; on a record
 *  that is not an adoption there is no adoptee, so it reads "The Child". */
export function blockTitle(letter, adoption) {
  if (letter === 'A' && !adoption) return 'The Child';
  return SCSR_BLOCKS.find((b) => b.block === letter)?.title || '';
}

/** The boxes that apply to this child, grouped under their block letters. */
export function blocksFor(child, study) {
  const stored = new Map((study?.sections || []).map((s) => [s.key, s]));
  return SCSR_BLOCKS.map((b) => ({
    ...b,
    entries: SCSR_SECTIONS.filter((e) => e.block === b.block
      && appliesTo(e, child, study)
      // The server's own answer wins where it gave one: it knows the rule.
      && stored.get(e.key)?.applies !== false),
  })).filter((b) => b.entries.length > 0);
}

/** The boxes a final copy holds, grouped under their block letters. A copy
 *  keeps exactly the boxes that applied the day it was made, so which ones
 *  print is read from it and never worked out again from the record. */
export function blocksForCopy(snapshot) {
  const kept = snapshot?.sections || {};
  return SCSR_BLOCKS.map((b) => ({
    ...b,
    entries: SCSR_SECTIONS.filter((e) => e.block === b.block && e.key in kept),
  })).filter((b) => b.entries.length > 0);
}

// --- dates ----------------------------------------------------------------------------

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December'];

export function todayIso() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

/** "2026-10-08" -> "October 8, 2026", read as a calendar date (never UTC). */
export function longDate(value) {
  if (!value) return '';
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return String(value);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

/** "2019" -> "2019", "2019-05" -> "May 2019", a full date -> "May 14, 2019". */
export function partialDate(value) {
  const text = str(value);
  if (/^\d{4}$/.test(text)) return text;
  const ym = /^(\d{4})-(\d{2})$/.exec(text);
  if (ym) return `${MONTHS[Number(ym[2]) - 1] || ym[2]} ${ym[1]}`;
  return longDate(text) || text;
}

const ymd = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(str(iso));
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
};

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** How old the child was on a day, in words: whole years from two up, years
 *  and months from one, months below that, because an infant "0 years old" at
 *  entrustment tells the reader nothing. '' when either date is missing. */
export function ageAtText(birth, on) {
  const b = ymd(birth);
  const d = ymd(on);
  if (!b || !d) return '';
  let months = (d[0] - b[0]) * 12 + (d[1] - b[1]);
  if (d[2] < b[2]) months -= 1;
  if (months < 0) return '';
  const years = Math.floor(months / 12);
  if (years >= 2) return plural(years, 'year');
  if (years === 1) return months % 12 ? `1 year ${plural(months % 12, 'month')}` : '1 year';
  return months > 0 ? plural(months, 'month') : 'under a month';
}

// --- wording ---------------------------------------------------------------------------

/* What the server says to a write when the case study is final
 * (backend case_study/access.py FINAL_SENTENCE; a test holds the two together).
 * The screen compares against it to tell "final" from "closed". */
export const FINAL_SENTENCE = 'This case study is final. Reopen it to change it.';

/* The two statements the Surrendered boxes print, in the template's own words
 * (docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx). */
export const TICK_SENTENCES = {
  a5_aware_irrevocable: 'That the birth parent is aware that the Deed of Voluntary Commitment shall become irrevocable three months after he/she signed the same, thus, he/she has time to reconsider her decision.',
  a5_explained_vernacular: 'That the content of the DVC was explained to the birth parent in the vernacular that he/she understands.',
};

/** Part I as the template words it, one row per line. `fmt` writes a date. The
 *  value is read from the record (`record_facts`), never typed here.
 *
 *  For a record that is not an adoption the template's adoption wording gives
 *  way: "Case type" stands where "Type of adoption" is, the placement date
 *  loses its adoption-only note, and the rows only an adoption uses (the legal
 *  status for the CDCLAA and the two intake dates, of which a record has one)
 *  are left out where they are blank. A copy made before this existed has no
 *  case type in its facts and is an adoption's. */
export function partOneRows(f, fmt) {
  const adoption = !f.case_type || f.case_type === ADOPTION;
  const health = f.health_condition === 'With special needs' && f.special_needs
    ? `${f.health_condition}: ${f.special_needs}` : f.health_condition;
  const rows = [
    ['Name (First, Middle and Last Name. For Child Without Known Parents, indicate the given first and last name and alias, if applicable)',
      f.alias ? `${f.fullname} (alias ${f.alias})` : f.fullname, 'Name'],
    ['Sex', f.gender],
    ['Date of Birth or Given Date of Birth', fmt(f.birth_date), 'Date of birth or given date of birth'],
    ['Age', f.age != null ? `${f.age} years old` : ''],
    ['Place of Birth or Place Found', f.place_of_birth_or_found, 'Place of birth or place found'],
    ['Birth Status (Marital/Non-Marital/Child)', f.birth_status, 'Birth status'],
    ['Category (Surrendered/Abandoned/Dependent/Neglected/Without Known Parents, Orphan)', f.case_category, 'Category'],
    ['Legal Status (with issued CDCLAA / IVC / judicially declared abandoned)',
      [f.legal_status, f.legal_status && f.legal_status_date ? `issued ${fmt(f.legal_status_date)}` : ''].filter(Boolean).join(', '), 'Legal status', true],
    ['Health Condition (healthy or with special needs, specify)', health, 'Health condition'],
    ['Date of Admission to the Agency', fmt(f.date_of_admission), 'Date of admission', true],
    [adoption ? 'Date of Placement to Custodian (for Relative/Stepparent/Adult/FA/IP)' : 'Date of Placement to Custodian',
      fmt(f.date_of_placement_to_custodian), 'Date of placement to custodian', true],
    adoption
      ? ['Type of Adoption (Regular, Domestic Relative, Step-parent, Adult, SIBRA, ICA Relative, IP, Foster-Adopt)', f.type_of_adoption, 'Type of adoption']
      : ['Case Type', f.case_type, 'Case type'],
    ['Current Whereabouts', f.current_placement],
  ];
  return rows
    .filter(([, value, , adoptionOnly]) => adoption || !adoptionOnly || value)
    .map(([label, value, short]) => ({ label, short: short || label, value: value || '' }));
}

/** Facts from the record that the template shows beside a box. */
export function recordNotes(key, f, fmt) {
  if (!f) return [];
  const rows = [];
  if (key === 'a5_summary' && f.cdclaa_date) rows.push(['Date the CDCLAA was issued', fmt(f.cdclaa_date)]);
  if (key === 'a5_abandonment') {
    if (f.date_found) rows.push(['Date found', fmt(f.date_found)]);
    if (f.place_found) rows.push(['Place found', f.place_found]);
    // Worked out here rather than taken from the record's whole years, which
    // reads "0 years" for a child found as a newborn.
    const found = ageAtText(f.birth_date, f.date_found);
    if (found) rows.push(['Age when found', found]);
  }
  if (key === 'c4_functioning' && f.education_level) rows.push(['Education level', f.education_level]);
  return rows;
}

/** A refusal as one sentence, whatever shape it arrived in. */
export function sentence(err, fallback) {
  const data = err?.response?.data;
  if (typeof data === 'string' && data.length < 300) return data;
  if (data?.detail) return String(data.detail);
  const first = data && typeof data === 'object' ? Object.values(data)[0] : null;
  if (Array.isArray(first) && first.length) return String(first[0]);
  if (!err?.response) return 'The server could not be reached. Nothing was lost; try again.';
  return fallback;
}
