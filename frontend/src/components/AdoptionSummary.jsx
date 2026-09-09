import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/client';
import { useAuth } from '../context/AuthContext';
import { Button, Card, Icon, Note } from '../ui';
import { STAGE_ICONS, STATUS_META } from '../api/adoption';
import { shortDate } from '../utils/time';

/* Where the adoption process shows up on a child's own chart.
 *
 * This exists because of one line in the permissions table: a psychologist may
 * read the timeline of a child they assessed, but may not see the board. The
 * board is the only other place a case appears, so without this the permission
 * would be real in the API and unreachable in the application.
 *
 * It renders nothing at all when the child has no adoption case — which is
 * most children. A card that says "not applicable" on every chart in the
 * agency is a card people learn to scroll past.
 */
export default function AdoptionSummary({ childId }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const canOpenBoard = ['Administrator', 'Staff'].includes(user?.role_name);
  const [caseRow, setCaseRow] = useState(null);

  useEffect(() => {
    let alive = true;
    api.get('/adoption/cases/', { params: { child: childId } })
      .then((r) => { if (alive) setCaseRow((r.data || [])[0] || null); })
      .catch(() => {});
    return () => { alive = false; };
  }, [childId]);

  if (!caseRow) return null;

  const m = STATUS_META[caseRow.status] || STATUS_META.on_track;

  return (
    <Card
      title="Adoption process"
      actions={canOpenBoard && (
        <Button variant="secondary" size="sm" onClick={() => navigate(`/adoption/case/${caseRow.id}`)}>
          Open the case
        </Button>
      )}
      padding="0"
    >
      <div style={{ padding: '13px 15px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 38, height: 38, borderRadius: 'var(--radius-control)', flex: 'none', background: 'var(--blue-50)', color: 'var(--blue-600)' }}>
          <Icon name={STAGE_ICONS[caseRow.stage] || 'heart-handshake'} size={20} />
        </span>
        <span style={{ flex: 1, minWidth: 180 }}>
          <span style={{ display: 'block', fontWeight: 800, fontSize: 14, color: 'var(--text-strong)' }}>
            Step {caseRow.stage} of 8 &middot; {caseRow.stage_name}
          </span>
          <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
            {caseRow.closed_at
              ? `Closed ${shortDate(caseRow.closed_at)} — ${caseRow.closure_reason}`
              : caseRow.next_action
                ? `Next: ${caseRow.next_action}`
                : 'Every requirement of this step is met.'}
          </span>
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 10px', borderRadius: 'var(--radius-pill)', background: m.bg, color: m.color, fontWeight: 800, fontSize: 11, flex: 'none' }}>
          {m.label}
        </span>
        <span className="racco-mono" style={{ fontSize: 12, color: 'var(--text-muted)', flex: 'none' }}>
          {caseRow.days_in_stage}d{caseRow.stage_target_days ? ` / ${caseRow.stage_target_days}` : ''}
        </span>
      </div>

      <div style={{ padding: '0 15px 13px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--text-muted)', marginBottom: 5 }}>
          <span>Whole journey</span>
          <span className="racco-mono">{caseRow.progress_percent}%</span>
        </div>
        <div style={{ height: 6, borderRadius: 'var(--radius-pill)', background: 'var(--divider)', overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${caseRow.progress_percent}%`, background: 'var(--blue-600)' }} />
        </div>
      </div>

      {!canOpenBoard && (
        <Note icon="eye">
          Read-only. The adoption docket is staff casework &mdash; this is here so the clinician who
          endorsed the child can see where the case has got to.
        </Note>
      )}
    </Card>
  );
}
