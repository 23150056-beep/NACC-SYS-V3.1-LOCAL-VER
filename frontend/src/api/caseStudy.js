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
