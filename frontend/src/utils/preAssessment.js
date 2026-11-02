/* The pre-assessment wizard's steps and the status the rest of the app shows
 * for them. `index` is the wizard's internal step number in PreAssessment.jsx
 * (1 consent, 2 interview, 3 instruments, 4 problems, 4.5 review); the server
 * keeps the key (clinical PreAssessment.current_step). */
export const PA_STEPS = [
  { key: 'consent', label: 'Consent', index: 1 },
  { key: 'interview', label: 'Clinical interview', index: 2 },
  { key: 'instruments', label: 'Instruments', index: 3 },
  { key: 'problems', label: 'Presenting problems', index: 4 },
  { key: 'review', label: 'Review', index: 4.5 },
];

export const stepKeyFor = (index) => PA_STEPS.find((s) => s.index === index)?.key || null;
export const stepIndexFor = (key) => PA_STEPS.find((s) => s.key === key)?.index || null;

/* progress is Child.pre_assessment_progress from the server. */
export const paProgressLabel = (progress) => progress?.label || 'Not started';

const TONES = { not_started: 'neutral', started: 'amber', in_progress: 'brand', completed: 'success' };
export const paProgressTone = (progress) => TONES[progress?.state] || 'neutral';

export const paProgressDetail = (progress) => {
  if (progress?.state !== 'in_progress' && progress?.state !== 'started') return '';
  const parts = [];
  if (progress.step_number) parts.push(`Step ${progress.step_number} of ${progress.total_steps || 6}`);
  if (progress.reassessment) parts.push('re-assessment');
  return parts.join(' · ');
};
