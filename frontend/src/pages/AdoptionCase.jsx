import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import {
  Alert, Badge, Button, Card, ConfirmDialog, FormField, Icon, IconChip, Input,
  Note, PAGE, Select,
} from '../ui';
import {
  advanceCase, closeCase, getCase, matchFamily, listFamilies,
  REQUIREMENT_STATE_META, revertCase, STAGE_ICONS, STATUS_META,
  submitRequirement, verifyRequirement, waiveRequirement,
} from '../api/adoption';
import { initialsOf } from '../utils/child';
import { shortDate } from '../utils/time';

/* One child's journey through the adoption process — spec section 5.6.
 *
 * Four blocks: who they are and where they are, the eight-stage timeline, the
 * matched family, and the docket. The timeline always renders all eight stages
 * rather than only the ones reached, because the point of the screen is that
 * the road ahead is visible to somebody who has never run this process before.
 */

const cardStyle = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden',
};

function ClockBar({ clock }) {
  const total = clock.total_days || 1;
  const used = Math.max(0, total - clock.days_left);
  const pct = Math.min(100, Math.round((used / total) * 100));
  const tone = clock.days_left < 0 ? 'var(--red-500)'
    : clock.days_left < 14 ? 'var(--warning-500)' : 'var(--success-500)';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12.5 }}>
        <span style={{ fontWeight: 600, color: 'var(--text-body)' }}>{clock.label}</span>
        <span className="racco-mono" style={{ fontWeight: 700, color: tone }}>
          {clock.days_left < 0 ? `${Math.abs(clock.days_left)}d over` : `${clock.days_left}d left`}
        </span>
      </div>
      <div style={{ height: 6, borderRadius: 'var(--radius-pill)', background: 'var(--divider)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: tone }} />
      </div>
      <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
        Started {shortDate(clock.started_at)} · {clock.duration_days} days
        {clock.extension_days ? ` + ${clock.extension_days} extension` : ''}
      </span>
    </div>
  );
}

function RequirementRow({ r, canAct, currentUserId, onSubmit, onVerify, onWaive, busy }) {
  const meta = REQUIREMENT_STATE_META[r.state] || REQUIREMENT_STATE_META.pending;
  const fileRef = useRef(null);
  // The one rule that is not about rank: you cannot sign off your own upload.
  // Shown and disabled rather than hidden, so the reason is legible instead of
  // the button just being missing.
  const isMyOwnUpload = r.submitted_by != null && r.submitted_by === currentUserId;

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11, padding: '10px 15px', borderBottom: '1px solid var(--divider-row)' }}>
      <Icon name={meta.icon} size={17} style={{ color: meta.color, marginTop: 2, flex: 'none' }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-strong)' }}>{r.label}</span>
          {r.owned_externally && (
            <Badge tone="brand" size="sm">Somebody else&rsquo;s to clear</Badge>
          )}
        </div>
        {/* Provenance, not just state: who put it there and who signed it. */}
        <div style={{ fontSize: 11.5, color: 'var(--text-muted)', marginTop: 2 }}>
          {r.state === 'waived' ? `Waived by ${r.verified_by_name || 'a colleague'} — ${r.waiver_reason}`
            : r.state === 'verified' ? `Verified by ${r.verified_by_name || 'a colleague'}${r.verified_at ? ` on ${shortDate(r.verified_at)}` : ''}`
              : r.state === 'submitted' ? `Submitted by ${r.submitted_by_name || 'a colleague'}${r.submitted_at ? ` on ${shortDate(r.submitted_at)}` : ''}, awaiting verification`
                : 'Not yet submitted'}
          {r.document_name ? ` · ${r.document_name}` : ''}
        </div>
      </div>
      {canAct && r.state !== 'verified' && r.state !== 'waived' && (
        <div style={{ display: 'flex', gap: 6, flex: 'none' }}>
          <input
            ref={fileRef} type="file" style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) onSubmit(r, f); }}
          />
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => fileRef.current?.click()}>
            {r.state === 'submitted' ? 'Replace' : 'Upload'}
          </Button>
          {r.state === 'pending' && (
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => onSubmit(r, null)}>
              Mark done
            </Button>
          )}
          {r.state === 'submitted' && (
            <Button
              variant="primary" size="sm" disabled={busy || isMyOwnUpload}
              title={isMyOwnUpload
                ? 'This is your own upload. Verification has to come from somebody else.'
                : 'Sign this off'}
              onClick={() => onVerify(r)}
            >
              Verify
            </Button>
          )}
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => onWaive(r)}>Waive</Button>
        </div>
      )}
    </div>
  );
}

export default function AdoptionCase() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const role = user?.role_name || 'Staff';
  // One casework capability, not two tiers. The module is a checklist the
  // office keeps for itself; the only per-row rule left is self-verification.
  const canAct = ['Administrator', 'Staff'].includes(role);

  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [families, setFamilies] = useState([]);
  const [waiving, setWaiving] = useState(null);   // requirement pending a reason
  const [waiverReason, setWaiverReason] = useState('');
  const [reverting, setReverting] = useState(false);
  const [revertNote, setRevertNote] = useState('');
  const [closing, setClosing] = useState(false);
  const [closeReason, setCloseReason] = useState('');
  const [blockersOpen, setBlockersOpen] = useState(false);

  const load = useCallback(() => {
    getCase(id).then(setData).catch(() => setData('error'));
  }, [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (canAct) listFamilies().then(setFamilies).catch(() => {}); }, [canAct]);

  const run = async (fn, okMessage) => {
    setBusy(true);
    try {
      await fn();
      if (okMessage) toast.success(okMessage);
      load();
      return true;
    } catch (err) {
      const body = err.response?.data;
      if (body?.blockers?.length) setBlockersOpen(true);
      toast.error(body?.detail || 'That did not go through.');
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (data === 'error') {
    return <div style={PAGE}><Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>This adoption case is unavailable.</Alert></div>;
  }
  if (!data) {
    return <div style={PAGE}><div style={{ color: 'var(--text-muted)' }}>Loading the case…</div></div>;
  }

  const m = STATUS_META[data.status] || STATUS_META.on_track;
  const blockers = data.blockers || [];
  const byStage = (n) => (data.requirements || []).filter((r) => r.stage_number === n);

  return (
    <div style={PAGE}>
      {/* Identity header. */}
      <div style={cardStyle}>
        <div className="racco-no-print" style={{ height: 44, background: 'linear-gradient(100deg, var(--blue-900), var(--blue-600))', display: 'flex', alignItems: 'center', padding: '0 14px' }}>
          <button
            type="button" onClick={() => navigate('/adoption')}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7, height: 30, padding: '0 12px', border: '1px solid rgba(255,255,255,0.28)', borderRadius: 'var(--radius-pill)', background: 'rgba(255,255,255,0.14)', color: '#fff', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}
          >
            <Icon name="arrow-left" size={16} />Adoption tracker
          </button>
        </div>

          {/* The avatar sits BELOW the bar, not lapped over it.
              Overlapping looks better and was what the mockup drew, but the
              back control lives at the bar's leading edge and the avatar is
              the first thing in the row underneath — so they land on the same
              44px of the left margin and the avatar, being later in the DOM,
              paints over the button. Measured at 1440px: 62px of horizontal
              overlap. Separating them vertically instead would need a ~94px
              bar to clear a 30px button and a 62px avatar, which is a lot of
              navy to fix a 10px collision. */}
        <div style={{ padding: '14px 18px 13px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 62, height: 62, borderRadius: '50%', flex: 'none', background: 'var(--blue-100)', color: 'var(--blue-700)', fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 22, alignSelf: 'flex-start' }}>
            {initialsOf(data.child_name)}
          </span>
          <div style={{ flex: 1, minWidth: 200 }}>
            <h2 style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 21, lineHeight: 1.2, letterSpacing: '-0.015em', color: 'var(--text-strong)' }}>{data.child_name}</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 5, flexWrap: 'wrap' }}>
              <span className="racco-mono" style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-muted)' }}>{data.child_ref}</span>
              {/* No 1px rule between the reference and the meta. It is a
                  flex item, so on a narrow window it wraps to the end of a
                  line on its own and reads as a stray mark; the mono/sans
                  contrast already separates the two. */}
              <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-body)' }}>
                Step {data.stage} of 8 · {data.stage_name}
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 10px', borderRadius: 'var(--radius-pill)', background: m.bg, color: m.color, fontWeight: 800, fontSize: 11 }}>
                {m.label}
              </span>
              {data.on_hold && (
                <Badge tone="warning" size="sm">Assessment reopened — process on hold</Badge>
              )}
              {data.closed_at && <Badge tone="neutral" size="sm">Closed · {data.closure_reason}</Badge>}
            </div>
            {data.endorsed_by?.name && (
              <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 4 }}>
                Endorsed by {data.endorsed_by.name} on {shortDate(data.endorsed_by.released_at)}
              </div>
            )}
          </div>

          {canAct && !data.closed_at && (
            <div style={{ display: 'flex', gap: 8, flex: 'none', flexWrap: 'wrap' }}>
              <Button variant="secondary" onClick={() => navigate(`/report/child/${data.child}`)} iconLeft={<Icon name="folder-open" size={17} />}>
                Full record
              </Button>
              {data.stage > 1 && (
                <Button variant="secondary" disabled={busy} onClick={() => setReverting(true)} iconLeft={<Icon name="corner-up-left" size={17} />}>
                  Step back
                </Button>
              )}
              <Button variant="secondary" disabled={busy} onClick={() => setClosing(true)}>Close case</Button>
              {/* Disabled with the reason in the tooltip, per the spec: the
                  button must say what is stopping it, not merely refuse. */}
              <Button
                variant="primary" disabled={busy || blockers.length > 0 || data.on_hold || data.stage >= 8}
                title={blockers.length
                  ? `Blocked by: ${blockers.map((b) => b.label).join(', ')}`
                  : data.on_hold ? 'The assessment has been reopened.'
                    : data.stage >= 8 ? 'This is the final step.' : 'Move to the next step'}
                onClick={() => run(() => advanceCase(data.id), 'Moved to the next step.')}
                iconLeft={<Icon name="arrow-right" size={17} />}
              >
                Advance step
              </Button>
            </div>
          )}
        </div>
      </div>

      {blockers.length > 0 && !data.closed_at && (
        <div style={{ ...cardStyle, border: '1px solid var(--warning-100)' }}>
          <div style={{ padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--warning-50)' }}>
            <IconChip icon="lock" tone="warning" style={{ background: 'var(--warning-100)' }} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: 'block', fontWeight: 800, fontSize: 13.5, color: 'var(--text-strong)' }}>
                Blocking this step
              </span>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>
                {blockers.map((b) => b.label).join(' · ')}
              </span>
            </span>
          </div>
        </div>
      )}

      {/* Process timeline — all eight, always. */}
      <Card title="Process timeline" padding="0">
        {(data.timeline || []).map((s) => {
          const done = s.state === 'done';
          const current = s.state === 'current';
          return (
            <div key={s.number} style={{ display: 'flex', gap: 13, padding: '11px 15px', borderBottom: '1px solid var(--divider-row)', background: current ? 'var(--blue-50)' : 'transparent' }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 30, height: 30, borderRadius: '50%', flex: 'none',
                background: done ? 'var(--success-500)' : current ? 'var(--blue-600)' : 'var(--surface)',
                color: done || current ? '#fff' : 'var(--text-faint)',
                border: `2px solid ${done ? 'var(--success-500)' : current ? 'var(--blue-600)' : 'var(--border-strong)'}`,
              }}>
                {done ? <Icon name="check" size={16} /> : <Icon name={STAGE_ICONS[s.number] || 'circle'} size={15} />}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'flex', alignItems: 'baseline', gap: 9, flexWrap: 'wrap' }}>
                  <span className="racco-mono" style={{ fontSize: 10.5, fontWeight: 800, color: 'var(--text-faint)' }}>
                    {String(s.number).padStart(2, '0')}
                  </span>
                  <span style={{ fontWeight: current ? 800 : 700, fontSize: 13, color: done || current ? 'var(--text-strong)' : 'var(--text-muted)' }}>
                    {s.name}
                  </span>
                  <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{s.owner_role}</span>
                  {s.completed_at && (
                    <span className="racco-mono" style={{ fontSize: 11, color: 'var(--success-700)' }}>
                      completed {shortDate(s.completed_at)}
                    </span>
                  )}
                  {current && (
                    <span className="racco-mono" style={{ fontSize: 11, color: 'var(--blue-700)' }}>
                      {s.target_days
                        ? `${data.days_in_stage} of ${s.target_days} days`
                        : `${data.days_in_stage} days in`}
                    </span>
                  )}
                </span>
                {/* The current stage expands into what is blocking it; future
                    stages stay closed so the list reads as a road, not a wall. */}
                {current && byStage(s.number).length > 0 && (
                  <span style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                    {byStage(s.number).map((r) => {
                      const rm = REQUIREMENT_STATE_META[r.state];
                      return (
                        <span key={r.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', borderRadius: 'var(--radius-pill)', background: 'var(--surface)', border: '1px solid var(--border)', fontSize: 11, fontWeight: 600, color: 'var(--text-body)' }}>
                          <Icon name={rm.icon} size={12} style={{ color: rm.color }} />{r.label}
                        </span>
                      );
                    })}
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
        {/* Matched family — empty but informative before stage 4. */}
        <Card title="Matched family" padding="14px 16px">
          {data.pap ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                <IconChip icon="home" tone="brand" size={38} />
                <span>
                  <span style={{ display: 'block', fontWeight: 800, fontSize: 15, color: 'var(--text-strong)' }}>{data.pap.family_name}</span>
                  <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>{data.pap.address || data.pap.region || '—'}</span>
                </span>
              </div>
              {[['CEA number', data.pap.cea_number],
                ['CEA expires', data.pap.cea_expiry && shortDate(data.pap.cea_expiry)],
                ['Home study expires', data.pap.home_study_expiry && shortDate(data.pap.home_study_expiry)],
                ['Children in household', data.pap.children_in_household]].map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 12, fontSize: 12.5, padding: '4px 0', borderBottom: '1px solid var(--ink-50)' }}>
                    <span style={{ width: 150, flex: 'none', fontWeight: 700, color: 'var(--text-muted)' }}>{k}</span>
                    <span style={{ fontWeight: 600, color: 'var(--text-strong)' }}>{v || '—'}</span>
                  </div>
                ))}
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
                No family matched yet. Matching happens at step 4, in conference.
                {families.length > 0 && ` ${families.filter((f) => f.status === 'eligible').length} eligible families are on the roster.`}
              </p>
              {canAct && !data.closed_at && data.stage >= 4 && families.length > 0 && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                  <FormField label="Record the conference outcome" style={{ flex: 1 }}>
                    <Select
                      size="sm" defaultValue=""
                      onChange={(e) => e.target.value && run(
                        () => matchFamily(data.id, e.target.value), 'Family matched.')}
                    >
                      <option value="">— Select family —</option>
                      {families.filter((f) => f.status === 'eligible').map((f) => (
                        <option key={f.id} value={f.id}>{f.family_name}</option>
                      ))}
                    </Select>
                  </FormField>
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="Compliance clock" padding="14px 16px">
          {(data.clocks || []).length === 0 ? (
            <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-muted)' }}>
              No statutory window is running on this case yet.
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {data.clocks.map((c) => <ClockBar key={c.id} clock={c} />)}
              <Note icon="info">
                Windows are counted from the date each authority was issued, never from the day
                the record was typed in. An extension lengthens the bar rather than resetting it,
                so an overrun stays visible.
              </Note>
            </div>
          )}
        </Card>
      </div>

      {/* Docket checklist — every requirement across every stage. */}
      <Card title="Docket checklist" padding="0"
            actions={<span style={{ fontWeight: 600, fontSize: 12, color: 'var(--text-muted)' }}>{data.progress_percent}% of the journey</span>}>
        {(data.timeline || []).map((s) => {
          const rows = byStage(s.number);
          if (!rows.length) return null;
          return (
            <div key={s.number}>
              <div style={{ padding: '8px 15px', background: 'var(--ink-25)', borderBottom: '1px solid var(--divider)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="racco-mono" style={{ fontWeight: 800, fontSize: 10.5, color: 'var(--text-faint)' }}>
                  {String(s.number).padStart(2, '0')}
                </span>
                <span style={{ fontWeight: 800, fontSize: 12, color: 'var(--text-strong)' }}>{s.name}</span>
              </div>
              {rows.map((r) => (
                <RequirementRow
                  key={r.id} r={r} canAct={canAct && !data.closed_at && !data.on_hold}
                  currentUserId={user?.id} busy={busy}
                  onSubmit={(req, file) => run(() => submitRequirement(req.id, file), 'Added to the docket.')}
                  onVerify={(req) => run(() => verifyRequirement(req.id), 'Verified.')}
                  onWaive={(req) => { setWaiving(req); setWaiverReason(''); }}
                />
              ))}
            </div>
          );
        })}
        <Note icon="gavel">
          A requirement counts as met only once somebody other than the person who submitted it has
          verified it &mdash; that one rule holds whoever you are. Waiving is the decision to proceed
          without a statutory document, and the reason you type is the only record of why.
        </Note>
      </Card>

      {waiving && (
        <ConfirmDialog
          onClose={() => setWaiving(null)}
          onConfirm={async () => {
            const ok = await run(() => waiveRequirement(waiving.id, waiverReason), 'Requirement waived.');
            if (ok) setWaiving(null);
          }}
          busy={busy || !waiverReason.trim()}
          tone="warning" icon={<Icon name="minus-circle" size={19} />}
          title={`Waive "${waiving.label}"?`}
          description="The case will be able to move on without this document. The reason below is the only record of that decision."
          confirmLabel="Waive" cancelLabel="Cancel"
        >
          <FormField label="Why is this being waived?" required>
            <Input value={waiverReason} onChange={(e) => setWaiverReason(e.target.value)}
                   placeholder="e.g. original lost in the 2019 fire; certified copy unobtainable" />
          </FormField>
        </ConfirmDialog>
      )}

      {reverting && (
        <ConfirmDialog
          onClose={() => setReverting(false)}
          onConfirm={async () => {
            const ok = await run(() => revertCase(data.id, revertNote), 'Stepped back one stage.');
            if (ok) { setReverting(false); setRevertNote(''); }
          }}
          busy={busy || !revertNote.trim()}
          tone="warning" icon={<Icon name="corner-up-left" size={19} />}
          title="Step back one stage?"
          description="This is how a mistake gets corrected. It is recorded in the case history with your note, so anybody reading it later can see why."
          confirmLabel="Step back" cancelLabel="Cancel"
        >
          <FormField label="Why?" required>
            <Input value={revertNote} onChange={(e) => setRevertNote(e.target.value)}
                   placeholder="e.g. the CDCLAA was filed against the wrong docket number" />
          </FormField>
        </ConfirmDialog>
      )}

      {closing && (
        <ConfirmDialog
          onClose={() => setClosing(false)}
          onConfirm={async () => {
            const ok = await run(() => closeCase(data.id, closeReason), 'Case closed.');
            if (ok) { setClosing(false); navigate('/adoption'); }
          }}
          busy={busy || !closeReason}
          tone="warning" icon={<Icon name="archive" size={19} />}
          title={`Close ${data.child_name}'s adoption case?`}
          description="The child's own record stays active. Closing ends this case only — a disrupted placement can be admitted again as a new one."
          confirmLabel="Close case" cancelLabel="Cancel"
        >
          <FormField label="Reason" required>
            <Select value={closeReason} onChange={(e) => setCloseReason(e.target.value)}>
              <option value="">— Select a reason —</option>
              {['Adoption finalized', 'Placement disruption', 'Reunified with biological family',
                'Aged out', 'Referred out of RACCO I'].map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </FormField>
        </ConfirmDialog>
      )}

      {blockersOpen && (
        <ConfirmDialog
          onClose={() => setBlockersOpen(false)} onConfirm={() => setBlockersOpen(false)}
          tone="warning" icon={<Icon name="lock" size={19} />}
          title="This step will not move yet"
          description="Every exit condition has to be verified or waived first. These are the ones still outstanding:"
          confirmLabel="Got it" cancelLabel={null}
        >
          <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {blockers.map((b) => (
              <li key={b.id} style={{ fontSize: 12.5, color: 'var(--text-body)' }}>{b.label}</li>
            ))}
          </ul>
        </ConfirmDialog>
      )}
    </div>
  );
}
