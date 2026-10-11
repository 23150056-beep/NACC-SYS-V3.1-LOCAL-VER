import api from './client';

/* The Social Case Study Report on a child's record (backend case_study/views.py).
 *
 * The answer has three shapes by who asks - the social worker everything, the
 * psychologist block A, the ISA a status - and a 404 for anybody else, which
 * the child's page reads as "no Case study tab". */

export const getCaseStudy = (childId) =>
  api.get(`/case-studies/child/${childId}/`).then((r) => r.data);

export const startCaseStudy = (childId) =>
  api.post(`/case-studies/child/${childId}/`, {}).then((r) => r.data);

// Only `date_prepared` and `custody_over_two_years` are accepted here.
export const saveCaseStudyHeader = (childId, patch) =>
  api.patch(`/case-studies/child/${childId}/`, patch).then((r) => r.data);

// One box, with the version its writer last saw. A 409 carries `current`.
export const saveCaseStudySection = (childId, key, body) =>
  api.put(`/case-studies/child/${childId}/sections/${key}/`, body).then((r) => r.data);

// Make the case study final: the social worker who holds the record only. The
// body says which version of it was on screen (`updated_at` as the server sent
// it), so a case study changed in another tab is refused with a 409 rather than
// frozen with text nobody here saw. A 400 carries `missing`, the titles still
// to complete. Answers with the social worker's reading of the case study.
export const finalizeCaseStudy = (childId, expectedUpdatedAt) =>
  api.post(`/case-studies/child/${childId}/final/`, { expected_updated_at: expectedUpdatedAt })
    .then((r) => r.data);

// Back to a draft. The finals already made stay on file.
export const reopenCaseStudy = (childId) =>
  api.post(`/case-studies/child/${childId}/reopen/`, {}).then((r) => r.data);

// One final copy whole, to print it: { id, finalized_at, finalized_by_name, snapshot }.
export const getCaseStudyFinal = (childId, finalId) =>
  api.get(`/case-studies/child/${childId}/finals/${finalId}/`).then((r) => r.data);
