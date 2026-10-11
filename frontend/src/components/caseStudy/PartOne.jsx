import { Card } from '../../ui';
import { shortDate } from '../../utils/time';
import { partOneRows } from './model';

/* Part I of block A, Identifying Information. It is the child's record, not a
 * box: read from `record_facts`, never typed here, and changed on the record
 * itself (the social worker's "Edit on the record"). */
export default function PartOne({ facts, actions = null, footer = null }) {
  return (
    <Card title="I. Identifying information" padding="0" actions={actions}>
      <div style={{ padding: '4px 15px 10px' }}>
        {partOneRows(facts, shortDate).map((row) => (
          <div key={row.short} style={{ display: 'flex', gap: 12, padding: '6px 0', borderBottom: '1px solid var(--ink-50)' }}>
            <span style={{ width: 200, flex: 'none', fontWeight: 700, fontSize: 12, color: 'var(--text-muted)' }}>{row.short}</span>
            <span style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 12.5, color: 'var(--text-strong)', overflowWrap: 'anywhere' }}>{row.value || '—'}</span>
          </div>
        ))}
      </div>
      {footer && <div style={{ padding: '9px 15px', background: 'var(--ink-50)', fontSize: 12, color: 'var(--text-muted)' }}>{footer}</div>}
    </Card>
  );
}
