import { useEffect, useState } from 'react';
import api from '../api/client';
import { useToast } from '../context/ToastContext';
import {
  Badge, Button, Card, Icon, MiniBar, Note, PAGE, PageHeader, Segmented, StatCard,
  TD, TH, THEAD_ROW, TR,
} from '../ui';
import { sendFeedback, censusNarrative } from '../api/assistant';

const RANGES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'yearly', label: 'Annual' },
];
const EMPTY = { total: 0, children: 0, by_case_type: {}, per_psychologist: [], trend: [], terminations_by_reason: {}, pending_pre_assessments: 0, caseload_per_psychologist: [], nacc_service_users: { age_groups: [], case_categories: [] } };

export default function AgencySummary() {
  const toast = useToast();
  const [range, setRange] = useState('monthly');
  const [data, setData] = useState(null);
  const [narrative, setNarrative] = useState(null); // { text, jobId }
  const [narrativeBusy, setNarrativeBusy] = useState(false);

  useEffect(() => {
    api.get(`/reports/summary/?range=${range}`).then((r) => setData(r.data)).catch(() => setData(EMPTY));
  }, [range]);

  const downloadCsv = async () => {
    try {
      const res = await api.get(`/reports/summary/?range=${range}&export=csv`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a'); a.href = url; a.download = `agency-summary-${range}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error('Could not export the summary.'); }
  };

  const d = data || EMPTY;
  const trend = (d.trend || []).map((t) => ({ bucket: t.bucket, count: t.count }));
  const trendMax = Math.max(1, ...trend.map((t) => t.count));
  const caseMix = Object.entries(d.by_case_type || {}).sort((a, b) => b[1] - a[1]);
  const caseMixMax = Math.max(1, ...caseMix.map(([, v]) => v));
  const terminations = Object.entries(d.terminations_by_reason || {}).sort((a, b) => b[1] - a[1]);
  const termMax = Math.max(1, ...terminations.map(([, v]) => v));

  const writeNarrative = async () => {
    setNarrativeBusy(true);
    try {
      // Only finished figures — the model restates these and computes nothing.
      const { draft, job_id } = await censusNarrative({
        period: range,
        completed_pre_assessments: d.total,
        children_seen: d.children,
        pending_pre_assessments: d.pending_pre_assessments,
        ...Object.fromEntries(
          Object.entries(d.by_case_type || {}).map(([k, v]) => [`case_type_${k}`, v])),
      });
      setNarrative({ text: draft, jobId: job_id });
    } catch (err) {
      toast.error(err.response?.status === 503
        ? 'The assistant is unavailable right now.'
        : 'Could not write the narrative.');
    } finally {
      setNarrativeBusy(false);
    }
  };

  const psychNames = [...new Set([
    ...(d.per_psychologist || []).map((p) => p.name),
    ...(d.caseload_per_psychologist || []).map((p) => p.name),
  ])];

  return (
    <div style={PAGE} className="racco-print-area">
      <PageHeader
        title="Agency Summary"
        subtitle={`${RANGES.find((r) => r.value === range)?.label || range} · RACCO I`}
      >
        <span className="racco-no-print" style={{ display: 'inline-flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Segmented options={RANGES} value={range} onChange={setRange} label="Summary range" />
          <Button variant="secondary" onClick={downloadCsv} iconLeft={<Icon name="download" size={17} />}>Export CSV</Button>
          <Button variant="secondary" onClick={() => window.print()} iconLeft={<Icon name="printer" size={17} />}>Print</Button>
        </span>
      </PageHeader>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        <StatCard label="Completed pre-assessments" value={d.total} tone="brand" icon={<Icon name="clipboard-check" size={18} />} hint="closed in this period" />
        <StatCard label="Children seen" value={d.children} tone="success" icon={<Icon name="users" size={18} />} hint="distinct children" />
        <StatCard label="Pending pre-assessments" value={d.pending_pre_assessments} tone="amber" icon={<Icon name="hourglass" size={18} />} hint="still open at period end" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
        <Card title="Completed sessions over time" padding="14px 16px">
          {trend.length === 0 ? (
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No sessions in this period.</p>
          ) : (
            <>
              {/* Plain bars rather than a charting library: this is one series
                  of six to nine integers, and the count is printed on each bar
                  — a tooltip nobody can read on paper was doing the same job. */}
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, height: 180, borderBottom: '1px solid var(--border)' }}>
                {trend.map((t) => (
                  <div key={t.bucket} style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', height: '100%' }}>
                    <span className="racco-mono" style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 3 }}>{t.count}</span>
                    <div style={{ width: '100%', background: 'var(--blue-600)', borderRadius: '5px 5px 0 0', height: `${Math.round((t.count / trendMax) * 92)}%` }} />
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 8, paddingTop: 6 }}>
                {trend.map((t) => (
                  <span key={t.bucket} style={{ flex: 1, textAlign: 'center', fontWeight: 600, fontSize: 10.5, color: 'var(--text-muted)' }}>{t.bucket}</span>
                ))}
              </div>
            </>
          )}
        </Card>

        <Card title="Active case mix" padding="14px 16px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
            {caseMix.length === 0
              ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No active cases.</p>
              : caseMix.map(([k, v]) => <MiniBar key={k} label={k} value={v} pct={`${Math.round((v / caseMixMax) * 100)}%`} />)}
          </div>
        </Card>
      </div>

      <Card
        title="Narrative summary"
        actions={(
          <Button variant="secondary" size="sm" onClick={writeNarrative} disabled={narrativeBusy} className="racco-no-print">
            {narrativeBusy ? 'Writing…' : narrative ? 'Re-draft' : 'Draft narrative'}
          </Button>
        )}
        padding="0"
      >
        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 11, padding: '10px 12px', background: 'var(--ink-50)', borderLeft: '3px solid var(--border-strong)', borderRadius: '0 8px 8px 0' }}>
            <Icon name="file-pen" size={17} style={{ color: 'var(--text-muted)', flex: 'none', marginTop: 1 }} />
            <p style={{ fontStyle: 'italic', fontSize: 11.5, lineHeight: 1.55, color: 'var(--text-muted)' }}>
              Drafted from the figures above. Every number is computed by the system; the assistant only writes the
              prose around them. Review before it goes in any report.
            </p>
          </div>
          {narrative ? (
            <>
              <p style={{ whiteSpace: 'pre-wrap', fontSize: 13.5, lineHeight: 1.7, color: 'var(--text-body)' }}>{narrative.text}</p>
              <div className="racco-no-print" style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end' }}>
                <span style={{ flex: 1, fontWeight: 600, fontSize: 11.5, color: 'var(--text-faint)' }}>
                  Your answer trains nothing — it is counted so the feature can be judged.
                </span>
                <Button
                  variant="secondary" size="sm"
                  onClick={() => { sendFeedback(narrative.jobId, 'discarded').catch(() => {}); setNarrative(null); }}
                >
                  Not useful
                </Button>
                <Button
                  variant="secondary" size="sm"
                  style={{ background: 'var(--success-50)', borderColor: 'var(--success-100)', color: 'var(--success-700)' }}
                  onClick={() => sendFeedback(narrative.jobId, 'accepted').catch(() => {})}
                >
                  Useful
                </Button>
              </div>
            </>
          ) : (
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No narrative drafted yet.</p>
          )}
        </div>
      </Card>

      <Card eyebrow="NACC-SAMD reporting" title="Service users (current)" padding="0">
        <div className="racco-scroll" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={THEAD_ROW}>
              {['Age group', 'Male', 'Female', 'Total'].map((h) => <th key={h} scope="col" style={TH}>{h}</th>)}
            </tr></thead>
            <tbody>
              {(d.nacc_service_users?.age_groups || []).length === 0
                ? <tr><td colSpan={4} style={{ padding: 16, color: 'var(--text-faint)', fontSize: 12.5 }}>No active children.</td></tr>
                : d.nacc_service_users.age_groups.map((g) => (
                  <tr key={g.label} style={TR}>
                    <td style={{ ...TD, fontWeight: 700, color: 'var(--text-strong)' }}>{g.label}</td>
                    <td style={TD}>{g.male}</td>
                    <td style={TD}>{g.female}</td>
                    <td className="racco-mono" style={{ ...TD, fontWeight: 700, color: 'var(--blue-600)' }}>{g.total}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <div style={{ padding: '12px 15px', borderTop: '1px solid var(--divider)' }}>
          <div className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)', marginBottom: 8 }}>By case category</div>
          {(d.nacc_service_users?.case_categories || []).length === 0 ? (
            <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No active children.</p>
          ) : (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {d.nacc_service_users.case_categories.map((c) => <Badge key={c.label} tone="neutral" size="sm">{c.label} · {c.count}</Badge>)}
            </div>
          )}
        </div>
        <Note icon="stamp">
          Mirrors the Service Users block of the NACC certification form (NACC-SAMD-GF-000, June 2025).
        </Note>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
        <Card title="Terminations by reason" padding="14px 16px">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
            {terminations.length === 0
              ? <p style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>No terminations in this period.</p>
              : terminations.map(([k, v]) => (
                <MiniBar key={k} label={k} value={v} pct={`${Math.round((v / termMax) * 100)}%`} color="var(--amber-500)" />
              ))}
          </div>
        </Card>

        <Card eyebrow="Clinical team" title="Activity &amp; caseload" padding="0">
          <div className="racco-scroll" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={THEAD_ROW}>
                {['Psychologist', 'Sessions', 'Active caseload'].map((h) => <th key={h} scope="col" style={TH}>{h}</th>)}
              </tr></thead>
              <tbody>
                {psychNames.length === 0
                  ? <tr><td colSpan={3} style={{ padding: 16, color: 'var(--text-faint)', fontSize: 12.5 }}>No activity in this period.</td></tr>
                  : psychNames.map((name) => (
                    <tr key={name} style={TR}>
                      <td style={{ ...TD, fontWeight: 700, color: 'var(--text-strong)' }}>{name}</td>
                      <td className="racco-mono" style={TD}>{(d.per_psychologist || []).find((p) => p.name === name)?.count || 0}</td>
                      <td className="racco-mono" style={TD}>{(d.caseload_per_psychologist || []).find((p) => p.name === name)?.caseload || 0}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
