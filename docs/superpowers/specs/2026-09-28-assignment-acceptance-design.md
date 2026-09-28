# A Psychologist Accepts a Case Before It Is Theirs

**Date:** 2026-09-28
**Status:** Built on `claude/ai-feature-access-control-xuieuz`
**Owner's request:** "when the staff user assign a child on that psychologist,
that child will not automatically added to the record of psychologist unless
this psychologist accept or deny it, then if it denied the psychologist can
remark their reason, if accepted they can either schedule right now or set a
schedule base on their own availability, add end dialogue always"

## What happens today

Picking a psychologist on Add Record, or on an edit, writes
`Child.assigned_psychologist` in the same save. That one field is the
psychologist's whole view of the agency: `accounts/scoping.py` narrows every
list, every clinical record, Monitoring, the calendar picker and the assistant
to it. So an assignment is a fact the moment it is saved. The psychologist
finds out from an email, an SMS and a bell entry saying "a new case has been
assigned to you", with nothing to answer.

Nothing like this was built before. No branch has it; the `development`
branch predates the social-worker scoping it would depend on.

## The idea in one line

**Assigning becomes asking.** The pick creates a pending *request*, and
`assigned_psychologist` changes only when the psychologist accepts it.

Because every rule already reads `assigned_psychologist`, keeping that field
unchanged until acceptance is the whole of "not added to their records". A
pending child is not in their Records, not in their Monitoring, not in their
calendar's child list and not something the assistant will talk to them about,
without any of those screens changing. That is the reason for a separate
request row rather than a status on the child: a second meaning for the field
would have to be taught to every one of those readers, and the one that is
missed would show the child anyway.

## The three people and what each sees

### The social worker (and the ISA) who asks

1. Add Record or Edit, the Assignment step: pick a psychologist from the
   availability list, as now. The list's heading says the psychologist is
   asked first.
2. Save: "Are you sure you want to proceed?" names who will be asked.
3. **End dialog**: "Request sent. [Name] is asked to take the case. [Child]
   joins their records once they accept. You will see their answer in the
   notifications and on the record."
4. While waiting: the Records row shows **Awaiting [Name]** where the
   psychologist's name goes, and the form's Assignment step says the same, with
   a **Withdraw request** button (confirmed, then an end dialog).
5. If declined: the row shows **Declined by [Name]**, with the reason on
   hover and in the form's Assignment step. The **Assign** button stays, so
   the next request is one click away. The bell carries the decision.
6. If accepted: the row shows the psychologist, as any assigned child does.

Both the ISA and a social worker go through the request. The owner's words
were "the staff user", but the local sign-in is the ISA's, and an ISA
assignment that skipped the question would be a second door around the rule —
and would look, from that login, as though the feature had not been built.

### The psychologist who is asked

A **Waiting for your answer** panel at the top of their Dashboard and their
Records, and a count on Records in the navigation. Each request shows only
what is needed to decide:

- the child's name and case reference, age, sex, case type and category;
- the reason for referral as written on the record;
- whether a case referral is on file (no sessions can be booked without one);
- who asked and when, and, for a transfer, who holds the child now.

Nothing clinical: no remarks, reports, assessments or self-reports. They
are not the psychologist's until accepted, and a colleague's opinions reach a
new assignee only through the carry-history choice, applied at acceptance.

**Accept** → "Are you sure you want to proceed?" → the child is theirs →
**the scheduling dialog**, which offers:

- **Schedule now** — pick a date and time yourself. It goes on your own
  calendar, so your posted hours do not limit it (the existing `own_calendar`
  rule); a clash, or a day of leave, is still refused.
- **From my availability** — the next open times from your posted hours, one
  click each. Worked out by `booking.bookable_slots`, the rule the booking
  endpoint runs, so every time offered can be booked.
- **Later** — book it from the Calendar when you are ready.

Both booking choices go through `POST /appointments/`, unchanged, and are
confirmed first. When the child has no case referral on file, the booking
choices say so and only Later is offered — the referral gate would refuse the
booking anyway. The flow ends with an **end dialog**: "[Child] is now in your
records", plus the session booked, if one was.

**Decline** → a dialog that asks for the reason (required) → confirmed → the
child stays exactly as it was: unassigned, or with the psychologist who had
them. **End dialog**: "Declined. [Social worker] sees your reason."

"Can remark their reason" is built as *must*: a refusal with no reason leaves
the social worker guessing whom to ask next, and terminating a case already
asks for a note on the same reasoning.

### The psychologist who holds the child now (a transfer)

Nothing changes for them until the new psychologist accepts; the child stays
in their records. On acceptance the child moves, the same as a reassignment
does today.

## Rules

- **One pending request per child**, held by a partial unique constraint.
  Picking a different psychologist withdraws the earlier request first.
- **"Leave unassigned"** withdraws any pending request and clears the
  assignment, as it did.
- **Only an active Psychologist account can be asked.** The field accepted
  any user id before, including a staff account.
- **The carry-history choice travels on the request** and is applied at
  acceptance; applied at the request, it would change what the *current*
  psychologist sees before anyone has agreed to anything.
- **Answering is a conditional update** on `status='pending'`: accepting a
  request that was withdrawn, or answered in another tab, is refused with 409
  and says what happened.
- **Terminating a case withdraws its pending request.** Reopening already
  clears the assignment.
- **Who may do what:** accept and decline — the psychologist asked, nobody
  else (404 to anyone else, the "hidden, not disclosed" convention); withdraw
  — the ISA, or the social worker whose record it is; read — the psychologist
  their own pending requests, a social worker requests on their own records,
  the ISA all.
- **Existing assignments are untouched.** Children already assigned stay
  assigned; there is no data migration.
- A psychologist still cannot change `psychologist` on a record (unchanged).

## Notifications

- On request: the bell (to the psychologist), the email and the SMS. The
  email and SMS say a case is *waiting for their answer* rather than that one
  *has been assigned*. Still no child's name in either — the processor rule is
  unchanged.
- On accept, decline or withdraw: the bell. A social worker's feed carries
  every `Assignment` event on their own records, so the row's recipient only
  matters to a psychologist: withdraw goes to the psychologist asked, so a
  request does not vanish from their panel unexplained, and an accepted
  transfer to the psychologist who held the child. One row per event — a
  second row for the previous holder showed the social worker the same
  acceptance twice.
- Four new `ActivityLog` actions — requested, accepted, declined, withdrawn —
  under entity type `Assignment`, whose id is the child's.

## API

| Method | Path | Who |
|---|---|---|
| GET | `/api/assignment-requests/?status=pending` | scoped as above |
| POST | `/api/assignment-requests/{id}/accept/` | the psychologist asked |
| POST | `/api/assignment-requests/{id}/decline/` `{reason}` | the psychologist asked |
| POST | `/api/assignment-requests/{id}/withdraw/` | ISA, or the record's SW |
| GET | `/api/availability/openings/?child=&duration=` | anyone who sees the child |

The child serializer gains `pending_assignment` and `declined_assignment`,
filled for the ISA and social workers only: a decline's reason is written to
the social worker, not to whichever psychologist holds the child.

## Considered and not done

- **A status on `Child`** instead of a request row — rejected above.
- **Auto-expiry of unanswered requests.** No deadline was asked for, and an
  expired request would silently undo a social worker's choice. The
  Awaiting chip and Withdraw cover it.
- **Accept straight from the email or SMS.** Both leave the system on
  purpose without the child's name; a link that accepts would be a write
  reachable without signing in.
- **A note from the social worker on the request.** Useful, not asked for;
  the reason for referral already travels with it.
- **Teaching the assistant about pending requests.** Its tool descriptions
  are routing logic (CLAUDE.md, The chatbot) and change only with an
  `ai_eval` run, which needs a live model.
