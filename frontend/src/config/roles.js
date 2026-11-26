// Every list of roles, named once, by the question it answers - the browser's
// copy of backend/accounts/capabilities.py. A screen reads a list from here and
// never writes a role array of its own. Each list is ONE statement of exactly
// the shape `export const NAME = ['Administrator', 'Staff'];`: the pin in
// backend/config/tests/test_cross_stack_roles.py reads that shape and nothing
// else, and compares members sorted. Where a screen renders a list in order,
// the order here is the order on screen.
//
// This browser file decides what is OFFERED; the API decides what is ALLOWED.

// May use the system at all; the authentication door refuses any other role.
export const EVERY_ROLE = ['Administrator', 'Officer', 'Psychologist', 'Staff'];

// Scope: sees every record, no narrowing.
export const SEES_EVERY_RECORD = ['Administrator', 'Officer'];

// Writes a child's record; every place that said "Admin or Staff".
export const KEEPS_RECORDS = ['Administrator', 'Officer', 'Staff'];

// Follows the social worker's rule where it differs from the ISA's (care gaps,
// next steps, the drafting table, cancel-only).
export const SOCIAL_WORK = ['Officer', 'Staff'];

// Follows the social worker's rule on any record, not only the ones it holds.
export const WORKS_ANY_RECORD = ['Officer'];

// Chooses holders, transfers records, closes by outcome, removes a duplicate.
export const OVERSEES_CASEWORK = ['Administrator'];

// May be Child.social_worker.
export const HOLDS_RECORDS = ['Staff'];

// What a stranger may say they are when asking for an account, at either door
// (the typed form and Google). In the order the pickers show them.
export const CLAIMABLE_AT_SIGNUP = ['Staff', 'Psychologist'];

// Who may sign in through the Google door once approved; not what may be
// claimed there, which is CLAIMABLE_AT_SIGNUP.
export const SIGNS_IN_WITH_GOOGLE = ['Officer', 'Staff', 'Psychologist'];

// Writes clinical records, and may polish a remark.
export const WRITES_CLINICAL = ['Administrator', 'Psychologist'];

// May combine availability windows.
export const MANAGES_AVAILABILITY = ['Administrator', 'Psychologist'];

// Records completed or no-show on a session the account holds as its
// psychologist (and may cancel it); the RACC Officer is never in it (the plan's
// rule 6: the Officer is not a psychologist).
export const RECORDS_OWN_SESSION_OUTCOMES = ['Administrator', 'Psychologist', 'Staff'];

// Roles allowed to manage assessment instruments (questionnaires).
// Capstone RBAC = admin-only; Psychologist added per product decision 2026-06-27.
// TO REVERT instrument management to admin-only: remove 'Psychologist' from this list.
export const INSTRUMENT_MANAGERS = ['Administrator', 'Psychologist'];

// capabilities.works_record: does this user follow the social worker's rule for
// this child - their role is in WORKS_ANY_RECORD (any record), or in SOCIAL_WORK
// and they are the child's social worker? `child.social_worker` is the holder's
// user id (ChildSerializer).
export function worksRecord(user, child) {
  const role = user?.role_name;
  return WORKS_ANY_RECORD.includes(role)
    || (SOCIAL_WORK.includes(role)
      && child?.social_worker != null
      && child.social_worker === user?.id);
}
