import { useCallback, useEffect, useMemo, useState } from 'react';
import api from '../api/client';
import { useToast } from '../context/ToastContext';
import {
  Card, Button, Badge, Alert, Icon, EmptyState, ConfidenceMeter, Tabs, PAGE,
} from '../ui';

// A plain text field with commit-on-blur/Enter semantics. The shared <Input>
// primitive manages its own focus-ring state via an internal onBlur handler;
// since JSX prop spread lets a later onBlur silently replace an earlier one,
// passing our own onBlur/onKeyDown to <Input> would permanently strand its
// focus ring "on" after the first blur. This local field owns its own focus
// styling instead, so commit callbacks compose cleanly.
function TextField({ value, onChange, onCommit, placeholder, style = {} }) {
  return (
    <input
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--blue-500)'; e.currentTarget.style.boxShadow = 'var(--shadow-focus)'; }}
      onBlur={(e) => {
        e.currentTarget.style.borderColor = 'var(--border-strong)';
        e.currentTarget.style.boxShadow = 'none';
        if (onCommit) onCommit(e);
      }}
      onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
      style={{
        width: '100%', height: 'var(--field-h-sm)', padding: '0 12px', fontFamily: 'var(--font-sans)',
        fontSize: 13, color: 'var(--text-strong)', background: 'var(--surface)',
        border: '1px solid var(--border-strong)', borderRadius: 'var(--radius-md)', outline: 'none',
        transition: 'border-color var(--dur-fast), box-shadow var(--dur-fast)',
        ...style,
      }}
    />
  );
}

// Certification bands, per NACC-SAMD-GF-000 (June 2025): 75-100% Full
// Certification, 60-74% Conditional Approval, 59% or below Non-Certification.
function bandTone(pct) {
  if (pct >= 75) return 'success';
  if (pct >= 60) return 'warning';
  return 'danger';
}
function bandBadgeTone(band) {
  if (band === 'Full Certification') return 'success';
  if (band === 'Conditional Approval') return 'warning';
  return 'danger';
}
const COMPLIANCE_LABEL = { yes: 'Yes', not: 'Not', na: 'N/A' };

function groupBySection(items) {
  const groups = [];
  let current = null;
  for (const item of items) {
    const label = item.section || '';
    if (!current || current.label !== label) {
      current = { label, items: [] };
      groups.push(current);
    }
    current.items.push(item);
  }
  return groups;
}

function Stat({ label, value }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 10.5, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{label}</span>
      <span className="racco-mono" style={{ fontWeight: 800, fontSize: 18, color: 'var(--text-strong)' }}>{value}</span>
    </div>
  );
}

function ComplianceControl({ value, onChange, disabled }) {
  const opts = [
    { v: 'yes', label: 'Yes', color: 'var(--success-500)' },
    { v: 'not', label: 'Not', color: 'var(--red-500)' },
    { v: 'na', label: 'N/A', color: 'var(--ink-500)' },
  ];
  return (
    <div style={{ display: 'inline-flex', gap: 4, background: 'var(--ink-50)', border: '1px solid var(--border)', borderRadius: 'var(--radius-pill)', padding: 3, flex: 'none' }}>
      {opts.map((o) => {
        const active = value === o.v;
        return (
          <button
            key={o.v} type="button" disabled={disabled}
            onClick={() => onChange(active ? '' : o.v)}
            style={{
              padding: '5px 12px', borderRadius: 'var(--radius-pill)', border: 'none',
              cursor: disabled ? 'not-allowed' : 'pointer', fontFamily: 'var(--font-sans)', fontWeight: 700,
              fontSize: 12, background: active ? o.color : 'transparent', color: active ? '#fff' : 'var(--text-muted)',
              transition: 'var(--transition-base)',
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function SamdItemRow({ item, answer, readOnly, onChange }) {
  const [expanded, setExpanded] = useState(false);
  const [remarksValue, setRemarksValue] = useState(answer?.remarks || '');
  useEffect(() => { setRemarksValue(answer?.remarks || ''); }, [answer?.remarks]);
  const compliance = answer?.compliance || '';

  const commitRemarks = () => {
    const trimmed = remarksValue.trim();
    if (trimmed === (answer?.remarks || '')) return;
    onChange(item.key, { remarks: trimmed });
  };

  return (
    <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--divider-row)', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <span className="racco-mono" style={{ fontWeight: 800, fontSize: 12.5, color: 'var(--blue-600)', paddingTop: 3, flex: 'none', width: 26 }}>{item.number}.</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ margin: 0, fontSize: 13.5, color: 'var(--text-strong)', lineHeight: 1.55 }}>{item.indicator}</p>
          {item.means.length > 0 && (
            <button
              type="button" onClick={() => setExpanded((x) => !x)} className="racco-no-print"
              style={{ marginTop: 6, background: 'none', border: 'none', padding: 0, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11.5, fontWeight: 700, color: 'var(--blue-600)' }}
            >
              <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={13} />
              Means of verification ({item.means.length})
            </button>
          )}
          {expanded && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.6 }}>
              {item.means.map((m, i) => <li key={i}>{m}</li>)}
            </ul>
          )}
        </div>
        <div style={{ flex: 'none' }}>
          {readOnly ? (
            <Badge tone={compliance === 'yes' ? 'success' : compliance === 'not' ? 'danger' : compliance === 'na' ? 'neutral' : 'neutral'} size="sm">
              {compliance ? COMPLIANCE_LABEL[compliance] : 'Unanswered'}
            </Badge>
          ) : (
            <ComplianceControl value={compliance} onChange={(v) => onChange(item.key, { compliance: v })} />
          )}
        </div>
      </div>
      {readOnly ? (
        answer?.remarks && <div style={{ marginLeft: 38, fontSize: 12.5, color: 'var(--text-muted)', fontStyle: 'italic' }}>{answer.remarks}</div>
      ) : (
        <div style={{ marginLeft: 38 }} className="racco-no-print">
          <TextField
            value={remarksValue} onChange={(e) => setRemarksValue(e.target.value)} onCommit={commitRemarks}
            placeholder="Remarks / findings (optional)"
          />
        </div>
      )}
    </div>
  );
}

function KraPanel({ kra, kraScore, responsesMap, readOnly, onChange }) {
  const groups = useMemo(() => groupBySection(kra.items), [kra]);
  return (
    <div>
      <ConfidenceMeter value={kraScore.pct} label={`${kra.key}. ${kra.title} — actual score ${kraScore.actual_score}/${kraScore.total}`} tone={bandTone(kraScore.pct)} style={{ marginBottom: 8 }} />
      <div style={{ display: 'flex', gap: 14, marginBottom: 18, fontSize: 11.5, color: 'var(--text-faint)' }}>
        <span>Yes {kraScore.yes}</span><span>Not {kraScore.not}</span><span>N/A {kraScore.na}</span><span>Unanswered {kraScore.unanswered}</span>
      </div>
      {groups.map((g, gi) => (
        <div key={gi} style={{ marginBottom: 18 }}>
          {g.label && <div className="racco-eyebrow" style={{ fontSize: 10.5, margin: '0 0 8px' }}>{g.label}</div>}
          <Card padding="0">
            {g.items.map((item) => (
              <SamdItemRow key={item.key} item={item} answer={responsesMap[item.key]} readOnly={readOnly} onChange={onChange} />
            ))}
          </Card>
        </div>
      ))}
    </div>
  );
}

function PrintSummary({ round, checklist }) {
  if (!round || !checklist) return null;
  const { scores } = round;
  const itemByKey = Object.fromEntries(checklist.kras.flatMap((k) => k.items.map((i) => [i.key, i])));
  const responsesMap = Object.fromEntries((round.responses || []).map((r) => [r.item_key, r]));
  const notItems = Object.values(responsesMap)
    .filter((r) => r.compliance === 'not')
    .map((r) => ({ ...r, item: itemByKey[r.item_key] }))
    .filter((r) => r.item)
    .sort((a, b) => a.item.key.localeCompare(b.item.key, undefined, { numeric: true }));

  return (
    <div className="racco-print-only">
      <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 20, margin: '0 0 2px' }}>SAMD Certification-Readiness — {round.label}</h1>
      <p style={{ fontSize: 11, color: '#555', margin: '0 0 18px' }}>
        Self-assessment against NACC-SAMD-GF-000 (June 2025) · Created {new Date(round.created_at).toLocaleDateString()}
        {round.completed_at ? ` · Completed ${new Date(round.completed_at).toLocaleDateString()}` : ''}
      </p>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 20, fontSize: 12 }}>
        <thead>
          <tr>
            {['KRA', 'Total', 'Yes', 'Not', 'N/A', 'Unanswered', 'Actual Score', '%'].map((h) => (
              <th key={h} style={{ textAlign: 'left', borderBottom: '1px solid #999', padding: '4px 8px' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {scores.kras.map((k) => (
            <tr key={k.key}>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.key}. {k.title}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.total}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.yes}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.not}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.na}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.unanswered}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.actual_score}</td>
              <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{k.pct}%</td>
            </tr>
          ))}
          <tr style={{ fontWeight: 700 }}>
            <td style={{ padding: '4px 8px' }}>Total</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.total}</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.yes}</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.not}</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.na}</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.unanswered}</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.actual_score}</td>
            <td style={{ padding: '4px 8px' }}>{scores.overall.pct}% — {scores.overall.band}</td>
          </tr>
        </tbody>
      </table>

      <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 15, margin: '0 0 8px' }}>
        30-day action plan — items marked &ldquo;Not&rdquo; ({notItems.length})
      </h2>
      {notItems.length === 0 ? (
        <p style={{ fontSize: 12, color: '#555' }}>No indicators were marked &quot;Not&quot; in this round.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
          <thead>
            <tr>
              {['Item', 'Indicator', 'Remarks'].map((h) => (
                <th key={h} style={{ textAlign: 'left', borderBottom: '1px solid #999', padding: '4px 8px' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {notItems.map((r) => (
              <tr key={r.item_key}>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd', whiteSpace: 'nowrap' }}>{r.item.key}</td>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{r.item.indicator}</td>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #ddd' }}>{r.remarks || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RoundsList({ rounds, loading, onOpen, onCreate, creating }) {
  return (
    <div style={PAGE}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 18, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 22, color: 'var(--text-strong)', margin: 0 }}>SAMD Certification Readiness</h1>
          <p style={{ fontSize: 12.5, color: 'var(--text-muted)', fontStyle: 'italic', margin: '4px 0 0', maxWidth: 640, lineHeight: 1.5 }}>
            Internal readiness self-check against the official NACC-SAMD certification tool (NACC-SAMD-GF-000, June 2025) — not an official NACC assessment.
          </p>
        </div>
        <Button variant="primary" onClick={onCreate} disabled={creating} iconLeft={<Icon name="plus" size={17} />}>
          {creating ? 'Starting…' : 'New self-assessment'}
        </Button>
      </div>

      <Card padding="0">
        {loading ? (
          <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>Loading…</div>
        ) : rounds.length === 0 ? (
          <EmptyState
            icon={<Icon name="shield-check" size={24} />}
            title="No self-assessments yet"
            description="Start a new round to gauge certification readiness against the 83 NACC-SAMD indicators."
            action={<Button variant="primary" onClick={onCreate} disabled={creating} iconLeft={<Icon name="plus" size={16} />}>New self-assessment</Button>}
          />
        ) : (
          <div className="racco-scroll" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 640, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--ink-50)', borderBottom: '1px solid var(--border)' }}>
                  {['Label', 'Created', 'Status', 'Readiness', ''].map((h) => (
                    <th key={h} style={{ textAlign: 'left', padding: '12px 16px', fontSize: 11, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rounds.map((r) => (
                  <tr key={r.id} style={{ borderBottom: '1px solid var(--divider-row)' }}>
                    <td style={{ padding: '12px 16px', fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{r.label}</td>
                    <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-body)' }}>{new Date(r.created_at).toLocaleDateString()}</td>
                    <td style={{ padding: '12px 16px' }}>
                      <Badge tone={r.status === 'completed' ? 'brand' : 'amber'} size="sm">{r.status === 'completed' ? 'Completed' : 'In progress'}</Badge>
                    </td>
                    <td style={{ padding: '12px 16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span className="racco-mono" style={{ fontWeight: 700, fontSize: 13.5 }}>{r.summary.pct}%</span>
                        <Badge tone={bandBadgeTone(r.summary.band)} size="sm">{r.summary.band}</Badge>
                      </div>
                    </td>
                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                      <Button variant="secondary" size="sm" onClick={() => onOpen(r.id)} iconRight={<Icon name="arrow-right" size={14} />}>Open</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export default function SamdReadiness() {
  const toast = useToast();
  const [checklist, setChecklist] = useState(null);
  const [rounds, setRounds] = useState([]);
  const [loadingRounds, setLoadingRounds] = useState(true);
  const [creating, setCreating] = useState(false);
  const [current, setCurrent] = useState(null);
  const [activeKra, setActiveKra] = useState('I');
  const [labelDraft, setLabelDraft] = useState('');

  useEffect(() => {
    api.get('/samd/checklist/').then((r) => setChecklist(r.data)).catch(() => toast.error('Could not load the checklist.'));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadRounds = useCallback(() => {
    setLoadingRounds(true);
    api.get('/samd/assessments/').then((r) => setRounds(r.data)).catch(() => toast.error('Could not load self-assessments.')).finally(() => setLoadingRounds(false));
  }, [toast]);
  useEffect(() => { loadRounds(); }, [loadRounds]);

  const openRound = (id) => {
    api.get(`/samd/assessments/${id}/`).then((r) => {
      setCurrent(r.data);
      setLabelDraft(r.data.label);
      setActiveKra(checklist?.kras?.[0]?.key || 'I');
    }).catch(() => toast.error('Could not open this self-assessment.'));
  };

  const createRound = async () => {
    setCreating(true);
    try {
      const { data } = await api.post('/samd/assessments/', {});
      toast.success(`"${data.label}" started.`);
      loadRounds();
      openRound(data.id);
    } catch {
      toast.error('Could not start a new self-assessment.');
    } finally {
      setCreating(false);
    }
  };

  const backToList = () => {
    setCurrent(null);
    loadRounds();
  };

  const saveLabel = async () => {
    const trimmed = labelDraft.trim();
    if (!trimmed || trimmed === current.label) { setLabelDraft(current.label); return; }
    try {
      const { data } = await api.patch(`/samd/assessments/${current.id}/`, { label: trimmed });
      setCurrent((c) => ({ ...c, label: data.label }));
    } catch {
      toast.error('Could not rename this self-assessment.');
      setLabelDraft(current.label);
    }
  };

  const responsesMap = useMemo(
    () => Object.fromEntries((current?.responses || []).map((r) => [r.item_key, r])),
    [current],
  );

  const handleChange = async (itemKey, patch) => {
    const existing = responsesMap[itemKey] || { compliance: '', remarks: '' };
    const body = { item_key: itemKey, compliance: existing.compliance, remarks: existing.remarks, ...patch };
    try {
      const { data: scores } = await api.post(`/samd/assessments/${current.id}/respond/`, body);
      setCurrent((c) => {
        const withoutItem = (c.responses || []).filter((r) => r.item_key !== itemKey);
        const hasAnswer = body.compliance || body.remarks;
        const nextResponses = hasAnswer ? [...withoutItem, { item_key: itemKey, compliance: body.compliance, remarks: body.remarks }] : withoutItem;
        return { ...c, responses: nextResponses, scores };
      });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not save that answer.');
    }
  };

  const markComplete = async () => {
    if (!window.confirm(`Mark "${current.label}" as completed? This locks the round from further edits.`)) return;
    try {
      const { data } = await api.post(`/samd/assessments/${current.id}/complete/`);
      setCurrent(data);
      toast.success('Self-assessment marked as completed.');
    } catch {
      toast.error('Could not complete this self-assessment.');
    }
  };

  if (!current) {
    return <RoundsList rounds={rounds} loading={loadingRounds} onOpen={openRound} onCreate={createRound} creating={creating} />;
  }

  if (!checklist) {
    return <div style={PAGE}>Loading…</div>;
  }

  const readOnly = current.status === 'completed';
  const kra = checklist.kras.find((k) => k.key === activeKra) || checklist.kras[0];
  const kraScore = current.scores.kras.find((k) => k.key === kra.key);
  const overall = current.scores.overall;

  return (
    <div style={PAGE} className="racco-print-area">
      <div className="racco-no-print">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          <Button variant="ghost" onClick={backToList} iconLeft={<Icon name="arrow-left" size={17} />}>Back to rounds</Button>
          <div style={{ display: 'flex', gap: 10 }}>
            <Button variant="secondary" onClick={() => window.print()} iconLeft={<Icon name="printer" size={17} />}>Print summary</Button>
            {!readOnly && (
              <Button variant="primary" onClick={markComplete} iconLeft={<Icon name="check-circle-2" size={17} />}>Mark as completed</Button>
            )}
          </div>
        </div>

        {readOnly && (
          <Alert tone="info" icon={<Icon name="lock" size={18} />} style={{ marginBottom: 16 }} title="Completed and locked">
            This self-assessment was completed on {new Date(current.completed_at).toLocaleDateString()}. Responses can no longer be edited.
          </Alert>
        )}

        <Card padding="20px" style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap', marginBottom: 18 }}>
            <div style={{ flex: '1 1 260px', minWidth: 200 }}>
              {readOnly ? (
                <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 18, color: 'var(--text-strong)' }}>{current.label}</div>
              ) : (
                <TextField
                  value={labelDraft} onChange={(e) => setLabelDraft(e.target.value)} onCommit={saveLabel}
                  style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 16, height: 'var(--field-h)' }}
                />
              )}
              <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 4 }}>
                Created {new Date(current.created_at).toLocaleDateString()}
              </div>
            </div>
            <div>
              <div className="racco-eyebrow" style={{ fontSize: 10.5 }}>Overall readiness</div>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 40, lineHeight: 1.1 }}>{overall.pct}%</div>
            </div>
            <Badge tone={bandBadgeTone(overall.band)} size="lg">{overall.band}</Badge>
            <div style={{ display: 'flex', gap: 20, marginLeft: 'auto', flexWrap: 'wrap' }}>
              <Stat label="Yes" value={overall.yes} />
              <Stat label="Not" value={overall.not} />
              <Stat label="N/A" value={overall.na} />
              <Stat label="Unanswered" value={overall.unanswered} />
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {current.scores.kras.map((k) => (
              <div key={k.key}>
                <ConfidenceMeter value={k.pct} label={`${k.key}. ${k.title}`} tone={bandTone(k.pct)} />
                <div style={{ display: 'flex', gap: 14, marginTop: 4, fontSize: 11, color: 'var(--text-faint)' }}>
                  <span>Yes {k.yes}</span><span>Not {k.not}</span><span>N/A {k.na}</span><span>Unanswered {k.unanswered}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Tabs
          tabs={checklist.kras.map((k) => ({ id: k.key, label: `${k.key}. ${k.title}`, count: k.items.length }))}
          active={kra.key}
          onChange={setActiveKra}
          style={{ marginBottom: 20 }}
        />

        <KraPanel kra={kra} kraScore={kraScore} responsesMap={responsesMap} readOnly={readOnly} onChange={handleChange} />
      </div>

      <PrintSummary round={current} checklist={checklist} />
    </div>
  );
}
