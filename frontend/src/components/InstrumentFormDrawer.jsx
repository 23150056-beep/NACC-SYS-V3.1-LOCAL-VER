import { Button, Input, Select, FormField, Alert, Icon, iconBtn, hoverLift } from '../ui';

export const CATEGORIES = [
  { v: 'cognitive', label: 'Cognitive' },
  { v: 'behavioral', label: 'Behavioral' },
  { v: 'projective', label: 'Projective' },
  { v: 'personality', label: 'Personality' },
  { v: 'developmental', label: 'Developmental' },
  { v: 'achievement', label: 'Achievement' },
  { v: 'other', label: 'Other' },
];
const AUDIENCES = [
  { v: 'child', label: 'For children' },
  { v: 'adoptive_parent', label: 'For prospective adoptive parents' },
  { v: 'both', label: 'Both' },
];
export const EMPTY_INSTRUMENT = { title: '', publisher: '', category: 'other', audience: 'child', age_range: '', notes: '', owner: '' };

// Add/edit drawer for an InstrumentCatalog entry. Reused by the admin/psychologist
// catalog page (Instruments.jsx) and by the instrument module embedded in the
// Pre-Assessment wizard's step 4 — both simply supply their own save/close handlers.
export default function InstrumentFormDrawer({ form, setForm, psychologists = [], isAdmin = false, error, onSave, onClose }) {
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, maxWidth: '94%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column', animation: 'racco-slide-left var(--dur-slow) var(--ease-out)' }}>
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--ink-50)' }}>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{form.id ? 'Edit Instrument' : 'Add Instrument Title'}</div>
          <button type="button" onClick={onClose} aria-label="Close" {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--text-muted)')}><Icon name="x" size={17} /></button>
        </div>
        <div className="racco-scroll" style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {error && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{error}</Alert>}
          <FormField label="Instrument Title" required hint="Bibliographic title only — items are never stored.">
            <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </FormField>
          <FormField label="Publisher"><Input value={form.publisher || ''} onChange={(e) => setForm({ ...form, publisher: e.target.value })} /></FormField>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <FormField label="Category">
              <Select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {CATEGORIES.map((c) => <option key={c.v} value={c.v}>{c.label}</option>)}
              </Select>
            </FormField>
            <FormField label="Age Range"><Input value={form.age_range || ''} onChange={(e) => setForm({ ...form, age_range: e.target.value })} placeholder="e.g. 6-18" /></FormField>
          </div>
          <FormField label="Audience">
            <Select value={form.audience || 'child'} onChange={(e) => setForm({ ...form, audience: e.target.value })}>
              {AUDIENCES.map((a) => <option key={a.v} value={a.v}>{a.label}</option>)}
            </Select>
          </FormField>
          <FormField label="Notes"><Input value={form.notes || ''} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></FormField>
          {isAdmin && (
            <FormField label="Owner (psychologist)">
              <Select value={form.owner || ''} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
                <option value="">— Shared (all psychologists) —</option>
                {psychologists.map((p) => <option key={p.id} value={p.id}>{p.fullname || p.username}</option>)}
              </Select>
            </FormField>
          )}
        </div>
        <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
          <Button variant="primary" fullWidth onClick={onSave} iconLeft={<Icon name="save" size={16} />}>Save</Button>
        </div>
      </div>
    </div>
  );
}
