import { useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { QRCodeSVG } from 'qrcode.react';
import api from '../api/client';
import { ageFrom, caseRef } from '../utils/child';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { useConfirm } from '../context/ConfirmContext';
import { useAssistant } from '../context/AssistantContext';
import {
  Alert, Avatar, Badge, Button, Card, ConfirmDialog, FormField, Icon, iconBtn, Modal, Note, PAGE, Select, Tabs,
} from '../ui';
import { PA_STATUS_TONES, SPECIAL_NEEDS, caseDate, reportTypeLabel } from '../config/caseData';
import { loadAll } from '../utils/load';
import { useOpenFromLink } from '../utils/links';
import { clock } from '../utils/time';
import { polishRemark, sendFeedback, getLatestBrief, generateBrief, getBriefFacts, summarizeDocument, confirmSummary } from '../api/assistant';
import AssistantAccessLog from '../components/AssistantAccessLog';
import BriefFacts from '../components/BriefFacts';
import ReportCheckNote from '../components/ReportCheckNote';
import UploadDrawer from '../components/UploadDrawer';
import PsychReportPrint from '../components/PsychReportPrint';
import ReportViewer from '../components/ReportViewer';
import CaseStudyTab from '../components/caseStudy/CaseStudyTab';
import ScsrPrint from '../components/caseStudy/ScsrPrint';
import { useCaseStudy, usePrintExtras, useScsrPrint } from '../components/caseStudy/useCaseStudy';

// "In her own words" reads better than a label, but gender is blank=True on
// the model and must never render as an empty string.
function ownWordsTitle(child) {
  const g = String(child?.gender || '').toLowerCase();
  if (g.startsWith('f')) return 'In her own words';
  if (g.startsWith('m')) return 'In his own words';
  return "In the child's own words";
}

const CASE_STATUS_META = {
  pre_assessment: { label: 'Pre-Assessment', tone: 'amber' },
  counseling: { label: 'Counseling', tone: 'brand' },
  terminated: { label: 'Terminated', tone: 'neutral' },
};

const td = { padding: '10px 14px', fontSize: 13, color: 'var(--text-body)', whiteSpace: 'nowrap' };
const textarea = { width: '100%', resize: 'vertical', padding: '11px 13px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14, lineHeight: 1.55 };

export default function ChildProgressReport() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  // False on a hosted deployment, where the server refuses every drafting
  // feature (the chatbot is unaffected). Above the early returns below: a hook
  // under `if (!data) return` crashed this page once already.
  const { drafting, brief: briefKind } = useAssistant();
  const isPsych = user?.role_name === 'Psychologist';
  // Which brief this person gets (owner, 8 Oct 2026): the psychologist's is the
  // facts and a written brief; a social worker's and the ISA's is the case
  // facts alone, and never asks for the written one. The server says which;
  // until it answers, the role does.
  const caseBrief = (briefKind || (isPsych ? 'clinical' : 'case')) === 'case';
  const [data, setData] = useState(null);
  // Which section of the chart is showing. Every panel stays mounted — see
  // .racco-tabpanel in index.css for why the report still prints whole.
  const [tab, setTab] = useState('overview');
  // `?tab=voice` opens the survey tab, for a link that points at it. (The
  // social worker's "survey waiting" care gap used to; it was removed because
  // a social worker cannot start a survey.) Above the early returns, with the
  // other hooks.
  useOpenFromLink('tab', 'voice', () => setTab('voice'));
  // The case study's activity events link here (utils/activity.js).
  useOpenFromLink('tab', 'casestudy', () => setTab('casestudy'));
  const [ackBusy, setAckBusy] = useState(null);
  const [remarkText, setRemarkText] = useState('');
  const [result, setResult] = useState(null); // add-result drawer
  const [plan, setPlan] = useState(null); // treatment plan drawer
  const [instruments, setInstruments] = useState([]);
  const [qr, setQr] = useState(null); // { token, url }
  const [surveyTemplates, setSurveyTemplates] = useState([]);
  const [openInterviews, setOpenInterviews] = useState({}); // interview id -> expanded?
  // Advancing a case is a clinical state change other screens key off, and there
  // is no undo button for it — so it is asked for, not just clicked. Declared up
  // here with the other state: everything below the early returns runs
  // conditionally, and a hook that only sometimes runs breaks the render.
  const [confirmMove, setConfirmMove] = useState(null); // { next, childName }
  const [polishing, setPolishing] = useState(false);
  const [polishJob, setPolishJob] = useState(null); // { id, draft }
  // The remark text as it stood before "Polish writing" overwrote it, so the
  // psychologist has a way back to their own words if the draft is worse.
  // Cleared once the remark is saved or the draft is reverted.
  const [preRemarkText, setPreRemarkText] = useState(null);
  // { childId, facts, factsFailed, prose: 'loading'|'ready'|'unavailable'|'failed'|'not_offered'|'none', draft, generatedAt, jobId }
  // ('none' is the case brief: there is no written part to wait for.)
  // childId is the child it was opened for: a reply for any other child is
  // dropped, so a slow request cannot fill another child's modal.
  const [brief, setBrief] = useState(null);
  const [briefBusy, setBriefBusy] = useState(false);
  // The child this page is on, for replies to compare against when they land;
  // the request counter, so a finished request can tell it is no longer the
  // latest one; and the prose request still running, if any, so reopening the
  // modal mid-draft waits for it instead of asking the model a second time.
  const currentChild = useRef(id);
  const briefSeq = useRef(0);
  const briefFlight = useRef(null); // { childId, seq }
  // Another child's route re-uses this component. Everything about the last
  // child's brief goes, and any request still out for it is orphaned.
  useEffect(() => {
    currentChild.current = id;
    briefSeq.current += 1;
    briefFlight.current = null;
    setBrief(null);
    setBriefBusy(false);
  }, [id]);
  const [summary, setSummary] = useState(null); // { kind, id, text, confirmed }
  const [summaryBusy, setSummaryBusy] = useState(false);
  // Re-summarising an already-confirmed document destroys the psychologist's
  // own clinical text with no undo, so that path is asked for, not clicked.
  const [confirmResummarize, setConfirmResummarize] = useState(null); // { kind, id, filename }
  // Resolving a problem is one unlabelled tick in a row of them, and there is
  // no unresolve anywhere in this app - the watchlist entry simply goes quiet.
  // Cheap to ask, expensive to get wrong on the wrong row.
  const [resolving, setResolving] = useState(null); // the problem awaiting confirmation
  const [resolveBusy, setResolveBusy] = useState(false);
  // Filing a report or a referral from the record itself, rather than sending
  // the psychologist off to Results & Reports and leaving them there.
  const [upload, setUpload] = useState(null); // 'report' | 'case_referral'
  const [viewing, setViewing] = useState(null); // the report being read on screen
  const isStaffOrAdmin = ['Administrator', 'Staff'].includes(user?.role_name);
  // The ISA's log of who had the assistant read this record; nobody else sees it.
  const isAdmin = user?.role_name === 'Administrator';
  // Whoever may start a survey asks the invite endpoint which forms it may use.
  const canStartSurvey = ['Administrator', 'Staff', 'Psychologist'].includes(user?.role_name);

  const load = () => api.get(`/reports/child/${id}/`).then((r) => setData(r.data)).catch(() => setData('error'));

  // The Social Case Study Report belongs to Adoption records only, and the tab
  // exists only if the server does not answer 404 for this reader (components/
  // caseStudy/useCaseStudy.js). Above the early returns, like every hook here.
  // `data` is still the last child's until the new one arrives (this component
  // is re-used between routes), so it counts only once it is this child's.
  const dataIsThisChild = !!data && data !== 'error' && String(data.child?.id) === String(id);
  const isAdoption = dataIsThisChild && data.child.case_type === 'Adoption';
  const caseStudy = useCaseStudy(id, isAdoption);
  const hasCaseStudyTab = caseStudy.phase === 'ready' || caseStudy.phase === 'error';
  // Print follows the tab: on the Case study tab the social worker who holds
  // the record prints the case study; everyone else, and every other tab, the
  // psychological report. The agency header and the license are read only once
  // there is a case study to put them on.
  const printsCaseStudy = tab === 'casestudy' && user?.role_name === 'Staff'
    && caseStudy.phase === 'ready' && !!caseStudy.study?.exists;
  // What the print element holds: the newest FINAL copy once the case study is
  // final (it carries its own preparer, license and agency, so nothing live is
  // read for it), the saved draft otherwise - whose agency header and license
  // are read only then.
  const printTarget = useScsrPrint(id, caseStudy.study, printsCaseStudy);
  const printExtras = usePrintExtras(printsCaseStudy && printTarget.mode === 'draft');
  // Landed on the tab by a link, or left on it by a record that has none.
  const caseStudyGone = caseStudy.phase === 'none' || (dataIsThisChild && !isAdoption);
  useEffect(() => {
    if (tab === 'casestudy' && caseStudyGone) setTab('overview');
  }, [tab, caseStudyGone]);

  const resolveProblem = async () => {
    setResolveBusy(true);
    try {
      await api.patch(`/problems/${resolving.id}/`, { resolved: true });
      setResolving(null);
      load();
    } catch { toast.error('Could not update.'); }
    finally { setResolveBusy(false); }
  };

  const acknowledgeFlag = async (flagId) => {
    // It leaves the list of flags waiting for someone, so it is asked.
    if (!(await confirm({
      description: 'This marks the flag as read by you. It stays on the record, but no longer waits for anyone.',
      confirmLabel: 'Yes, mark it read',
    }))) return;
    setAckBusy(flagId);
    try {
      await api.post(`/self-report-flags/${flagId}/acknowledge/`, {});
      await load();
    } catch {
      toast.error('Could not mark that as read.');
    }
    setAckBusy(null);
  };
  useEffect(() => {
    load();
    /* eslint-disable-next-line */
  }, [id]);
  // The report's own load above renders a real error state. These two are
  // additive - they fill the instrument picker and the opinionnaire list -
  // and a failure leaves those controls looking empty rather than broken.
  useEffect(() => {
    setSurveyTemplates([]); // another child's forms are not this child's
    loadAll(toast, [
      isPsych && (() => api.get('/instruments/').then((r) => setInstruments(r.data))),
      // Not /form-templates/, which a social worker may not read: the invite
      // endpoint answers with the forms this child's survey may use.
      canStartSurvey && (() => api.get(`/opinionnaire-invites/templates/?child=${id}`)
        .then((r) => setSurveyTemplates(r.data))),
    ], 'Some options on this page could not load. Refresh to try again.');
  }, [isPsych, canStartSurvey, id, toast]);

  if (data === 'error') return <div style={PAGE}><Alert tone="danger" icon={<Icon name="alert-triangle" size={18} />}>This report is unavailable.</Alert></div>;
  if (!data) return <div style={PAGE}><div style={{ color: 'var(--text-muted)' }}>Loading report…</div></div>;

  const { child } = data;
  const canWrite = isPsych && String(child.psychologist) === String(user?.id);
  const canAdvance = canWrite || user?.role_name === 'Administrator';
  // A summary is written onto the document, so it follows the document's own
  // write rule (assistant/views.py _DOC_KINDS): a report is its psychologist's
  // or an administrator's, a referral a social worker's or an administrator's.
  // Offered to anyone else, "Re-summarise" replaced another person's confirmed
  // summary for good. And only where this deployment drafts at all.
  const canSummariseReport = canAdvance && drafting;
  const canSummariseReferral = isStaffOrAdmin && drafting;
  const activePlan = (data.treatment_plans || []).find((p) => p.status === 'active') || (data.treatment_plans || [])[0];
  const csMeta = CASE_STATUS_META[child.case_status] || CASE_STATUS_META.pre_assessment;

  // Sessions cannot be booked without the social worker's referral on file —
  // enforced in the booking endpoint, so the button must not pretend otherwise.
  const hasReferral = (data.case_referrals || []).length > 0;

  const age = ageFrom(child.birth_date);
  const unreviewedFlags = (data.self_report_flags || []).filter((f) => !f.is_reviewed).length;
  const heroMeta = [age != null ? `${age} y` : null, child.gender, child.case_type].filter(Boolean).join(' · ');

  /* The counts are the point of the tab strip: it says how much is behind
   * each section before you spend a click finding out. */
  const chartTabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'interviews', label: 'Interviews', count: (data.interviews || []).length || undefined },
    { id: 'instruments', label: 'Results & reports', count: ((data.result_entries || []).length + (data.reports || []).length) || undefined },
    { id: 'remarks', label: 'Remarks', count: (data.remarks || []).length || undefined },
    { id: 'voice', label: "Child's voice", count: (data.opinionnaires || []).length || undefined },
    { id: 'casework', label: 'Casework', count: (data.case_referrals || []).length || undefined },
    ...(hasCaseStudyTab ? [{ id: 'casestudy', label: 'Case study' }] : []),
    ...(isAdmin ? [{ id: 'assistant', label: 'Assistant log' }] : []),
  ];

  const facts = [
    { k: 'Case reference', v: caseRef(child.id) },
    { k: 'Age / date of birth or given date of birth', v: [age != null ? `${age} years` : null, child.birth_date].filter(Boolean).join(' · ') },
    ...(child.alias ? [{ k: 'Alias', v: child.alias }] : []),
    { k: 'Gender', v: child.gender },
    { k: 'Case type', v: child.case_type },
    { k: 'Category', v: child.case_category },
    { k: 'Legal status', v: child.legal_status },
    ...(child.legal_status && child.legal_status_date ? [{ k: 'Date issued', v: child.legal_status_date }] : []),
    { k: 'Health condition', v: child.health_condition === SPECIAL_NEEDS && child.special_needs ? `${child.health_condition}: ${child.special_needs}` : child.health_condition },
    { k: 'Current whereabouts', v: child.current_placement },
    { k: 'Address', v: [[child.house_number, child.street].filter(Boolean).join(' '), child.barangay, child.municipality, child.province].filter(Boolean).join(', ') },
    { k: 'Assigned psychologist', v: child.psychologist_name },
    { k: caseDate(child)[0], v: caseDate(child)[1] },
    { k: 'Educational placement', v: child.education_level },
  ];

  const advance = async (next) => {
    try {
      await api.post(`/children/${id}/advance-status/`, { case_status: next });
      toast.success(`Case moved to ${CASE_STATUS_META[next].label}`);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Could not update the case status.'); }
  };

  /* The forms come from /opinionnaire-invites/templates/ (shared ones, the
   * child's psychologist's, and the viewer's own), so a social worker, who
   * cannot read /form-templates/, can start a survey too. An empty list
   * names who can fix it by role.
   */
  const createInvite = async () => {
    const tpl = surveyTemplates[0];
    if (!tpl) {
      toast.error(user?.role_name === 'Staff'
        ? 'No self-report template is set up yet. Ask the ISA to add one under Pre-Assessment Instruments.'
        : 'Create a Self-Report (Government Form) template under Pre-Assessment Instruments first.');
      return;
    }
    if (!(await confirm({
      description: `This creates a QR survey link for ${child.fullname}. Anyone holding the link can answer it until it is used.`,
      confirmLabel: 'Yes, create the link',
      details: [['Form', tpl.title]],
    }))) return;
    try {
      const { data: inv } = await api.post('/opinionnaire-invites/', { child: Number(id), template: tpl.id });
      setQr({ token: inv.token, url: `${window.location.origin}/survey/${inv.token}`, title: tpl.title });
      load();
    } catch (err) { toast.error(JSON.stringify(err.response?.data || 'Could not create the survey link.')); }
  };

  const polish = async () => {
    const raw = remarkText.trim();
    if (!raw) return;
    setPolishing(true);
    try {
      const { draft, job_id } = await polishRemark(raw);
      setPreRemarkText(remarkText);
      setRemarkText(draft);
      setPolishJob({ id: job_id, draft });
    } catch (err) {
      // 503 means the assistant is off or the runtime is down. That is a normal
      // state, not an error the psychologist caused. A 422 means the draft came
      // back in the wrong language and was rejected rather than shown — the
      // server explains why, so surface its message instead of a generic one.
      toast.error(err.response?.status === 503
        ? 'The writing assistant is unavailable right now.'
        : err.response?.data?.detail || 'Could not polish the remark.');
    } finally {
      setPolishing(false);
    }
  };

  const revertPolish = () => {
    if (preRemarkText !== null) setRemarkText(preRemarkText);
    if (polishJob) sendFeedback(polishJob.id, 'discarded').catch(() => {});
    setPolishJob(null);
    setPreRemarkText(null);
  };

  const openBrief = async ({ regenerate = false } = {}) => {
    const childId = id;
    // Replies below belong to this child only.
    const mine = (b) => (b && b.childId === childId ? b : null);
    const stale = () => currentChild.current !== childId;
    const flying = briefFlight.current && briefFlight.current.childId === childId;
    // Opens at once with the facts: they are queries and arrive in well under
    // a second, while the prose can take a minute or not come at all (the
    // assistant off, or a hosted deployment, where drafting is refused).
    if (!regenerate) {
      setBrief((b) => ({
        childId,
        facts: mine(b)?.facts ?? null,
        factsFailed: false,
        // A draft still being written for this child fills the modal when it
        // lands; nothing else is asked of the model.
        prose: caseBrief ? 'none' : drafting ? 'loading' : 'not_offered',
      }));
    }
    getBriefFacts(childId)
      .then((facts) => setBrief((b) => (mine(b) ? { ...b, facts, factsFailed: false } : b)))
      .catch(() => setBrief((b) => (mine(b) ? { ...b, factsFailed: !b.facts } : b)));
    // Where the deployment does not draft, or the reader is not the child's
    // psychologist, the facts are all there is: the prose is not requested at
    // all, so nothing is refused and nothing is audited as a failed job.
    if (caseBrief || !drafting) return;
    if (!regenerate && flying) return;
    const seq = briefSeq.current + 1;
    briefSeq.current = seq;
    briefFlight.current = { childId, seq };
    setBriefBusy(true);
    try {
      const data = regenerate
        ? await generateBrief(childId)
        : await getLatestBrief(childId).catch((err) => {
            // 404 just means nothing was drafted today — fall back to the slow path.
            if (err.response?.status === 404) return generateBrief(childId);
            throw err;
          });
      // `mine` throughout: closed while the prose was drafting stays closed,
      // and a draft for another child is never shown.
      setBrief((b) => (mine(b) ? { ...b, prose: 'ready', draft: data.draft, generatedAt: data.generated_at, jobId: data.job_id } : b));
    } catch (err) {
      if (stale()) return;
      const unavailable = err.response?.status === 503;
      if (regenerate) {
        // The draft already on screen stays, as it always did.
        toast.error(unavailable ? 'The assistant is unavailable right now.' : 'Could not prepare the brief.');
        // Unless the modal was closed and reopened meanwhile: it is waiting on
        // this very request, and must not be left saying "drafting".
        setBrief((b) => (mine(b) && b.prose === 'loading'
          ? { ...b, prose: unavailable ? 'unavailable' : 'failed' } : b));
      } else {
        setBrief((b) => (mine(b) ? { ...b, prose: unavailable ? 'unavailable' : 'failed' } : b));
      }
    } finally {
      // Only the latest request may clear these: an old one finishing must
      // not unlock the button, or forget a newer draft, for a newer request.
      if (briefSeq.current === seq) {
        briefFlight.current = null;
        setBriefBusy(false);
      }
    }
  };

  const draftSummary = async (kind, docId) => {
    setSummaryBusy(true);
    try {
      const { draft, coverage } = await summarizeDocument(kind, docId);
      setSummary({ kind, id: docId, text: draft, confirmed: false, coverage });
    } catch (err) {
      toast.error(err.response?.status === 503
        ? 'The assistant is unavailable right now.'
        : err.response?.data?.detail || 'Could not summarise this document.');
    } finally {
      setSummaryBusy(false);
    }
  };

  const saveSummary = async () => {
    if (!(await confirm({
      description: 'This saves the summary, as you have left it, beside the document for everyone who can read it.',
      confirmLabel: 'Yes, save the summary',
    }))) return;
    try {
      await confirmSummary(summary.kind, summary.id, summary.text);
      setSummary(null); load(); toast.success('Summary confirmed');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not confirm the summary.');
    }
  };

  const addRemark = async () => {
    if (!remarkText.trim()) return;
    const saved = remarkText.trim();
    if (!(await confirm({
      description: `This adds the remark to ${child.fullname}'s record, under your name and today's date.`,
      confirmLabel: 'Yes, add the remark',
    }))) return;
    try {
      await api.post('/remarks/', { child: Number(id), text: saved });
      if (polishJob) {
        // Accepted if the saved text is the draft verbatim, else edited.
        const outcome = saved === polishJob.draft.trim() ? 'accepted' : 'edited';
        sendFeedback(polishJob.id, outcome).catch(() => {});
        setPolishJob(null);
      }
      setPreRemarkText(null);
      setRemarkText(''); load(); toast.success('Remark added');
    } catch (err) { toast.error(err.response?.data?.detail || 'Could not add the remark.'); }
  };

  const saveResult = async () => {
    if (!(await confirm({
      description: `This saves the result entry on ${child.fullname}'s record.`,
      confirmLabel: 'Yes, save the entry',
      details: [['Classification', result.classification]],
    }))) return;
    try {
      await api.post('/result-entries/', {
        child: Number(id), instrument: result.instrument || null,
        summary: result.summary, classification: result.classification,
        baseline_category: result.baseline_category || '',
      });
      setResult(null); load(); toast.success('Result entry saved');
    } catch (err) { toast.error(JSON.stringify(err.response?.data || 'Could not save.')); }
  };

  const savePlan = async () => {
    if (!(await confirm({
      description: plan.id ? `This saves your changes to ${child.fullname}'s treatment plan.`
        : `This starts a treatment plan for ${child.fullname}.`,
      confirmLabel: 'Yes, save the plan',
      details: [['Review date', plan.review_date]],
    }))) return;
    try {
      if (plan.id) await api.patch(`/treatment-plans/${plan.id}/`, { objectives: plan.objectives, interventions: plan.interventions, status: plan.status, review_date: plan.review_date || null });
      else await api.post('/treatment-plans/', { child: Number(id), objectives: plan.objectives, interventions: plan.interventions, review_date: plan.review_date || null });
      setPlan(null); load(); toast.success('Treatment plan saved');
    } catch (err) { toast.error(JSON.stringify(err.response?.data || 'Could not save.')); }
  };

  // A draft prints what is saved. Say so before printing over typing that has
  // not been saved yet. A final prints its newest final copy.
  const printPage = async () => {
    if (!printsCaseStudy) { window.print(); return; }
    if (printTarget.mode === 'draft' && caseStudy.unsaved > 0 && !(await confirm({
      description: 'Some boxes have changes that are not saved. The printed case study shows what is saved, not what is in the boxes.',
      confirmLabel: 'Yes, print what is saved',
    }))) return;
    await printTarget.printDefault();
  };

  const download = async (f) => {
    try {
      const res = await api.get(`/report-files/${f.id}/download/`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a'); a.href = url; a.download = f.original_filename || 'report'; a.click();
      URL.revokeObjectURL(url);
    } catch { toast.error('Could not download the file.'); }
  };


  return (
    <div style={PAGE} className="racco-print-area racco-psych-print-root">
      {/* What Print puts on paper: the psychological report, not this screen
          (index.css hides the page's other children when printing). */}
      {printsCaseStudy
        ? (printTarget.mode === 'final' && !printTarget.copy
          // The final copy is on its way; nothing live stands in for it.
          ? <div className="racco-print-only" />
          : <ScsrPrint child={child} study={caseStudy.study} copy={printTarget.copy} agency={printExtras.agency} license={printExtras.license} preparedBy={user?.fullname} />)
        : <PsychReportPrint data={data} />}
      {/* Hero. Back out to Records, who this child is, and the three things
          you came here to do — above the tab strip, so they stay put whichever
          section you are reading. */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 'var(--radius-card)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        <div className="racco-no-print" style={{ height: 44, background: 'linear-gradient(100deg, var(--blue-900), var(--blue-600))', display: 'flex', alignItems: 'center', padding: '0 14px' }}>
          <button
            type="button" onClick={() => navigate('/children')}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7, height: 30, padding: '0 12px', border: '1px solid rgba(255,255,255,0.28)', borderRadius: 'var(--radius-pill)', background: 'rgba(255,255,255,0.14)', color: '#fff', fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}
          >
            <Icon name="arrow-left" size={16} />All records
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
          <Avatar name={child.fullname} size={62} style={{ alignSelf: 'flex-start' }} />
          <div style={{ flex: 1, minWidth: 200 }}>
            <h2 style={{ fontFamily: 'var(--font-sans)', fontWeight: 800, fontSize: 21, lineHeight: 1.2, letterSpacing: '-0.015em', color: 'var(--text-strong)' }}>{child.fullname}</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 5, flexWrap: 'wrap' }}>
              <span className="racco-mono" style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-muted)' }}>{caseRef(child.id)}</span>
              {/* No 1px rule between the reference and the meta. It is a
                  flex item, so on a narrow window it wraps to the end of a
                  line on its own and reads as a stray mark; the mono/sans
                  contrast already separates the two. */}
              <span style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--text-body)' }}>{heroMeta}</span>
              <Badge tone={child.status === 'active' ? 'success' : 'neutral'} size="sm" dot>
                {child.status === 'active' ? 'Active' : 'Archived (Terminated)'}
              </Badge>
              <Badge tone={csMeta.tone} size="sm">{csMeta.label}</Badge>
              <Badge tone={PA_STATUS_TONES[child.pre_assessment_status] || 'amber'} size="sm" dot>{child.pre_assessment_status}</Badge>
              {unreviewedFlags > 0 && (
                <Badge tone="danger" size="sm">
                  {unreviewedFlags} self-report answer{unreviewedFlags === 1 ? '' : 's'} to read
                </Badge>
              )}
              {!child.psychologist_name && child.status === 'active' && (
                <Badge tone="warning" size="sm">No assigned psychologist</Badge>
              )}
            </div>
          </div>
          <div className="racco-no-print" style={{ display: 'flex', gap: 8, flex: 'none', flexWrap: 'wrap' }}>
            {/* Stays where drafting is off: the facts need no model. A social
                worker's and the ISA's is the case brief, facts only. */}
            <Button variant="secondary" onClick={() => openBrief()} iconLeft={<Icon name="sparkles" size={17} />}>
              {briefBusy ? 'Preparing…' : caseBrief ? 'Case brief' : 'Pre-session brief'}
            </Button>
            <Button variant="secondary" onClick={printPage}
              disabled={printsCaseStudy && !printTarget.ready}
              title={!printsCaseStudy ? "Print this child's psychological report"
                : !printTarget.ready ? 'Getting the final copy ready to print…'
                  : printTarget.mode === 'final' ? "Print this child's social case study report as it was made final"
                    : "Print this child's social case study report as a draft"}
              iconLeft={<Icon name="printer" size={17} />}>
              {printsCaseStudy ? (printTarget.mode === 'final' ? 'Print final copy' : 'Print case study') : 'Print'}
            </Button>
            {/* Booking a session is the thing you most often want next while
                reading a child's record, and it used to mean leaving for the
                Calendar and picking the same child out of a list again. The
                link carries the child, so the drawer opens knowing who. */}
            {child.status === 'active' && (
              <Button
                variant="secondary"
                disabled={!hasReferral}
                title={hasReferral
                  ? 'Book a session for this child'
                  : 'A case referral has to be on file before sessions can be booked.'}
                onClick={() => navigate(`/schedule?book=1&child=${child.id}`)}
                iconLeft={<Icon name="calendar-plus" size={17} />}
              >
                Book appointment
              </Button>
            )}
            {canAdvance && child.status === 'active' && (child.case_status === 'pre_assessment'
              ? <Button variant="primary" onClick={() => setConfirmMove({ next: 'counseling', childName: child.fullname })} iconLeft={<Icon name="chevron-right" size={16} />}>Move to Counseling</Button>
              : <Button variant="secondary" onClick={async () => {
                  if (await confirm({
                    description: `This moves ${child.fullname}'s case back to Pre-Assessment.`,
                    confirmLabel: 'Yes, move it back', tone: 'warning',
                  })) advance('pre_assessment');
                }} iconLeft={<Icon name="arrow-left" size={16} />}>Back to Pre-Assessment</Button>)}
          </div>
        </div>

        <div className="racco-no-print">
          <Tabs tabs={chartTabs} active={tab} onChange={setTab} style={{ borderBottom: 'none' }} />
        </div>
      </div>

      <div className="racco-stack racco-tabpanel" hidden={tab !== 'overview'}>
        <Card title="Identifying information" padding="0">
          <div style={{ padding: '4px 15px 12px' }}>
            {facts.map((f) => (
              <div key={f.k} style={{ display: 'flex', gap: 12, padding: '6px 0', borderBottom: '1px solid var(--ink-50)' }}>
                <span style={{ width: 150, flex: 'none', fontWeight: 700, fontSize: 12, color: 'var(--text-muted)' }}>{f.k}</span>
                <span style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 12.5, color: 'var(--text-strong)' }}>{f.v || '—'}</span>
              </div>
            ))}
          </div>
          {(child.instruments_used || []).length > 0 && (
            <div style={{ padding: '11px 15px', background: 'var(--ink-50)', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="racco-eyebrow" style={{ fontSize: 'var(--text-3xs)', marginRight: 4 }}>Instruments used</span>
              {child.instruments_used.map((t) => <Badge key={t} tone="brand" size="sm">{t}</Badge>)}
            </div>
          )}
        </Card>

      {/* The child's own words, flagged as worth reading.

          Shown EXPANDED and immediately above the remarks. The words were
          never missing before this — they were rendered further down, folded
          behind an "Answers (3)" toggle, one click from anyone who thought to
          look. Nobody did. Folding them again would rebuild the failure. */}
      {(data.self_report_flags || []).filter((f) => !f.is_reviewed).length > 0 && (
        <Card
          eyebrow="Self-report"
          title={ownWordsTitle(data.child)}
          padding="20px"
          accent="var(--danger-500)"
        >
          <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 14 }}>
            Read these alongside the remarks below. Flagging says only that the
            child said something worth reading — it is not a judgement about the
            case or about anyone&apos;s notes.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {(data.self_report_flags || []).filter((f) => !f.is_reviewed).map((f) => (
              <div key={f.id} style={{ borderLeft: '3px solid var(--danger-500)', paddingLeft: 14 }}>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{f.question}</div>
                <div style={{ fontSize: 15.5, lineHeight: 1.5, color: 'var(--text-strong)', margin: '4px 0 6px' }}>
                  &ldquo;{f.answer}&rdquo;
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
                  <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                    {String(f.created_at || '').slice(0, 10)}
                    {' · '}
                    {f.source === 'model' ? 'local model' : 'phrase'}: {f.matched}
                  </span>
                  <Button size="sm" variant="secondary" onClick={() => acknowledgeFlag(f.id)}
                          disabled={ackBusy === f.id} className="racco-no-print">
                    {ackBusy === f.id ? 'Marking…' : 'Mark as read'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Treatment plan + problems, side by side */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 18 }}>
        <Card eyebrow="Care" title="Treatment plan" padding="20px">
          {activePlan ? (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <Badge tone={activePlan.status === 'active' ? 'success' : 'neutral'} size="sm" dot>{activePlan.status}</Badge>
                {activePlan.review_date && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Review {activePlan.review_date}</span>}
              </div>
              <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Objectives</div>
              <p style={{ fontSize: 13.5, color: 'var(--text-strong)', margin: '4px 0 10px', lineHeight: 1.55 }}>{activePlan.objectives}</p>
              {activePlan.interventions && (<>
                <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>Interventions</div>
                <p style={{ fontSize: 13.5, color: 'var(--text-body)', margin: '4px 0 0', lineHeight: 1.55 }}>{activePlan.interventions}</p>
              </>)}
              {canWrite && <div style={{ marginTop: 12 }} className="racco-no-print"><Button variant="secondary" onClick={() => setPlan({ ...activePlan })} iconLeft={<Icon name="pencil" size={15} />}>Edit plan</Button></div>}
            </div>
          ) : (
            <div>
              <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>No treatment plan yet.</div>
              {canWrite && <Button variant="primary" onClick={() => setPlan({ objectives: '', interventions: '', status: 'active', review_date: '' })} iconLeft={<Icon name="plus" size={15} />} className="racco-no-print">Create plan</Button>}
            </div>
          )}
        </Card>

        <Card eyebrow="Watchlist" title="Problems encountered" padding="20px">
          {data.problems.length === 0 ? (
            <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>No problems logged.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {data.problems.map((p) => (
                <div key={p.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 'var(--radius-md)', background: p.resolved ? 'var(--success-50)' : 'var(--ink-50)', border: '1px solid var(--border)' }}>
                  <Icon name={p.resolved ? 'check-circle-2' : 'alert-triangle'} size={15} style={{ color: p.resolved ? 'var(--success-600)' : 'var(--amber-500)' }} />
                  <span style={{ flex: 1, fontSize: 13, color: 'var(--text-strong)', textDecoration: p.resolved ? 'line-through' : 'none', opacity: p.resolved ? 0.7 : 1 }}>{p.description}</span>
                  {p.category && <Badge tone="neutral" size="sm">{p.category}</Badge>}
                  {canWrite && !p.resolved && (
                    <button title="Mark resolved" aria-label={`Mark "${p.description}" resolved`}
                      className="racco-no-print" style={iconBtn('var(--success-600)')}
                      onClick={() => setResolving(p)}>
                      <Icon name="check" size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Pre-assessment log */}
      <Card eyebrow="Clinical workflow" title="Pre-assessment log" padding="0">
        {data.pre_assessments.length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: 'var(--text-muted)' }}>No pre-assessments yet.</div>
        ) : (
          <div className="racco-scroll" style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 640, borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--ink-50)', borderBottom: '1px solid var(--border)' }}>
                {['Date', 'Status', 'Consent', 'Interview', 'Instrument Titles', 'Psychologist'].map((h) => (
                  <th key={h} style={{ textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {data.pre_assessments.map((p) => (
                  <tr key={p.id} style={{ borderBottom: '1px solid var(--divider-row)' }}>
                    <td style={td}>{p.date}</td>
                    <td style={td}><Badge tone={p.status === 'completed' ? 'success' : 'amber'} size="sm" dot>{p.status.replace('_', ' ')}</Badge></td>
                    <td style={td}>{p.consent ? (p.consent_status || 'linked') : '—'}</td>
                    <td style={td}>{p.interview ? (p.interview_respondent || 'recorded') : '—'}</td>
                    <td style={{ ...td, whiteSpace: 'normal' }}>{(p.instrument_titles || []).join(', ') || '—'}</td>
                    <td style={td}>{p.psychologist_name || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      </div>

      <div className="racco-stack racco-tabpanel" hidden={tab !== 'interviews'}>

      {/* Clinical interviews — every respondent, incl. secondary "Save & interview another" records */}
      <Card eyebrow="Clinical workflow" title="Clinical interviews" padding="0">
        {(data.interviews || []).length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: 'var(--text-muted)' }}>
            No clinical interviews recorded yet — they are conducted in the pre-assessment wizard.
          </div>
        ) : (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.interviews.map((iv) => {
              const entries = Object.entries(iv.answers || {}).filter(([, a]) => String(a ?? '').trim() !== '');
              const open = !!openInterviews[iv.id];
              return (
                <div key={iv.id} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 14, background: 'var(--ink-50)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Badge tone="brand" size="sm">{iv.respondent || 'Respondent not recorded'}</Badge>
                      <span style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{iv.template_title || 'Free-form interview'}</span>
                    </div>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                      <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{iv.date} · {iv.interviewer_name || '—'}</span>
                      {entries.length > 0 && (
                        <Button variant="ghost" className="racco-no-print"
                          onClick={() => setOpenInterviews((s) => ({ ...s, [iv.id]: !s[iv.id] }))}
                          iconLeft={<Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} />}>
                          {open ? 'Hide answers' : `Answers (${entries.length})`}
                        </Button>
                      )}
                    </div>
                  </div>
                  {entries.length === 0 && (
                    <div style={{ fontSize: 12, color: 'var(--text-faint)', marginTop: 6 }}>No written answers recorded.</div>
                  )}
                  {open && entries.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
                      {entries.map(([q, a]) => (
                        <div key={q} style={{ fontSize: 13, lineHeight: 1.5 }}>
                          <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{q}</span>{' '}
                          <span style={{ color: 'var(--text-strong)' }}>— {a}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      </div>

      <div className="racco-stack racco-tabpanel" hidden={tab !== 'instruments'}>

      {/* Result entries */}
      <Card eyebrow="Findings" title="Result entries (manual)" padding="0">
        <div style={{ padding: '12px 16px', display: 'flex', justifyContent: 'flex-end' }} className="racco-no-print">
          {canWrite && <Button variant="primary" onClick={() => setResult({ instrument: '', summary: '', classification: '', baseline_category: '' })} iconLeft={<Icon name="plus" size={15} />}>Add Result Entry</Button>}
        </div>
        {data.result_entries.length === 0 ? (
          <div style={{ padding: '0 18px 18px', fontSize: 13, color: 'var(--text-muted)' }}>No result entries yet — the psychologist records findings here after paper administration.</div>
        ) : (
          <div style={{ padding: '0 16px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.result_entries.map((r) => (
              <div key={r.id} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 14, background: 'var(--ink-50)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{r.instrument_title || 'General findings'}</div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    {r.baseline_category && <Badge tone={r.baseline_category === 'Needs Counseling' ? 'amber' : 'success'} size="sm" dot>{r.baseline_category}</Badge>}
                    {r.classification && <Badge tone="brand" size="sm">{r.classification}</Badge>}
                    <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{r.date}</span>
                  </div>
                </div>
                <p style={{ fontSize: 13.5, color: 'var(--text-body)', margin: 0, lineHeight: 1.6 }}>{r.summary}</p>
                <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 6 }}>Entered by {r.entered_by_name || '—'}</div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Uploaded reports */}
      <Card eyebrow="Documents" title="Psychological reports" padding="0">
        {canWrite && (
          <div style={{ padding: '12px 16px', display: 'flex', justifyContent: 'flex-end' }} className="racco-no-print">
            <Button variant="primary" onClick={() => setUpload('report')} iconLeft={<Icon name="upload" size={15} />}>Upload report</Button>
          </div>
        )}
        {data.reports.length === 0 ? (
          <div style={{ padding: canWrite ? '0 18px 18px' : 18, fontSize: 13, color: 'var(--text-muted)' }}>
            No reports uploaded yet{canWrite ? ' — your own report, in your own format, PDF or Word.' : '.'}
          </div>
        ) : (
          <div style={{ padding: canWrite ? '0 16px 16px' : 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.reports.map((f) => (
              <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)', background: 'var(--ink-50)' }}>
                <Icon name="file-text" size={18} style={{ color: 'var(--blue-600)' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.original_filename}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{reportTypeLabel(f.report_type)}{f.coverage ? ` · ${f.coverage}` : ''} · {f.author_name || '—'} · {(f.created_at || '').slice(0, 10)}</div>
                  {f.ai_summary && f.ai_summary_confirmed && (
                    <div style={{ marginTop: 6, padding: '8px 10px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-100)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <Icon name="file-text" size={12} style={{ color: 'var(--blue-600)' }} />
                        <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--blue-700)' }}>
                          Summary
                        </span>
                      </div>
                      <p style={{ fontSize: 12.5, color: 'var(--text-body)', margin: 0, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{f.ai_summary}</p>
                    </div>
                  )}
                  <ReportCheckNote report={f} canReview={canAdvance} onReviewed={load} />
                </div>
                {canSummariseReport && (
                  <Button variant="ghost" size="sm" disabled={summaryBusy} className="racco-no-print"
                          onClick={() => f.ai_summary_confirmed
                            ? setConfirmResummarize({ kind: 'report', id: f.id, filename: f.original_filename })
                            : draftSummary('report', f.id)}>
                    {f.ai_summary ? 'Re-summarise' : 'AI summary'}
                  </Button>
                )}
                {f.ai_summary && (
                  <Badge tone={f.ai_summary_confirmed ? 'success' : 'amber'} size="sm">
                    {f.ai_summary_confirmed ? 'Confirmed' : 'Draft (unconfirmed)'}
                  </Badge>
                )}
                <Button variant="ghost" onClick={() => setViewing(f)} iconLeft={<Icon name="eye" size={15} />} className="racco-no-print">View</Button>
                <Button variant="ghost" onClick={() => download(f)} iconLeft={<Icon name="download" size={15} />} className="racco-no-print">Download</Button>
              </div>
            ))}
          </div>
        )}
      </Card>

      </div>

      <div className="racco-stack racco-tabpanel" hidden={tab !== 'remarks'}>

      {/* Remarks log */}
      <Card eyebrow="Progress log" title="Psychological remark notes" padding="20px">
        {canWrite && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: data.remarks.length ? 18 : 0 }} className="racco-no-print">
            <textarea value={remarkText} onChange={(e) => setRemarkText(e.target.value)} rows={3} placeholder="Add a dated remark for this child…" style={textarea} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              {drafting && (
                <Button variant="ghost" onClick={polish}
                        disabled={!remarkText.trim() || polishing}
                        iconLeft={<Icon name="sparkles" size={16} />}>
                  {polishing ? 'Polishing…' : 'Polish writing'}
                </Button>
              )}
              <Button variant="primary" onClick={addRemark} iconLeft={<Icon name="plus" size={16} />} disabled={!remarkText.trim()}>Add remark</Button>
            </div>
            {polishJob && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
                <Alert tone="info" disclaimer style={{ flex: 1, marginTop: 0 }}>
                  AI-drafted decision support, not a diagnosis. Review and edit before saving.
                </Alert>
                <Button variant="ghost" size="sm" onClick={revertPolish}>Revert to my text</Button>
              </div>
            )}
          </div>
        )}
        {data.remarks.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>No remarks yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {data.remarks.map((n) => (
              <div key={n.id} style={{ borderLeft: '3px solid var(--blue-200)', paddingLeft: 12 }}>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 700 }}>{n.date} · {n.author_name || '—'}</div>
                <p style={{ fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-strong)', margin: '4px 0 0' }}>{n.text}</p>
              </div>
            ))}
          </div>
        )}
      </Card>

      </div>

      <div className="racco-stack racco-tabpanel" hidden={tab !== 'voice'}>

      {/* Child opinionnaire (QR survey) */}
      <Card eyebrow="Child's voice" title="Opinionnaire (QR survey)" padding="20px">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: (data.opinionnaires || []).length ? 14 : 0 }}>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0, maxWidth: 520 }}>
            The child answers the agency&apos;s self-report opinionnaire on a secondary device via QR code.
            Agency/government forms only — never published instruments.
            {' '}Self-reports are always shown — the child&apos;s own words are not part of carried
            history, so they stay visible even when session history was not carried to a new
            psychologist.
          </p>
          {(isStaffOrAdmin || canWrite) && child.status === 'active' && (
            <Button variant="primary" onClick={createInvite} iconLeft={<Icon name="qr-code" size={16} />} className="racco-no-print">New QR Survey</Button>
          )}
        </div>
        {(data.opinionnaires || []).length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.opinionnaires.map((o) => (
              <div key={o.id} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-lg)', padding: 14, background: 'var(--ink-50)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: o.status === 'submitted' ? 8 : 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{o.template_title}</div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <Badge tone={o.status === 'submitted' ? 'success' : o.is_open ? 'amber' : 'neutral'} size="sm" dot>
                      {o.status === 'submitted' ? `Answered ${String(o.submitted_at || '').slice(0, 10)}` : o.is_open ? 'Waiting for answers' : 'Expired'}
                    </Badge>
                    {o.is_open && (isStaffOrAdmin || canWrite) && (
                      <Button variant="ghost" onClick={() => setQr({ token: o.token, url: `${window.location.origin}/survey/${o.token}`, title: o.template_title })} iconLeft={<Icon name="qr-code" size={14} />} className="racco-no-print">Show QR</Button>
                    )}
                  </div>
                </div>
                {o.status === 'submitted' && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {Object.entries(o.answers || {}).map(([q, a]) => (
                      <div key={q} style={{ fontSize: 13, lineHeight: 1.5 }}>
                        <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{q}</span>{' '}
                        <span style={{ color: 'var(--text-strong)' }}>— {a}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      </div>

      <div className="racco-stack racco-tabpanel" hidden={tab !== 'casework'}>

      {/* Case referrals (social worker's side of the split view) */}
      <Card eyebrow="Casework" title="Case referral (social worker)" padding="0">
        {(data.case_referrals || []).length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: 'var(--text-muted)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-start' }}>
              <div>
                No case referral uploaded yet &mdash; sessions cannot be booked for
                {' '}{child.fullname} until one is on file.
              </div>
              {isStaffOrAdmin && (
                <Button
                  variant="primary" size="sm"
                  onClick={() => setUpload('case_referral')}
                  iconLeft={<Icon name="upload" size={15} />}
                >
                  Upload case referral
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.case_referrals.map((f) => (
              <div key={f.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)', background: 'var(--ink-50)' }}>
                <Icon name="folder-heart" size={18} style={{ color: 'var(--amber-500)' }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.original_filename}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>{f.description || 'Case referral'} · {f.uploaded_by_name || '—'} · {(f.created_at || '').slice(0, 10)}</div>
                  {f.ai_summary && f.ai_summary_confirmed && (
                    <div style={{ marginTop: 6, padding: '8px 10px', borderRadius: 'var(--radius-md)', background: 'var(--blue-50)', border: '1px solid var(--blue-100)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <Icon name="file-text" size={12} style={{ color: 'var(--blue-600)' }} />
                        <span style={{ fontSize: 10.5, fontWeight: 800, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--blue-700)' }}>
                          Summary
                        </span>
                      </div>
                      <p style={{ fontSize: 12.5, color: 'var(--text-body)', margin: 0, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{f.ai_summary}</p>
                    </div>
                  )}
                </div>
                {canSummariseReferral && (
                  <Button variant="ghost" size="sm" disabled={summaryBusy} className="racco-no-print"
                          onClick={() => f.ai_summary_confirmed
                            ? setConfirmResummarize({ kind: 'case-referral', id: f.id, filename: f.original_filename })
                            : draftSummary('case-referral', f.id)}>
                    {f.ai_summary ? 'Re-summarise' : 'AI summary'}
                  </Button>
                )}
                {f.ai_summary && (
                  <Badge tone={f.ai_summary_confirmed ? 'success' : 'amber'} size="sm">
                    {f.ai_summary_confirmed ? 'Confirmed' : 'Draft (unconfirmed)'}
                  </Badge>
                )}
                <Button variant="ghost" onClick={async () => {
                  try {
                    const res = await api.get(`/case-referrals/${f.id}/download/`, { responseType: 'blob' });
                    const url = URL.createObjectURL(res.data);
                    const a = document.createElement('a'); a.href = url; a.download = f.original_filename || 'case-referral'; a.click();
                    URL.revokeObjectURL(url);
                  } catch { toast.error('Could not download the file.'); }
                }} iconLeft={<Icon name="download" size={15} />} className="racco-no-print">Download</Button>
              </div>
            ))}
          </div>
        )}
      </Card>

      </div>

      {/* The Social Case Study Report (Adoption records). Mounted as soon as the
          server has answered, like every other panel, so what is half-typed in
          it survives a visit to another tab. */}
      {hasCaseStudyTab && (
        <div className="racco-stack racco-tabpanel" hidden={tab !== 'casestudy'}>
          <CaseStudyTab key={child.id} child={child} cs={caseStudy} print={printTarget} />
        </div>
      )}

      {isAdmin && (
        <div className="racco-stack racco-tabpanel" hidden={tab !== 'assistant'}>
          <AssistantAccessLog childId={child.id} active={tab === 'assistant'} />
        </div>
      )}

      <Alert disclaimer title="Note.">All clinical findings are the licensed psychologist&apos;s own professional judgment.</Alert>

      {/* Add-result drawer */}
      {result && (
        <div onClick={() => setResult(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 440, maxWidth: '92%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>Add result entry</div>
            <FormField label="Instrument (title)">
              <Select value={result.instrument} onChange={(e) => setResult({ ...result, instrument: e.target.value })}>
                <option value="">— General / none —</option>
                {instruments.map((i) => <option key={i.id} value={i.id}>{i.title}</option>)}
              </Select>
            </FormField>
            <FormField label="Baseline category" hint="Simple post-session verdict for the case tracker.">
              <Select value={result.baseline_category || ''} onChange={(e) => setResult({ ...result, baseline_category: e.target.value })}>
                <option value="">— Not set —</option>
                <option>Needs Counseling</option>
                <option>Good Assessment</option>
              </Select>
            </FormField>
            <FormField label="Findings summary" required>
              <textarea value={result.summary} onChange={(e) => setResult({ ...result, summary: e.target.value })} rows={7} style={textarea} placeholder="Findings in your own words…" />
            </FormField>
            <FormField label="Classification (your own words)">
              <textarea value={result.classification} onChange={(e) => setResult({ ...result, classification: e.target.value })} rows={2} style={textarea} />
            </FormField>
            <Button variant="primary" onClick={saveResult} disabled={!result.summary.trim()} iconLeft={<Icon name="save" size={16} />}>Save entry</Button>
            <div style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>Manual input only — the system never computes scores.</div>
          </div>
        </div>
      )}

      {/* QR modal */}
      {qr && (
        <div onClick={() => setQr(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 90 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 380, maxWidth: '92%', background: 'var(--surface)', borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-xl)', padding: 24, textAlign: 'center' }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)', marginBottom: 4 }}>Scan to answer</div>
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginBottom: 16 }}>{qr.title} — hand the second device to the child.</div>
            <div style={{ display: 'inline-block', padding: 14, background: '#fff', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border)' }}>
              <QRCodeSVG value={qr.url} size={200} />
            </div>
            <div className="racco-mono" style={{ fontSize: 11, color: 'var(--text-muted)', margin: '12px 0 16px', wordBreak: 'break-all' }}>{qr.url}</div>
            <div style={{ display: 'flex', gap: 10 }}>
              <Button variant="secondary" fullWidth onClick={() => { navigator.clipboard?.writeText(qr.url); toast.success('Link copied'); }}>Copy link</Button>
              <Button variant="primary" fullWidth onClick={() => setQr(null)}>Done</Button>
            </div>
          </div>
        </div>
      )}


      {/* Treatment plan drawer */}
      {plan && (
        <div onClick={() => setPlan(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(14,19,29,0.32)', display: 'flex', justifyContent: 'flex-end', zIndex: 70 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 440, maxWidth: '92%', height: '100%', background: 'var(--surface)', boxShadow: 'var(--shadow-xl)', padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: 17, color: 'var(--text-strong)' }}>{plan.id ? 'Edit treatment plan' : 'New treatment plan'}</div>
            <FormField label="Objectives" required>
              <textarea value={plan.objectives} onChange={(e) => setPlan({ ...plan, objectives: e.target.value })} rows={5} style={textarea} />
            </FormField>
            <FormField label="Interventions">
              <textarea value={plan.interventions} onChange={(e) => setPlan({ ...plan, interventions: e.target.value })} rows={5} style={textarea} />
            </FormField>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {plan.id && (
                <FormField label="Status">
                  <Select value={plan.status} onChange={(e) => setPlan({ ...plan, status: e.target.value })}>
                    <option value="active">Active</option><option value="completed">Completed</option><option value="revised">Revised</option>
                  </Select>
                </FormField>
              )}
              <FormField label="Review date">
                <input type="date" value={plan.review_date || ''} onChange={(e) => setPlan({ ...plan, review_date: e.target.value })}
                  style={{ width: '100%', padding: '9px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-strong)', fontFamily: 'var(--font-sans)', fontSize: 14 }} />
              </FormField>
            </div>
            <Button variant="primary" onClick={savePlan} disabled={!plan.objectives.trim()} iconLeft={<Icon name="save" size={16} />}>Save plan</Button>
          </div>
        </div>
      )}
      {confirmMove && (
        <ConfirmDialog
          onClose={() => setConfirmMove(null)}
          onConfirm={() => { const n = confirmMove.next; setConfirmMove(null); advance(n); }}
          tone="brand" icon={<Icon name="chevron-right" size={19} />}
          title="Move to counseling?"
          description={`${confirmMove.childName || 'This child'} moves out of pre-assessment and into counseling. You can move them back, but the change shows in the audit trail either way.`}
          confirmLabel="Move to counseling" cancelLabel="Cancel"
        />
      )}
      {resolving && (
        <ConfirmDialog
          onClose={() => setResolving(null)}
          onConfirm={resolveProblem}
          busy={resolveBusy}
          tone="brand" icon={<Icon name="check" size={19} />}
          title="Mark this problem resolved?"
          description="It stays on the watchlist, struck through, as part of the case history."
          confirmLabel="Mark resolved" cancelLabel="Not yet"
        >
          <div style={{ padding: '10px 12px', borderRadius: 'var(--radius-md)', background: 'var(--ink-50)', border: '1px solid var(--border)' }}>
            <div style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--text-strong)' }}>{resolving.description}</div>
            {resolving.category && <div style={{ fontSize: 12.5, color: 'var(--text-muted)', marginTop: 2 }}>{resolving.category}</div>}
          </div>
          {/* Said plainly because the screen offers no way back: there is no
              unresolve button, here or anywhere else. */}
          <Alert tone="warning" icon={<Icon name="alert-triangle" size={18} />}>
            No screen can mark it unresolved again.
          </Alert>
        </ConfirmDialog>
      )}
      {confirmResummarize && (
        <ConfirmDialog
          onClose={() => setConfirmResummarize(null)}
          onConfirm={() => {
            const { kind, id } = confirmResummarize;
            setConfirmResummarize(null);
            draftSummary(kind, id);
          }}
          tone="danger" icon={<Icon name="alert-triangle" size={19} />}
          title="Replace the confirmed summary?"
          description={`The current summary of "${confirmResummarize.filename || 'this document'}" is the psychologist's own confirmed clinical text, not a draft. Re-summarising replaces it with a new AI draft, and the confirmed text cannot be recovered.`}
          confirmLabel="Replace with new draft" cancelLabel="Cancel"
        />
      )}

      {/* Pre-session brief modal. The case brief (social worker, ISA) is the
          facts panel and a Close button: no written part, no disclaimer about
          one, and it never reaches the endpoints that draft it. */}
      {brief && (
        <Modal open onClose={() => setBrief(null)} title={caseBrief ? 'Case brief' : 'Pre-session brief'}
               subtitle={brief.prose === 'ready' ? `Drafted ${clock(brief.generatedAt)}` : null}
               width={560}>
          <BriefFacts facts={brief.facts} failed={brief.factsFailed} />
          {brief.prose !== 'none' && (
            <div style={{ borderTop: '1px solid var(--border)', margin: '16px 0 12px' }} />
          )}
          {brief.prose === 'loading' && (
            <p role="status" style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              Drafting the written brief… this can take up to a minute.
            </p>
          )}
          {brief.prose === 'not_offered' && (
            <Note icon="info">A written brief is not available on this deployment.</Note>
          )}
          {brief.prose === 'unavailable' && (
            <Note icon="info">
              The written brief is unavailable right now. The facts above come straight from the record.
            </Note>
          )}
          {brief.prose === 'failed' && (
            <Note tone="warning" icon="alert-triangle">Could not prepare the written brief.</Note>
          )}
          {brief.prose === 'ready' && (
            <>
              <Alert tone="info" disclaimer style={{ marginBottom: 12 }}>
                AI-drafted decision support, not a diagnosis. The licensed psychologist
                reviews, edits, and approves all content.
              </Alert>
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.6 }}>{brief.draft}</div>
            </>
          )}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
            {brief.prose === 'ready' ? (
              <>
                <Button variant="ghost" onClick={() => { sendFeedback(brief.jobId, 'discarded').catch(() => {}); setBrief(null); }}>
                  Not useful
                </Button>
                <Button variant="ghost" onClick={() => openBrief({ regenerate: true })} disabled={briefBusy}>
                  {briefBusy ? 'Drafting…' : 'Regenerate (slow)'}
                </Button>
                <Button variant="primary" onClick={() => { sendFeedback(brief.jobId, 'accepted').catch(() => {}); setBrief(null); }}>
                  Useful
                </Button>
              </>
            ) : (
              <>
                {brief.prose === 'failed' && (
                  <Button variant="ghost" onClick={() => openBrief()} disabled={briefBusy}>Try again</Button>
                )}
                <Button variant="primary" onClick={() => setBrief(null)}>Close</Button>
              </>
            )}
          </div>
        </Modal>
      )}

      {upload && (
        <UploadDrawer kind={upload} child={child} onClose={() => setUpload(null)} onUploaded={load} />
      )}
      {viewing && <ReportViewer report={viewing} onClose={() => setViewing(null)} onDownload={download} />}

      {/* Document summary modal */}
      {summary && (
        <Modal open onClose={() => setSummary(null)} title="Document summary" width={620}>
          <Alert tone="info" disclaimer style={{ marginBottom: 12 }}>
            AI-drafted decision support, not a diagnosis. Edit freely — confirming
            makes this your own clinical text.
          </Alert>
          {/* A long report is read in part. Saying which part is what lets the
              person confirming this know what the draft could not have seen. */}
          {summary.coverage && (
            <Alert tone="warning" title="Read in part." style={{ marginBottom: 12 }}>
              {summary.coverage}
            </Alert>
          )}
          <textarea rows={14} style={textarea} value={summary.text} aria-label="Summary draft"
                    onChange={(e) => setSummary({ ...summary, text: e.target.value })} />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
            <Button variant="ghost" onClick={() => setSummary(null)}>Cancel</Button>
            <Button variant="primary" onClick={saveSummary} disabled={!summary.text.trim()}>
              Confirm summary
            </Button>
          </div>
        </Modal>
      )}

    </div>
  );
}
