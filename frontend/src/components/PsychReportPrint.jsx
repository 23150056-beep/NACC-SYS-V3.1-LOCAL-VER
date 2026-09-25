import { ageFrom, caseRef } from '../utils/child';
import { caseDate } from '../config/caseData';

/* What the child record's Print button puts on paper (owner's request,
 * 24 Sep 2026): a psychological report in the standard layout, filled from
 * the record the reader can already see on screen.
 *
 * It is a STANDARD layout, not the agency's own template - none has been seen
 * yet (see CLAUDE.md, Reports). When it arrives, this is the one file to change.
 *
 * Built only from what the page already loaded for this reader, so it can
 * never print more than they could read: a psychologist without the child's
 * history gets their own entries only, exactly as on screen. Remarks and the
 * child's self-report flags are left out on purpose - they are working notes
 * and the child's own words, not findings. Anything the record does not hold
 * prints as ruled lines for the psychologist to complete by hand, rather than
 * as an invented sentence.
 */

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December'];

// "2026-09-24" -> "September 24, 2026", read as a calendar date. Parsing it
// with new Date() would read midnight UTC and print the day before anywhere
// west of Greenwich.
function longDate(value) {
  if (!value) return '';
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number);
  if (!y || !m || !d) return String(value);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

function todayIso() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

const S = {
  page: { fontFamily: "Georgia, 'Times New Roman', serif", fontSize: '11pt', lineHeight: 1.5, color: '#000' },
  heading: { fontFamily: 'inherit', color: '#000', fontSize: '11pt', fontWeight: 700, textTransform: 'uppercase', margin: '18pt 0 6pt', letterSpacing: '0.02em' },
  cellK: { padding: '3pt 8pt', border: '1px solid #000', width: '34%', fontWeight: 700, verticalAlign: 'top' },
  cellV: { padding: '3pt 8pt', border: '1px solid #000', verticalAlign: 'top' },
  th: { padding: '3pt 8pt', border: '1px solid #000', textAlign: 'left', fontWeight: 700 },
  line: { borderBottom: '1px solid #000', height: '18pt' },
};

function Lines({ n = 4 }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: n }, (_, i) => <div key={i} style={S.line} />)}
    </div>
  );
}

function Section({ n, title, children }) {
  return (
    <section style={{ breakInside: 'avoid-page' }}>
      <h2 style={S.heading}>{n}. {title}</h2>
      {children}
    </section>
  );
}

export default function PsychReportPrint({ data, className = 'racco-print-only' }) {
  const child = data.child || {};
  const age = ageFrom(child.birth_date);
  const [dateLabel, dateValue] = caseDate(child);
  const address = [[child.house_number, child.street].filter(Boolean).join(' '),
    child.barangay, child.municipality, child.province].filter(Boolean).join(', ');

  const completed = (data.pre_assessments || []).filter((p) => p.status === 'completed');
  const instruments = [];
  const seen = new Set();
  for (const title of [
    ...completed.flatMap((p) => p.instrument_titles || []),
    ...(data.result_entries || []).map((e) => e.instrument_title),
  ]) {
    if (title && !seen.has(title)) { seen.add(title); instruments.push(title); }
  }
  const interviews = data.interviews || [];
  const results = [...(data.result_entries || [])].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const problems = data.problems || [];
  const plan = (data.treatment_plans || []).find((p) => p.status === 'active') || (data.treatment_plans || [])[0];

  const identifying = [
    ['Name', child.fullname],
    ['Case reference', child.id ? caseRef(child.id) : ''],
    ['Date of birth / Age', [longDate(child.birth_date), age != null ? `${age} years old` : ''].filter(Boolean).join(' · ')],
    ['Sex', child.gender],
    ['Place of birth or place found', child.place_of_birth_or_found],
    ...(child.date_found ? [['Date found', longDate(child.date_found)]] : []),
    ['Birth status', child.birth_status],
    ['Educational placement', child.education_level],
    ['Address', address || child.address],
    ['Category / Case type', [child.case_category, child.case_type].filter(Boolean).join(' · ')],
    ['Legal status', child.legal_status],
    ...(child.surrendered_by ? [['Previous custodian', child.surrendered_by]] : []),
    [dateLabel, longDate(dateValue)],
    ['Referral source', child.referral_source],
    ['Psychologist', child.psychologist_name],
    ['Date of report', longDate(todayIso())],
  ];

  const who = child.first_name || child.fullname || 'The child';
  const background = [
    `${who} is ${age != null ? `a ${age}-year-old` : 'a'}${child.gender ? ` ${child.gender.toLowerCase()}` : ''} child`,
    child.case_type ? ` under the agency's ${child.case_type} program` : '',
    child.case_category ? `, categorized as ${child.case_category}` : '',
    dateValue ? `, with a ${dateLabel.toLowerCase()} of ${longDate(dateValue)}` : '',
    '.',
  ].join('');

  return (
    <div className={className} style={S.page}>
      {/* Not <header>/<footer>: index.css hides those when printing, for the
          app's own top bar. */}
      <div style={{ textAlign: 'center', marginBottom: '14pt' }}>
        <div style={{ fontSize: '10pt' }}>Republic of the Philippines</div>
        <div style={{ fontWeight: 700 }}>NATIONAL AUTHORITY FOR CHILD CARE</div>
        <div style={{ fontSize: '10pt' }}>Regional Alternative Child Care Office I</div>
        <div style={{ fontSize: '14pt', fontWeight: 700, marginTop: '12pt', letterSpacing: '0.06em' }}>PSYCHOLOGICAL REPORT</div>
        <div style={{ fontSize: '9pt', fontWeight: 700, marginTop: '2pt' }}>CONFIDENTIAL</div>
      </div>

      <Section n="I" title="Identifying Information">
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {identifying.map(([k, v]) => (
              <tr key={k}><td style={S.cellK}>{k}</td><td style={S.cellV}>{v || '—'}</td></tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section n="II" title="Reason for Referral">
        {child.referral_reason ? <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{child.referral_reason}</p> : <Lines n={3} />}
      </Section>

      <Section n="III" title="Background Information">
        <p style={{ margin: '0 0 6pt' }}>{background}</p>
        {child.medical_notes && <p style={{ margin: '0 0 6pt', whiteSpace: 'pre-wrap' }}><strong>Medical notes.</strong> {child.medical_notes}</p>}
        <Lines n={2} />
      </Section>

      <Section n="IV" title="Assessment Procedures">
        {instruments.length === 0 && interviews.length === 0 ? <Lines n={3} /> : (
          <ul style={{ margin: 0, paddingLeft: '18pt', listStyle: 'disc' }}>
            {interviews.map((iv) => (
              <li key={`iv-${iv.id}`}>Clinical interview{iv.template_title ? ` (${iv.template_title})` : ''}
                {iv.respondent ? ` with ${iv.respondent}` : ''}{iv.date ? `, ${longDate(iv.date)}` : ''}</li>
            ))}
            {instruments.map((t) => <li key={t}>{t}</li>)}
            <li>Behavioral observation</li>
          </ul>
        )}
      </Section>

      <Section n="V" title="Behavioral Observations">
        {problems.length === 0 ? <Lines n={4} /> : (
          <ul style={{ margin: 0, paddingLeft: '18pt', listStyle: 'disc' }}>
            {problems.map((p) => (
              <li key={p.id}>{p.description}{p.category ? ` (${p.category})` : ''}
                {p.identified_on ? `, noted ${longDate(p.identified_on)}` : ''}{p.resolved ? ' — since resolved' : ''}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section n="VI" title="Results and Interpretation">
        {results.length === 0 ? <Lines n={4} /> : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr><th style={S.th}>Instrument</th><th style={S.th}>Date</th><th style={S.th}>Findings</th><th style={S.th}>Classification</th></tr>
            </thead>
            <tbody>
              {results.map((e) => (
                <tr key={e.id}>
                  <td style={S.cellV}>{e.instrument_title || 'Assessment'}</td>
                  <td style={{ ...S.cellV, whiteSpace: 'nowrap' }}>{longDate(e.date)}</td>
                  <td style={{ ...S.cellV, whiteSpace: 'pre-wrap' }}>{e.summary}</td>
                  <td style={S.cellV}>{e.classification || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section n="VII" title="Summary and Clinical Impression">
        <Lines n={5} />
      </Section>

      <Section n="VIII" title="Recommendations">
        {child.recommendation && <p style={{ margin: '0 0 6pt', whiteSpace: 'pre-wrap' }}>{child.recommendation}</p>}
        {plan && (
          <div style={{ margin: '0 0 6pt' }}>
            {plan.objectives && <p style={{ margin: '0 0 4pt', whiteSpace: 'pre-wrap' }}><strong>Treatment objectives.</strong> {plan.objectives}</p>}
            {plan.interventions && <p style={{ margin: '0 0 4pt', whiteSpace: 'pre-wrap' }}><strong>Interventions.</strong> {plan.interventions}</p>}
            {plan.review_date && <p style={{ margin: 0 }}><strong>Review date.</strong> {longDate(plan.review_date)}</p>}
          </div>
        )}
        {!child.recommendation && !plan ? <Lines n={4} /> : <Lines n={2} />}
      </Section>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '28pt', marginTop: '36pt', breakInside: 'avoid-page' }}>
        <div>
          <div>Prepared by:</div>
          <div style={{ ...S.line, marginTop: '28pt' }} />
          <div style={{ fontWeight: 700 }}>{child.psychologist_name || 'Psychologist'}</div>
          <div>Psychologist · License No. ______________</div>
          <div>Date: ______________</div>
        </div>
        <div>
          <div>Noted by:</div>
          <div style={{ ...S.line, marginTop: '28pt' }} />
          <div>Signature over printed name</div>
          <div>Designation: ______________</div>
          <div>Date: ______________</div>
        </div>
      </div>

      <div style={{ marginTop: '24pt', fontSize: '8.5pt', borderTop: '1px solid #000', paddingTop: '4pt' }}>
        Confidential. Prepared from the records of NACC – RACCO I on {longDate(todayIso())}; sections left as lines
        are for the psychologist to complete. Release only to persons authorized under the Data Privacy Act of 2012.
      </div>
    </div>
  );
}
