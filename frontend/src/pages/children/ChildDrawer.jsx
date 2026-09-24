import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../../api/client';
import { useToast } from '../../context/ToastContext';
import {
  Alert, Avatar, Badge, Button, ConfirmDialog, FormField, Icon, iconBtn, hoverLift, Select, roleLabel,
} from '../../ui';
import { ADMISSION, CASE_TYPE_FIELDS, PLACEMENT, TERMINATION_REASONS, dateFieldFor } from '../../config/caseData';
import { PURPOSE_LABEL, StatusChip, fmtDay, fmtTime, localDate } from './shared';

/* The record drawer, and the terminate confirmation it opens.
 *
 * Moved out of Children.jsx with ChildForm before it — the page was 1,102
 * lines and nine components, and these two are 251 of them. They travel
 * together because the drawer is the only thing that opens the modal.
 * Nothing in either body changed.
 */

export default function ChildDrawer({ child, upcoming = [], canEdit, canTerminate, canReopen = false, others = [], onEdit, onTerminate, onReopen, onClose }) {
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  // "Next possible sessions" — when can this child next be counseled, given
  // their assigned psychologist's availability. Named function (not an
  // inline effect body) so a later task can re-invoke it after booking.
  /* The referral documents themselves, not just whether one exists. The list
     gives a boolean so the chip can paint immediately; this fetch is what lets
     somebody open the file and check it, and replace it if it is the wrong
     one — which is the whole point of being able to see it. */
  const [referrals, setReferrals] = useState(null);
  const [refBusy, setRefBusy] = useState(false);
  const [replacing, setReplacing] = useState(null);   // the row being swapped out
  // { row, file } - chosen, described, and not yet sent. The picker used to run
  // straight into the upload-then-delete, so the only thing between a mis-click
  // on Replace and a referral being swapped was the file dialog itself. Asking
  // AFTER the file is chosen rather than before lets the question name both
  // documents, which is the question somebody actually wants answered.
  const [pendingReplace, setPendingReplace] = useState(null);
  const referralFileRef = useRef(null);
  const loadReferrals = useCallback(() => {
    api.get(`/case-referrals/?child=${child.id}`)
      .then((r) => setReferrals(r.data))
      .catch(() => setReferrals([]));
  }, [child.id]);
  useEffect(() => { loadReferrals(); }, [loadReferrals]);

  // Straight from the list until the fetch lands, then from the fetch — so the
  // chip is right the instant the drawer opens AND after an upload, without
  // waiting for the caseload behind it to be re-fetched.
  const hasReferral = referrals === null ? !!child.has_case_referral : referrals.length > 0;

  const openReferral = async (row) => {
    try {
      const res = await api.get(`/case-referrals/${row.id}/download/`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      window.open(url, '_blank', 'noopener');
      // Revoked on a delay: revoking immediately can beat the new tab to it.
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      toast.error('Could not open that file.');
    }
  };

  /* Replace is upload-then-remove, in that order. If the upload fails the old
     document is still there, which is the right way round: a child left with
     no referral cannot be booked, so losing the old one to a failed upload
     would be worse than keeping a wrong file a minute longer. */
  const replaceReferral = async () => {
    // Deliberately NOT cleared here: the dialog stays up, disabled, while the
    // upload runs, so the confirm button cannot be pressed twice and the
    // reader can see it is working. It closes on the way out, either way.
    const { row: replacing, file } = pendingReplace;
    setRefBusy(true);
    try {
      const fd = new FormData();
      fd.append('child', child.id);
      fd.append('file', file);
      fd.append('description', 'Case referral');
      await api.post('/case-referrals/', fd);
      try {
        await api.delete(`/case-referrals/${replacing.id}/`);
      } catch {
        toast.error('New file uploaded, but the old one could not be removed.');
      }
      toast.success('Case referral replaced');
      setReplacing(null);
      loadReferrals();
    } catch (err) {
      const detail = err.response?.data?.file || err.response?.data?.detail;
      toast.error(Array.isArray(detail) ? detail[0]
        : detail || 'Could not upload that file. PDF or Word only.');
    } finally {
      setRefBusy(false);
      setPendingReplace(null);
    }
  };

  const [slots, setSlots] = useState(null);
  const canSuggestSlots = child.status === 'active' && !!child.psychologist_name;
  const loadSlots = useCallback(() => {
    if (!canSuggestSlots) { setSlots(null); return; }
    api.get(`/availability/next-slots/?child=${child.id}`).then((r) => setSlots(r.data)).catch(() => setSlots(null));
  }, [child.id, canSuggestSlots]);
  useEffect(() => { loadSlots(); }, [loadSlots]);
  // One-click first booking — clicking a suggested slot opens an inline
  // confirm (purpose + Book/Cancel) instead of navigating to /schedule.
  const [pendingSlot, setPendingSlot] = useState(null);
  const [purpose, setPurpose] = useState(null);
  const [bookingBusy, setBookingBusy] = useState(false);
  // Answered/Completed both mean the pre-assessment was administered.
  const defaultPurpose = ['Answered', 'Completed'].includes(child.pre_assessment_status) ? 'session' : 'pre_assessment';
  const bookSlot = async () => {
    setBookingBusy(true);
    try {
      await api.post('/appointments/', {
        child: child.id, psychologist: child.psychologist,
        start: `${pendingSlot.date}T${pendingSlot.start}:00`,
        duration_minutes: 60, purpose, notes: '',
      });
      toast.success(`Booked — ${pendingSlot.weekday} ${pendingSlot.date} at ${pendingSlot.start}`);
      setPendingSlot(null);
      loadSlots();
    } catch (err) {
      const d = err.response?.data;
      toast.error(d?.start || d?.psychologist || d?.detail || 'Could not book this slot.');
    } finally { setBookingBusy(false); }
  };
  const street = [child.house_number, child.street].filter(Boolean).join(' ');
  const location = [street, child.barangay, child.municipality, child.province].filter(Boolean).join(', ') || child.address || '—';
  // The date the case records. An older record may hold the other one too,
  // from before the form asked for just one; that is shown rather than hidden.
  const dated = dateFieldFor(child.case_type, child.type_of_adoption);
  const asked = (f) => (CASE_TYPE_FIELDS[child.case_type] || []).includes(f);
  const dateRows = [
    [ADMISSION, 'Date of Admission to the Agency'],
    [PLACEMENT, 'Date of Placement to Custodian'],
  ].filter(([f]) => f === dated || child[f] || (!dated && f === ADMISSION))
    .map(([f, label]) => [label, child[f] || '—']);
  const showReopen = canReopen && child.status === 'inactive';
  const hasRecommendationContent = child.recommendation || child.referral_source;
  const fields = [
    ['Category', child.case_category || '—'],
    ['Middle Name', child.middle_name || '—'],
    ['Sex', child.gender || '—'],
    ...(child.date_found ? [['Date Found', child.date_found]] : []),
    ['Place of Birth or Place Found', child.place_of_birth_or_found || '—'],
    ['Birth Status', child.birth_status || '—'],
    ['Legal Status', child.legal_status || '—'],
    ['Educational Placement', child.education_level || '—'],
    ['Assigned Psychologist', child.psychologist_name || '—'],
    ...(asked('surrendered_by') || child.surrendered_by ? [['Previous Custodian', child.surrendered_by || '—']] : []),
    ['Address', location],
    ...(child.landmark ? [['Landmark', child.landmark]] : []),
    ...dateRows,
    ...(asked('type_of_adoption') || child.type_of_adoption ? [['Type of Adoption', child.type_of_adoption || '—']] : []),
    ['Pre-Assessment', child.pre_assessment_status || '—'],
  ];
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, zIndex: 60, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
      <div role="dialog" aria-modal="true" aria-label={`Case record for ${child.fullname}`} onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(980px, 96vw)', height: 'min(86vh, 820px)', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, background: 'var(--ink-50)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Avatar name={child.fullname} tone="brand" size="lg" />
            <div>
              <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{child.fullname}</div>
              <div className="racco-mono" style={{ fontSize: 12, color: 'var(--text-muted)' }}>{child.ref}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                <StatusChip child={child} size="md" />
                {child.case_type && <Badge tone="neutral" size="sm">{child.case_type}</Badge>}
                {child.age != null && <span style={{ fontSize: 12.5, color: 'var(--text-muted)', fontWeight: 600 }}>{child.age} yrs old ({child.group})</span>}
              </div>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close panel" title="Close" {...hoverLift({ lift: -1, shadow: 'var(--shadow-md)' })} style={iconBtn('var(--text-muted)')}><Icon name="x" size={17} /></button>
        </div>
        {others.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 24px', background: 'var(--blue-50)', borderBottom: '1px solid var(--blue-100)' }}>
            <Icon name="users" size={14} style={{ color: 'var(--blue-600)' }} />
            {others.map((o, i) => <Badge key={i} tone="brand" size="sm" dot>{o.name} ({roleLabel(o.role)}) is here</Badge>)}
          </div>
        )}
        <div className="racco-scroll" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 24px' }}>
          <div className="racco-case-grid">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {fields.map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, paddingBottom: 12, borderBottom: '1px solid var(--divider-row)' }}>
                  <span style={{ fontSize: 13, color: 'var(--text-muted)', fontWeight: 600 }}>{k}</span>
                  <span style={{ fontSize: 13.5, color: 'var(--text-strong)', fontWeight: 700, textAlign: 'right' }}>{v}</span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {child.status === 'inactive' && (child.terminations || []).length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div className="racco-eyebrow" style={{ fontSize: 10 }}>Termination history ({child.terminations.length})</div>
                  {child.terminations.map((t, i) => (
                    <div key={i} style={{ padding: '12px 14px', borderRadius: 'var(--radius-lg)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-strong)' }}>{t.reason_category}</div>
                      <p style={{ fontSize: 12.5, color: 'var(--text-body)', margin: '4px 0 0', lineHeight: 1.5 }}>{t.note}</p>
                      <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 6 }}>{t.date}{t.terminated_by ? ` · by ${t.terminated_by}` : ''}</div>
                    </div>
                  ))}
                </div>
              )}
              {(child.instruments_used || []).length > 0 && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Instrument titles used</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {child.instruments_used.map((t) => <Badge key={t} tone="brand" size="sm">{t}</Badge>)}
                  </div>
                </div>
              )}
              {hasRecommendationContent && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Recommendation</div>
                  {child.recommendation && <p style={{ fontSize: 13, color: 'var(--text-body)', margin: '0 0 10px', lineHeight: 1.55 }}>{child.recommendation}</p>}
                  {[['Referral Source', child.referral_source]]
                    .filter(([, v]) => v).map(([k, v]) => (
                      <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, paddingBottom: 10, borderBottom: '1px solid var(--divider-row)', marginBottom: 10 }}>
                        <span style={{ fontSize: 13, color: 'var(--text-muted)', fontWeight: 600 }}>{k}</span>
                        <span style={{ fontSize: 13.5, color: 'var(--text-strong)', fontWeight: 700, textAlign: 'right' }}>{v}</span>
                      </div>
                  ))}
                </div>
              )}
              {child.referral_reason && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 6 }}>Referral reason</div>
                  <p style={{ fontSize: 13, color: 'var(--text-body)', margin: 0, lineHeight: 1.55 }}>{child.referral_reason}</p>
                </div>
              )}
              {child.medical_notes && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 6 }}>Medical notes</div>
                  <p style={{ fontSize: 13, color: 'var(--text-body)', margin: 0, lineHeight: 1.55 }}>{child.medical_notes}</p>
                </div>
              )}
              {/* Whether this child can be booked at all, said before the
                  sessions are listed rather than discovered in the booking
                  form. Sessions are refused without a referral on file, and
                  the only way to learn that used to be to pick a day and read
                  the refusal. */}
              <div>
                <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Case referral</div>
                {hasReferral ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {/* One row per document, because duplicates happen — an
                        upload that looked like it failed and was tried again
                        leaves two, and you cannot tidy what you cannot see. */}
                    {(referrals || []).map((row) => (
                      <div key={row.id} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 13px', borderRadius: 'var(--radius-lg)', background: 'var(--success-50, var(--ink-50))', border: '1px solid var(--border)', flexWrap: 'wrap' }}>
                        <Icon name="check-circle" size={17} style={{ color: 'var(--success-600)', flex: 'none' }} />
                        <button
                          type="button"
                          onClick={() => openReferral(row)}
                          title="Open this file"
                          style={{ all: 'unset', cursor: 'pointer', flex: 1, minWidth: 0, fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 700, color: 'var(--text-link)', textDecoration: 'underline', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        >
                          {row.original_filename || 'Case referral'}
                        </button>
                        <span style={{ fontSize: 11.5, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                          {row.uploaded_by_name || '—'} · {(row.created_at || '').slice(0, 10)}
                        </span>
                        {canEdit && (
                          <Button
                            variant="ghost" size="sm" disabled={refBusy}
                            onClick={() => { setReplacing(row); referralFileRef.current?.click(); }}
                            iconLeft={<Icon name="refresh-cw" size={14} />}
                          >
                            Replace
                          </Button>
                        )}
                      </div>
                    ))}
                    {referrals === null && (
                      <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>On file. Loading the document…</div>
                    )}
                  </div>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 13px', borderRadius: 'var(--radius-lg)', background: 'var(--amber-50, var(--ink-50))', border: '1px solid var(--amber-300, var(--border))', flexWrap: 'wrap' }}>
                    <Icon name="alert-triangle" size={17} style={{ color: 'var(--amber-600, var(--text-muted))', flex: 'none' }} />
                    <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-strong)' }}>Not uploaded</span>
                    <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: 'var(--text-muted)' }}>
                      No sessions can be booked until it is.
                    </span>
                    {canEdit && (
                      <Button
                        variant="secondary" size="sm"
                        onClick={() => navigate(`/reports?upload=1&child=${child.id}`)}
                        iconLeft={<Icon name="upload" size={14} />}
                      >
                        Upload
                      </Button>
                    )}
                  </div>
                )}
              </div>
              <input
                ref={referralFileRef} type="file" accept=".pdf,.doc,.docx" style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = '';
                  if (f && replacing) setPendingReplace({ row: replacing, file: f });
                }}
              />
              {upcoming.length > 0 && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Upcoming appointments</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {upcoming.slice(0, 3).map((a) => {
                      const isToday = a.start.slice(0, 10) === localDate(new Date());
                      return (
                        <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 13px', borderRadius: 'var(--radius-md)', background: isToday ? 'var(--amber-50)' : 'var(--ink-50)', border: `1px solid ${isToday ? 'var(--amber-200)' : 'var(--border)'}` }}>
                          <Icon name="calendar" size={15} style={{ color: isToday ? 'var(--amber-600)' : 'var(--blue-600)', flex: 'none' }} />
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-strong)' }}>
                              {isToday ? 'Today' : fmtDay(a.start)} · {fmtTime(a.start)}
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                              {PURPOSE_LABEL[a.purpose] || a.purpose}{a.psychologist_name ? ` · ${a.psychologist_name}` : ''}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {canSuggestSlots && slots?.slots?.length > 0 && (
                <div>
                  <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 8 }}>Next possible sessions — {slots.psychologist}</div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {slots.slots.map((s, i) => (
                      <button key={i} type="button" onClick={() => { setPendingSlot(s); setPurpose(defaultPurpose); }}
                        style={{ padding: '5px 11px', borderRadius: 'var(--radius-pill)', border: '1px solid var(--success-100)', background: 'var(--success-50)', color: 'var(--success-600)', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 11.5, cursor: 'pointer' }}>
                        {s.weekday.slice(0, 3)} {s.date.slice(5)} · {s.start}–{s.end}
                      </button>
                    ))}
                  </div>
                  {pendingSlot && (
                    <div style={{ marginTop: 10, padding: '11px 13px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-200)', display: 'flex', flexDirection: 'column', gap: 9 }}>
                      <span style={{ fontSize: 12.5, color: 'var(--text-strong)', fontWeight: 600 }}>
                        Book {child.fullname} with {slots.psychologist} — {pendingSlot.weekday} {pendingSlot.date} at {pendingSlot.start}?
                      </span>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <Select value={purpose} onChange={(e) => setPurpose(e.target.value)} style={{ maxWidth: 180 }}>
                          <option value="pre_assessment">Pre-Assessment</option>
                          <option value="session">Session</option>
                          <option value="follow_up">Follow-up</option>
                        </Select>
                        <Button variant="primary" disabled={bookingBusy} onClick={bookSlot} iconLeft={<Icon name="calendar" size={15} />}>Book</Button>
                        <Button variant="ghost" onClick={() => setPendingSlot(null)}>Cancel</Button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        {(canEdit || canTerminate || showReopen) && (
          <div style={{ padding: '14px 24px', borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {showReopen && (
              <Button variant="primary" fullWidth onClick={onReopen} iconLeft={<Icon name="rotate-ccw" size={16} />}>Reopen Case</Button>
            )}
            {(canEdit || canTerminate) && (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
                {canEdit && <Button variant="secondary" onClick={onEdit} iconLeft={<Icon name="pencil" size={16} />}>Edit</Button>}
                {canTerminate && <Button variant="danger" onClick={onTerminate} iconLeft={<Icon name="archive" size={16} />}>Terminate Case</Button>}
              </div>
            )}
          </div>
        )}
      </div>

      {pendingReplace && (
        <ConfirmDialog
          onClose={() => { setPendingReplace(null); setReplacing(null); }}
          onConfirm={replaceReferral}
          busy={refBusy}
          tone="danger"
          icon={<Icon name="refresh-cw" size={19} />}
          title="Replace the case referral?"
          confirmLabel="Replace it" cancelLabel="Keep the current one"
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ padding: '10px 12px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
              <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 3 }}>Removed</div>
              <div style={{ fontSize: 13, color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                {pendingReplace.row.original_filename || 'Case referral'}
              </div>
            </div>
            <div style={{ padding: '10px 12px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-200)' }}>
              <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 3 }}>Uploaded in its place</div>
              <div style={{ fontSize: 13, color: 'var(--text-strong)', wordBreak: 'break-all' }}>{pendingReplace.file.name}</div>
            </div>
          </div>
          {/* The order is deliberate and worth saying: nothing is removed
              until the new file is safely up, because a child with no
              referral on file cannot be booked at all. */}
          <Alert tone="info" icon={<Icon name="info" size={18} />}>
            The new file uploads first. The old one is only removed once that
            has worked.
          </Alert>
        </ConfirmDialog>
      )}
    </div>
  );
}

export function TerminateModal({ child, onConfirm, onClose }) {
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 80, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
      <div role="dialog" aria-modal="true" aria-label={`Terminate ${child.fullname}'s case`} onClick={(e) => e.stopPropagation()} style={{ width: 460, maxWidth: '92%', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', padding: 22, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>Terminate case — {child.fullname}</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 4 }}>
            The record becomes <strong>Inactive (Terminated)</strong> and is archived from active caseloads. A reason is required.
          </div>
        </div>
        <FormField label="Reason" required>
          <Select value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">— Select termination reason —</option>
            {TERMINATION_REASONS.map((r) => <option key={r}>{r}</option>)}
          </Select>
        </FormField>
        <FormField label="Closing summary" required>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4} placeholder="Describe why this case is being terminated…"
            style={{ width: '100%', resize: 'vertical', padding: '11px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.55 }} />
        </FormField>
        <div style={{ display: 'flex', gap: 10 }}>
          <Button variant="secondary" fullWidth onClick={onClose}>Cancel</Button>
          <Button variant="danger" fullWidth disabled={!reason || !note.trim()} onClick={() => onConfirm(child, reason, note.trim())} iconLeft={<Icon name="archive" size={16} />}>Terminate</Button>
        </div>
      </div>
    </div>
  );
}

/* The intake form is five sections long, which as one scroll is a wall of
 * fields that staff abandon halfway. Paged instead, matching the agency's own
 * profiling sequence, with the pills doubling as progress and as navigation —
 * a correction on an earlier page is one click away, not a scroll hunt. */
