import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import api from '../../api/client';
import { getBriefFacts } from '../../api/assistant';
import { PolishBar, usePolish } from '../../components/PolishWriting';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  Alert, Avatar, Badge, Button, ConfirmDialog, Drawer, FormField, Icon, Select, roleLabel,
} from '../../ui';
import { ADMISSION, CASE_TYPE_FIELDS, PLACEMENT, SPECIAL_NEEDS, dateFieldFor } from '../../config/caseData';
import { PURPOSE_LABEL, fmtDay, fmtTime, localDate } from './shared';
import { clock, clockRange } from '../../utils/time';
import { firstError } from '../../utils/errors';
import './drawer.css';

/* Text typed into the record's boxes, shown as typed: its line breaks kept,
 * as the printed report keeps them, and a long word or link wrapped inside
 * the box instead of running off the drawer's edge. A numbered
 * recommendation used to run together into one line. */
const TYPED_TEXT = { fontSize: 13, color: 'var(--text-body)', margin: 0, lineHeight: 1.55,
  whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' };

/* What each Pre-Assessment word means, read off Child.pre_assessment_status
 * (backend children/models.py) rather than guessed. The words stay the
 * agency's; this only says them plainly. "Answered" and "Completed" differ by
 * the case tracker, not by who did the scoring. */
const PRE_ASSESSMENT_HINT = {
  'No Consent Yet': 'No signed consent is on file yet.',
  'Not Yet Pre-Assessed': 'Consent is signed; no pre-assessment has been started.',
  'In Progress': 'A pre-assessment has been started but is not finished.',
  Answered: 'A pre-assessment is finished. The case has not moved to counseling yet.',
  Completed: 'A pre-assessment is finished and the case is now in counseling.',
};

/* How many upcoming sessions the window lists before it says "and N more". */
const UPCOMING_SHOWN = 3;

/* The record drawer, and the terminate confirmation it opens.
 *
 * Moved out of Children.jsx with ChildForm before it — the page was 1,102
 * lines and nine components, and these two are 251 of them. They travel
 * together because the drawer is the only thing that opens the modal.
 *
 * It is the shared Drawer now (focus stays inside, Escape works, focus goes
 * back to the row that opened it). The hand-built overlay had none of that,
 * and one tap outside closed it in the middle of a booking.
 */

export default function ChildDrawer({ child, upcoming = [], canEdit, canTerminate, canReopen = false, canRemoveDuplicate = false, others = [], onEdit, onTerminate, onReopen, onRemoveDuplicate, onClose }) {
  const toast = useToast();
  const navigate = useNavigate();
  const { user } = useAuth();
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

  // The first thing left to do on this record, for the record's own social
  // worker only. The server works the list out (assistant/brief_facts.py
  // next_steps) and sends it to nobody else; this shows its first line. If the
  // request fails there is simply no line - the window is whole without it.
  const ownSocialWorker = user?.role_name === 'Staff' && child.status === 'active'
    && child.social_worker != null && child.social_worker === user.id;
  const [nextStep, setNextStep] = useState(null);
  useEffect(() => {
    setNextStep(null);
    if (!ownSocialWorker) return undefined;
    let live = true;
    getBriefFacts(child.id)
      .then((f) => { if (live) setNextStep((f?.next_steps || [])[0] || null); })
      .catch(() => {});
    return () => { live = false; };
  }, [child.id, ownSocialWorker]);

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

  // "Next possible sessions" — when can this child next be counseled, given
  // their assigned psychologist's availability. Named function (not an
  // inline effect body) so booking can re-invoke it afterwards. Not asked for
  // while there is no referral: every time would be refused, so none is shown.
  const [slots, setSlots] = useState(null);
  const [slotsFailed, setSlotsFailed] = useState(false);
  const hasPsychologist = !!child.psychologist_name;
  const canSuggestSlots = child.status === 'active' && hasPsychologist && hasReferral;
  const loadSlots = useCallback(() => {
    setSlotsFailed(false);
    if (!canSuggestSlots) { setSlots(null); return; }
    api.get(`/availability/next-slots/?child=${child.id}`)
      .then((r) => setSlots(r.data))
      .catch(() => { setSlots(null); setSlotsFailed(true); });
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
      toast.success(`Booked — ${pendingSlot.weekday} ${pendingSlot.date} at ${clock(pendingSlot.start)}`);
      setPendingSlot(null);
      loadSlots();
    } catch (err) {
      toast.error(firstError(err.response?.data, 'Could not book this slot.'));
      setPendingSlot(null);
      loadSlots();
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
  const hasRecommendationContent = !!child.recommendation;

  /* Every field the window always had, now under four headings. Within a
     heading the rows with an answer come first and the empty ones ("—") last,
     so a long run of dashes does not push the answers off the screen. */
  const groups = [
    ['Identity', [
      ['Category', child.case_category || '—'],
      ['Middle Name', child.middle_name || '—'],
      ...(child.alias ? [['Alias', child.alias]] : []),
      ['Sex', child.gender || '—'],
      ...(child.date_found ? [['Date Found', child.date_found]] : []),
      ['Place of Birth or Place Found', child.place_of_birth_or_found || '—'],
      ['Birth Status', child.birth_status || '—'],
      ['Legal Status', child.legal_status || '—'],
      ...(child.legal_status && child.legal_status_date ? [['Date Issued', child.legal_status_date]] : []),
      ...(asked('type_of_adoption') || child.type_of_adoption ? [['Type of Adoption', child.type_of_adoption || '—']] : []),
    ]],
    ['Care', [
      ['Health Condition', child.health_condition
        ? `${child.health_condition}${child.health_condition === SPECIAL_NEEDS && child.special_needs ? `: ${child.special_needs}` : ''}` : '—'],
      ['Current Whereabouts', child.current_placement || '—'],
      ['Educational Placement', child.education_level || '—'],
      ['Pre-Assessment', child.pre_assessment_status || '—', PRE_ASSESSMENT_HINT[child.pre_assessment_status]],
    ]],
    ['People', [
      ['Assigned Psychologist', child.psychologist_name || '—'],
      // Asked, not yet accepted (backend children/assignment.py) - and the last
      // answer when it was no. Both come only to the ISA and social workers.
      ...(child.pending_assignment
        ? [['Waiting on', `${child.pending_assignment.psychologist_name} to accept`]] : []),
      ...(!child.pending_assignment && child.declined_assignment
        ? [['Declined by', `${child.declined_assignment.psychologist_name}: “${child.declined_assignment.reason}”`]] : []),
      ['Social Worker', child.social_worker_name || 'No social worker yet'],
      // Who the child lives with now, and whether they get appointment texts
      // (backend children/custodian.py).
      ...(asked('custodian_name') || child.custodian_name ? [['Custodian', child.custodian_name || '—']] : []),
      ...(child.custodian_contact_display || asked('custodian_name')
        ? [['Contact Number', child.custodian_contact_display
          ? `${child.custodian_contact_display} · ${child.custodian_texts?.status || ''}`
          : '—']] : []),
    ]],
    ['Address & dates', [
      ['Address', location],
      ...(child.landmark ? [['Landmark', child.landmark]] : []),
      ...dateRows,
    ]],
  ].map(([heading, rows]) => [heading, [...rows.filter((r) => r[1] !== '—'), ...rows.filter((r) => r[1] === '—')]]);

  const today = localDate(new Date());
  const moreUpcoming = Math.max(0, upcoming.length - UPCOMING_SHOWN);
  const asking = child.pending_assignment;
  const declined = !asking && child.declined_assignment;
  const askFromEdit = canEdit ? ' Use Edit to ask one.' : '';

  const footer = (canEdit || canTerminate || showReopen || canRemoveDuplicate) ? (
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {showReopen && (
        <Button variant="primary" fullWidth onClick={onReopen} iconLeft={<Icon name="rotate-ccw" size={16} />}>Reopen Case</Button>
      )}
      {(canEdit || canTerminate || canRemoveDuplicate) && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          {/* Edit is the main action; the ones that end or undo something sit
              on the other side of the row so they are not pressed by habit.
              Remove-duplicate is the ISA's, only for a record added by
              mistake, and the server says whether this one may go. */}
          <span style={{ display: 'inline-flex', gap: 10, flexWrap: 'wrap' }}>
            {canTerminate && (
              <Button variant="secondary" onClick={onTerminate} iconLeft={<Icon name="archive" size={16} />}
                style={{ color: 'var(--red-600)', border: '1px solid var(--red-300)' }}>Terminate Case</Button>
            )}
            {canRemoveDuplicate && <Button variant="ghost" onClick={onRemoveDuplicate} iconLeft={<Icon name="trash-2" size={16} />}>Remove duplicate record…</Button>}
          </span>
          {canEdit && <Button variant="primary" onClick={onEdit} iconLeft={<Icon name="pencil" size={16} />}>Edit</Button>}
        </div>
      )}
    </div>
  ) : null;

  return (
    <>
      <Drawer
        className="racco-child-drawer"
        width={980}
        title={child.fullname}
        subtitle={<span className="racco-mono">{child.ref}</span>}
        avatar={<Avatar name={child.fullname} tone="brand" size="lg" />}
        onClose={onClose}
        // A tap outside (or Escape, or the X) must not throw away a booking
        // question that is half answered: it only withdraws the question, and
        // the next one closes the window. The replace question is its own
        // dialog and has its own way out.
        dismissible={!pendingSlot && !pendingReplace}
        onDismissBlocked={() => { if (pendingSlot && !bookingBusy) setPendingSlot(null); }}
        footer={footer}
      >
        {others.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '8px 12px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-100)' }}>
            <Icon name="users" size={14} style={{ color: 'var(--blue-600)' }} />
            {others.map((o, i) => <Badge key={i} tone="brand" size="sm" dot>{o.name} ({roleLabel(o.role)}) is here</Badge>)}
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Badge tone={child.status === 'inactive' ? 'neutral' : 'success'} size="md" dot>
              {child.status === 'inactive' ? 'Archived' : 'Active'}
            </Badge>
            {child.case_type && <Badge tone="neutral" size="sm">{child.case_type}</Badge>}
            {child.age != null && <span className="racco-cd-small" style={{ fontWeight: 600 }}>{child.age} yrs old ({child.group})</span>}
          </div>
          {/* The case study, sessions and remarks live on the child's own page;
              the only other way there used to be an eye icon on archived rows. */}
          <Button variant="outline" size="sm" onClick={() => navigate(`/report/child/${child.id}`)} iconRight={<Icon name="arrow-right" size={14} />}>
            Open full record
          </Button>
        </div>
        {nextStep && (
          <div role="note" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '10px 13px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-200)' }}>
            <Icon name="info" size={16} style={{ color: 'var(--blue-600)', flex: 'none' }} />
            <span style={{ flex: 1, minWidth: 180, fontSize: 13, color: 'var(--text-strong)', fontWeight: 600, lineHeight: 1.45 }}>
              <span style={{ fontWeight: 800 }}>Next step: </span>{nextStep.text}
            </span>
            {nextStep.link && (
              <Button variant="outline" size="sm" onClick={() => navigate(nextStep.link)}>Go there</Button>
            )}
          </div>
        )}
        <div className="racco-case-grid">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {groups.map(([heading, rows]) => (
              <div key={heading} className="racco-cd-group" role="group" aria-label={heading}>
                <div className="racco-eyebrow racco-cd-group-h">{heading}</div>
                {rows.map(([k, v, hint]) => (
                  <div key={k} className="racco-cd-row">
                    <span className="racco-cd-label">{k}</span>
                    <span className={v === '—' ? 'racco-cd-value racco-cd-empty' : 'racco-cd-value'}>
                      {v}
                      {hint && <span className="racco-cd-hint">{hint}</span>}
                    </span>
                  </div>
                ))}
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
                        className="racco-btn"
                        style={{ all: 'unset', cursor: 'pointer', flex: 1, minWidth: 0, fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 700, color: 'var(--text-link)', textDecoration: 'underline', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      >
                        {row.original_filename || 'Case referral'}
                      </button>
                      <span className="racco-cd-small" style={{ whiteSpace: 'nowrap' }}>
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
                    <div className="racco-cd-small">On file. Loading the document…</div>
                  )}
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 13px', borderRadius: 'var(--radius-lg)', background: 'var(--amber-50, var(--ink-50))', border: '1px solid var(--amber-300, var(--border))', flexWrap: 'wrap' }}>
                  <Icon name="alert-triangle" size={17} style={{ color: 'var(--amber-600, var(--text-muted))', flex: 'none' }} />
                  <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-strong)' }}>Not uploaded</span>
                  <span className="racco-cd-small" style={{ flex: 1, minWidth: 0 }}>
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
              {/* Who referred the child sits with the referral file, not under
                  a heading of its own. */}
              {child.referral_source && (
                <div className="racco-cd-row" style={{ marginTop: 10 }}>
                  <span className="racco-cd-label">Referral Source</span>
                  <span className="racco-cd-value">{child.referral_source}</span>
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
                  {upcoming.slice(0, UPCOMING_SHOWN).map((a) => {
                    const isToday = a.start.slice(0, 10) === today;
                    return (
                      // The calendar does not open a single appointment from a
                      // link, so this goes to the calendar itself.
                      <button key={a.id} type="button" className="racco-cd-appt" onClick={() => navigate('/schedule')}
                        title="Open the calendar"
                        style={{ background: isToday ? 'var(--amber-50)' : 'var(--ink-50)', border: `1px solid ${isToday ? 'var(--amber-200)' : 'var(--border)'}` }}>
                        <Icon name="calendar" size={15} style={{ color: isToday ? 'var(--amber-600)' : 'var(--blue-600)', flex: 'none' }} />
                        <span style={{ minWidth: 0, flex: 1 }}>
                          <span style={{ display: 'block', fontSize: 13, fontWeight: 700, color: 'var(--text-strong)' }}>
                            {isToday ? 'Today' : fmtDay(a.start)} · {fmtTime(a.start)}
                          </span>
                          <span className="racco-cd-small" style={{ display: 'block' }}>
                            {PURPOSE_LABEL[a.purpose] || a.purpose}{a.psychologist_name ? ` · ${a.psychologist_name}` : ''}
                          </span>
                        </span>
                        <Icon name="chevron-right" size={15} style={{ color: 'var(--text-muted)', flex: 'none' }} />
                      </button>
                    );
                  })}
                  {moreUpcoming > 0 && (
                    <button type="button" className="racco-btn racco-cd-small" onClick={() => navigate('/schedule')}
                      style={{ all: 'unset', cursor: 'pointer', fontFamily: 'var(--font-sans)', fontSize: 12.5, fontWeight: 700, color: 'var(--text-link)', textDecoration: 'underline' }}>
                      and {moreUpcoming} more - see calendar
                    </button>
                  )}
                </div>
              </div>
            )}
            {child.status === 'active' && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                  <div className="racco-eyebrow" style={{ fontSize: 10 }}>
                    Book the next session — pick a time{slots?.psychologist ? ` with ${slots.psychologist}` : ''}
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => navigate(hasReferral && hasPsychologist ? `/schedule?book=1&child=${child.id}` : '/schedule')}
                    iconLeft={<Icon name="calendar" size={14} />}>
                    Open calendar
                  </Button>
                </div>
                {/* Times are only drawn when a booking could go through: the
                    server decides in the end, but a green chip that is refused
                    after the click is a promise broken. Otherwise one plain
                    line says what is in the way. */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {!hasReferral && (
                    <p className="racco-cd-small" style={{ margin: 0 }}><strong>Upload the case referral first.</strong> No session can be booked until it is on file.</p>
                  )}
                  {!hasPsychologist && asking && (
                    <p className="racco-cd-small" style={{ margin: 0 }}>Waiting on {asking.psychologist_name} to accept. Times show here once they do.</p>
                  )}
                  {!hasPsychologist && declined && (
                    <p className="racco-cd-small" style={{ margin: 0 }}>{declined.psychologist_name} declined, so no psychologist holds this child.{askFromEdit}</p>
                  )}
                  {!hasPsychologist && !asking && !declined && (
                    <p className="racco-cd-small" style={{ margin: 0 }}>No psychologist yet, so there are no times to offer.{askFromEdit}</p>
                  )}
                  {canSuggestSlots && slotsFailed && (
                    <p className="racco-cd-small" style={{ margin: 0 }}>The times could not be loaded. Open the calendar to book.</p>
                  )}
                  {canSuggestSlots && !slots && !slotsFailed && <p className="racco-cd-small" style={{ margin: 0 }}>Looking for open times…</p>}
                  {canSuggestSlots && slots && slots.slots?.length === 0 && (
                    <p className="racco-cd-small" style={{ margin: 0 }}>{slots.reason || 'There is no open time in the next two weeks.'}</p>
                  )}
                </div>
                {canSuggestSlots && slots?.slots?.length > 0 && (
                  <>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {slots.slots.map((s, i) => (
                        <button key={i} type="button" className="racco-cd-slot" onClick={() => { setPendingSlot(s); setPurpose(defaultPurpose); }}>
                          {s.weekday.slice(0, 3)} {s.date.slice(5)} · {clockRange(s.start, s.end)}
                        </button>
                      ))}
                    </div>
                    {pendingSlot && (
                      <div style={{ marginTop: 10, padding: '11px 13px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-200)', display: 'flex', flexDirection: 'column', gap: 9 }}>
                        <span style={{ fontSize: 12.5, color: 'var(--text-strong)', fontWeight: 600 }}>
                          Book {child.fullname} with {slots.psychologist} — {pendingSlot.weekday} {pendingSlot.date} at {clock(pendingSlot.start)}?
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
                  </>
                )}
              </div>
            )}
            {(child.instruments_used || []).length > 0 && (
              <div>
                <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 4 }}>Instrument titles used</div>
                <span className="racco-cd-hint" style={{ marginTop: 0, marginBottom: 8 }}>The tests (instruments) given to this child, by title.</span>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {child.instruments_used.map((t) => <Badge key={t} tone="brand" size="sm">{t}</Badge>)}
                </div>
              </div>
            )}
            {hasRecommendationContent && (
              <div>
                <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 4 }}>Recommendation</div>
                <p style={TYPED_TEXT}>{child.recommendation}</p>
              </div>
            )}
            {child.referral_reason && (
              <div>
                <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 6 }}>Referral reason</div>
                <p style={TYPED_TEXT}>{child.referral_reason}</p>
              </div>
            )}
            {child.medical_notes && (
              <div>
                <div className="racco-eyebrow" style={{ fontSize: 10, marginBottom: 6 }}>Medical notes</div>
                <p style={TYPED_TEXT}>{child.medical_notes}</p>
              </div>
            )}
          </div>
        </div>
      </Drawer>

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
    </>
  );
}

/* Which reasons are offered comes from the server (GET closure-reasons/,
 * backend children/termination.py), which the terminate endpoint also
 * refuses by - so the dialog cannot offer a reason the save would refuse.
 *
 * A psychologist closes for where the clinical work ended, and each reason is
 * open only when the record bears it out: "Counseling completed" needs a
 * session marked completed, the pre-assessment reasons need a completed
 * pre-assessment and no counseling. The ones not open are shown greyed with
 * why - hidden, "Counseling completed" would simply be missing, and nobody
 * would know it wants a session recorded first. The ISA keeps the case-outcome
 * list as a dropdown (owner, 28 Sep 2026: "the psychologist side only"). */
export function TerminateModal({ child, onConfirm, onClose }) {
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  const polisher = usePolish(note, setNote);
  const [offer, setOffer] = useState(null);
  const [loadError, setLoadError] = useState('');
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  useEffect(() => {
    let live = true;
    api.get(`/children/${child.id}/closure-reasons/`)
      .then((r) => { if (live) setOffer(r.data); })
      .catch((err) => { if (live) setLoadError(err.response?.data?.detail || 'The reasons could not be loaded. Close this and try again.'); });
    return () => { live = false; };
  }, [child.id]);
  const clinical = !!offer?.facts;
  const facts = offer?.facts;
  const chosen = offer?.reasons.find((r) => r.value === reason);
  // In a portal above the record window (the shared Drawer is a portal at 80):
  // drawn where the page put it, this sat BEHIND the window it was opened from.
  return createPortal(
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 90, animation: 'racco-fade-in var(--dur-base) var(--ease-out)' }}>
      <div role="dialog" aria-modal="true" aria-label={`Terminate ${child.fullname}'s case`} onClick={(e) => e.stopPropagation()} style={{ width: clinical ? 540 : 460, maxWidth: '92%', maxHeight: '92vh', overflowY: 'auto', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', padding: 22, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>Terminate case — {child.fullname}</div>
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 4 }}>
            The record becomes <strong>Inactive (Terminated)</strong> and is archived from active caseloads. A reason is required.
          </div>
        </div>
        {loadError && <Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>{loadError}</Alert>}
        {!offer && !loadError && <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: 0 }}>Loading the reasons…</p>}
        {clinical && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span className="racco-eyebrow" style={{ fontSize: 10, marginRight: 2 }}>The record shows</span>
            <Badge tone={facts.pre_assessment_completed ? 'success' : 'neutral'} size="sm" dot>
              Pre-assessment {facts.pre_assessment_completed ? 'completed' : 'not completed'}
            </Badge>
            <Badge tone={facts.in_counseling ? 'brand' : 'neutral'} size="sm" dot>
              {facts.in_counseling ? 'In counseling' : 'Not moved to counseling'}
            </Badge>
            <Badge tone={facts.sessions_held ? 'success' : 'neutral'} size="sm" dot>
              {facts.sessions_held} counseling session{facts.sessions_held === 1 ? '' : 's'} held
            </Badge>
          </div>
        )}
        {clinical ? (
          <FormField label="Reason" required htmlFor="closure-reason-list">
            <div id="closure-reason-list" role="radiogroup" aria-label="Reason for closing" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {offer.reasons.map((r) => {
                const on = reason === r.value;
                return (
                  <button
                    key={r.value} type="button" role="radio" aria-checked={on} disabled={!r.available}
                    onClick={() => setReason(r.value)}
                    style={{ textAlign: 'left', padding: '9px 11px', borderRadius: 'var(--radius-md)', fontFamily: 'var(--font-sans)', cursor: r.available ? 'pointer' : 'not-allowed', border: `1px solid ${on ? 'var(--blue-500)' : 'var(--border)'}`, background: on ? 'var(--blue-50)' : r.available ? 'var(--surface)' : 'var(--ink-25)' }}
                  >
                    <span style={{ display: 'block', fontWeight: 700, fontSize: 13.5, color: !r.available ? 'var(--text-muted)' : on ? 'var(--blue-700)' : 'var(--text-strong)' }}>{r.value}</span>
                    <span style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                      {r.available ? r.hint : r.why}
                    </span>
                  </button>
                );
              })}
            </div>
          </FormField>
        ) : offer && (
          <FormField label="Reason" required>
            <Select value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">— Select termination reason —</option>
              {offer.reasons.map((r) => <option key={r.value}>{r.value}</option>)}
            </Select>
          </FormField>
        )}
        <FormField label="Closing summary" required htmlFor="terminate-closing-summary">
          <textarea id="terminate-closing-summary" value={note} onChange={(e) => setNote(e.target.value)} rows={4} placeholder="Describe why this case is being terminated…"
            style={{ width: '100%', resize: 'vertical', padding: '11px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.55 }} />
          <PolishBar polisher={polisher} value={note} />
        </FormField>
        <div style={{ display: 'flex', gap: 10 }}>
          <Button variant="secondary" fullWidth onClick={onClose}>Cancel</Button>
          <Button variant="danger" fullWidth disabled={!chosen?.available || !note.trim()} onClick={async () => { const closing = note.trim(); if (await onConfirm(child, reason, closing)) polisher.settle(closing); }} iconLeft={<Icon name="archive" size={16} />}>Terminate</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* The intake form is five sections long, which as one scroll is a wall of
 * fields that staff abandon halfway. Paged instead, matching the agency's own
 * profiling sequence, with the pills doubling as progress and as navigation —
 * a correction on an earlier page is one click away, not a scroll hunt. */
