import api from './client';

// The agency's name, address and head of office. Anyone signed in may read it
// (printed reports do); only an administrator can save.
export const getAgencyProfile = () =>
  api.get('/agency-profile/').then((r) => r.data);

export const saveAgencyProfile = (payload) =>
  api.put('/agency-profile/', payload).then((r) => r.data);
