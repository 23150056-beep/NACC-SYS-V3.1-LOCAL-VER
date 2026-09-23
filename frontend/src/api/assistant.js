import api from './client';

// Every call here degrades silently at the call site: the assistant returns 503
// when it is switched off, and no screen may break because of that.
export const polishRemark = (text) =>
  api.post('/assistant/polish-remark/', { text }).then((r) => r.data);

/* Whether a draft was accepted or discarded, for the usage metrics.
 *
 * Every caller deliberately ignores a failure - `catch(() => {})` at six
 * sites, and that is correct rather than an oversight. This is telemetry
 * about a decision the user has ALREADY made and moved on from; telling
 * them the bookkeeping did not save would interrupt them over something
 * they cannot act on and do not care about. */
export const sendFeedback = (jobId, outcome) =>
  api.post(`/assistant/jobs/${jobId}/feedback/`, { outcome }).then((r) => r.data);

export const getAssistantSettings = () =>
  api.get('/assistant/settings/').then((r) => r.data);

export const saveAssistantSettings = (payload) =>
  api.put('/assistant/settings/', payload).then((r) => r.data);

export const getLatestBrief = (childId) =>
  api.get(`/assistant/brief/child/${childId}/latest/`).then((r) => r.data);

export const generateBrief = (childId) =>
  api.post(`/assistant/brief/child/${childId}/`).then((r) => r.data);

// Fire and forget. Failures here are invisible on purpose: a schedule screen
// must not report that a background convenience did not happen.
export const prefetchBriefs = () =>
  api.post('/assistant/prefetch-briefs/').catch(() => null);

const SUMMARIZE = { report: 'summarize-report', 'case-referral': 'summarize-case-referral' };
const CONFIRM = { report: 'confirm-summary', 'case-referral': 'confirm-case-referral-summary' };

export const summarizeDocument = (kind, id) =>
  api.post(`/assistant/${SUMMARIZE[kind]}/${id}/`).then((r) => r.data);

export const confirmSummary = (kind, id, text) =>
  api.post(`/assistant/${CONFIRM[kind]}/${id}/`, { text }).then((r) => r.data);

export const censusNarrative = (figures) =>
  api.post('/assistant/census-narrative/', { figures }).then((r) => r.data);

export const getAssistantMetrics = () =>
  api.get('/assistant/metrics/').then((r) => r.data);

// What people asked the chatbot that it could not answer, most-asked first.
// Administrators only, like the metrics.
export const getUnansweredQuestions = () =>
  api.get('/assistant/unanswered/').then((r) => r.data);

export const checkAssistant = () =>
  api.post('/assistant/check/').then((r) => r.data);

// The chatbot. Returns { ok, tool, echo, result } — or { ok: false, message }
// when the model produced something the validator refused. A 503 means the
// assistant is off or the runtime is down; the panel says so and stays usable.
export const askAssistant = (question) =>
  api.post('/assistant/ask/', { question }).then((r) => r.data);

// A follow-up chip: the ready-made call the last answer offered, run without
// the model. Sent back exactly as offered — the server validates it again.
export const runFollowup = ({ tool, args, label }) =>
  api.post('/assistant/followup/', { tool, args, label }).then((r) => r.data);

// What this user can ask, and a few example questions. Served rather than
// hardcoded so the empty panel and the assistant's own refusal text cannot
// drift apart — there is one answer to "what can I ask", not two.
export const getAssistantCapabilities = () =>
  api.get('/assistant/capabilities/').then((r) => r.data);
