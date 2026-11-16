# The RACC Officer: a Fourth Role, Above the Social Worker

**Date:** 2026-11-16
**Status:** Plan only. Nothing is built. Written to be handed to a build session.
**Read at:** commit `ab447f2` on `claude/sweet-wozniak-9fzjme`. Line numbers are that commit's.
**Owner's request:** "we're adding a new type of user and its: Social Welfare
Officer IV/OIC - RACC Officer ... this role is the above level of the social
worker and what this account can do is able to access all the child record of
each staff, this is the leader that can also assign a child to each social
worker, in short this user level can able to see all and access everything that
the social worker can't do, and no it's not an admin type but another admin
type, like it's a super user version of the social worker."

## What happens today

There are three roles, and one rule decides what each sees
(`accounts/scoping.py`): the ISA (Administrator) sees every child, a social
worker (Staff) sees the records they hold (`Child.social_worker`), a
psychologist sees the children assigned to them.

Nobody stands between the social workers and the ISA. So everything about
*whose* a record is has been the ISA's by default: the record form's Social
Worker field, Transfer records, the "No social worker yet" filter, removing a
duplicate, and closing a case by where the child went. The ISA is the agency's
IT support. It holds those powers because no other account could, not because
they are IT work. A social worker, meanwhile, cannot close a case at all, and
cannot see a colleague's record even to cover for them.

**An account with a role the code has never heard of** is refused or sees
nothing at most doors, because scoping answers "nothing" for an unknown role
and the permission classes list the three roles by name. That is the safe
default and it should stay. But it is not true everywhere. Reading the code,
these doors decide with an `else` that means "the Administrator", so a fourth
role would walk into the ISA's branch without anybody deciding it:

| Door | What an unknown role gets | Where |
|---|---|---|
| The bell and the audit trail | the whole audit stream, security and user lines included | `activity/views.py:81` |
| The appointments list | every session in the agency (names masked) | `scheduling/views.py:407` |
| Moving an appointment | any session it names; the own-child check asks Staff only | `scheduling/views.py:490` |
| The Dashboard | today's sessions and availability, unfiltered | `clinical/reports_views.py:379` |
| The assistant's schedule answers | the agency's | `assistant/tools.py:566` |

And these sit behind a gate today but would open the moment the gate admits a
new role: Add Record's `else` is the ISA's path (`children/views.py:123`); an
edit's `else` writes `assigned_psychologist` directly, with nobody asked
(`children/views.py:155`); closure reasons give any non-psychologist the ISA's
list (`children/termination.py:75`); the custodian rule treats "not ISA, not
Staff" as a psychologist (`children/custodian.py:83`); care gaps give any
non-Staff the ISA's list (`clinical/care_gaps.py:242`).

No such account can exist today, because the only Role rows are the three. It
becomes real the day a fourth row is added. So the first thing the build does
is close those branches, before the role exists (work package 1).

## The idea in one line

**The Officer is a social worker with every record in view, plus the casework
oversight the ISA has been holding for want of anyone else. It is never IT, and
never a psychologist.**

Nothing is taken from the ISA in this build. The ISA keeps both hats; the
Officer is given the casework one.

## The role

- **On screen:** "SWO IV/OIC (RACC Officer)", following "ISA (Administrator)"
  and "SW (Staff)". The long form, "Social Welfare Officer IV/OIC - RACC
  Officer", goes where there is room (the role's description in Users and
  Access Requests). In sentences the server writes: "the RACC Officer".
- **Stored:** `Role.OFFICER = "Officer"`. One word like the other three,
  because it is a key in JavaScript objects, a JWT claim and the word the audit
  log writes ("role changed from Staff to Officer"). **It is never renamed once
  an account holds it.** Never compare anything against the label.

The seven rules:

1. The Officer sees every record: active and closed, whoever holds it, and the
   ones nobody holds yet.
2. On any record, the Officer may do what that record's social worker may do.
3. The Officer decides who holds a record: picks or changes the social worker
   on one record, moves many at once, and works the queues about records.
4. The Officer closes a case by where the child went, reopens one, and removes
   a duplicate record.
5. The Officer is not IT: no user accounts, roles, access requests, Settings,
   assistant settings, test texts, system health or security log.
6. The Officer is not a psychologist: no clinical writing, no written brief, no
   availability or leave.
7. The Officer does not hold records. A record's social worker is still a
   Staff account (decision 1 below).

Where the ISA is kept **out** of something for being IT support, the Officer
follows the social worker's rule, not the ISA's: the written brief, remark
polish, and the case study's text.

## Decisions for the owner

Each has a default the plan is written to. Each is a small change either way,
because the plan puts every one of them behind a single named list or function.

| # | Question | Default | The other way costs |
|---|---|---|---|
| 1 | Does the Officer also hold records of their own? | **No.** They assign and work on everyone's; a holder is a Staff account. | Add the Officer to `HOLDS_RECORDS`; Add Record's default holder, the pickers, Transfer's two ends and Workload follow. About a day. |
| 2 | What may the Officer do with a social worker's case study (SCSR)? | **Read all of it, drafts included, and print. Not write, Final or Reopen.** The copy freezes the name and PRC license of whoever pressed Final as its preparer (`case_study/finalize.py:78`), and it is the preparing social worker's signed report. | Give the Officer the holder's level in `access_for`, decide whose name the copy carries, and tell the holder when somebody else changes it. |
| 3 | May the Officer remove a duplicate record? It is the only delete of a child. | **Yes**, with the same typed case number. | One list entry less. |
| 4 | May the Officer close a case by outcome? | **Yes**, the ISA's list (`CASE_OUTCOMES`), any record. The ISA keeps it too. | One list entry less. Taking it from the ISA is a later decision. |
| 5 | May the Officer record a psychologist's leave or availability? | **No.** The ISA for anybody, a psychologist for themselves, as now. | Two checks in `scheduling/views.py`. |
| 6 | The words on screen | **"SWO IV/OIC (RACC Officer)"** | Display only; one line. |
| 7 | How many Officers at once? | **Any number.** An OIC designation changes hands and can overlap. No hand-over rule like the single administrator's. | - |
| 8 | Who edits the agency details (Head of Office on the SCSR)? | **Still the ISA**, in Settings. | One permission. |
| 9 | Which emails does the Officer get? | **None yet**, like the ISA. Candidates are under "Not done, suggested". | New catalogue keys. |
| 10 | The child's Assistant log tab (who had the model read a record) | **Still the ISA only.** | One permission, one tab. |
| 11 | A Staff account that still holds active records cannot be changed to another role until they are moved | **Yes.** See "A promotion must not strand a caseload". | Drop one check. |

## A day as the Officer

1. Signs in. The Dashboard opens on **the casework desk**: records with no
   social worker, records whose holder's account is inactive, psychologist
   requests waiting too long or declined, possible duplicates; then each social
   worker's load beside each psychologist's. Under it, the agency's figures and
   the care gaps a social worker would act on, across every record.
2. Opens **Records**. Every child is there, with a Social worker column and
   filter, and "No social worker yet".
3. A new referral arrives: **Add record**, pick the social worker in the form,
   or leave it and assign it from the desk later.
4. A worker goes on leave: **Transfer records**, from her to two colleagues.
5. Opens a colleague's child: files the referral, asks a psychologist, books
   the session, starts a survey, reads the case study and prints it. Each of
   these tells the record's social worker the way it tells them today when the
   ISA or a psychologist acts on their record.
6. A case ends with the family reunified: **Terminate**, with the outcome.

What the Officer never sees: Users, Settings, Access Requests, the admin
console, a psychologist's pre-assessment wizard, the instruments catalogue.

## How it is built: capabilities, not role names

About 140 lines outside the tests name a role constant, and about 360 lines in
46 frontend files branch on `role_name`. Adding `Role.OFFICER` to each by hand
is how the twelfth call site gets written slightly differently. Instead every
list of roles becomes a **named capability**, defined once.

`backend/accounts/capabilities.py` (new):

| Name | Members when done | The question it answers |
|---|---|---|
| `EVERY_ROLE` | Administrator, Officer, Psychologist, Staff | may read case files at all |
| `SEES_EVERY_RECORD` | Administrator, Officer | scope: no narrowing |
| `KEEPS_RECORDS` | Administrator, Officer, Staff | writes a child's record (every place that says "Admin or Staff" today) |
| `SOCIAL_WORK` | Officer, Staff | follows the social worker's rule where it differs from the ISA's: care gaps, next steps, the drafting table, cancel-only |
| `OVERSEES_CASEWORK` | Administrator, Officer | chooses holders, transfers, closes by outcome, removes a duplicate, reads the casework desk |
| `HOLDS_RECORDS` | Staff | may be `Child.social_worker` |
| `HOLDS_A_LICENSE` | Officer, Psychologist, Staff | PRC license on My Profile |
| `CLAIMABLE_AT_SIGNUP` | Psychologist, Staff | what a stranger may say they are |
| `SIGNS_IN_WITH_GOOGLE` | Officer, Psychologist, Staff | the Google door, for an account that already exists |

`SEES_EVERY_RECORD` and `OVERSEES_CASEWORK` have the same members and are
different questions: one is what is read, the other what may be decided. A
read-only role added later would be in the first and not the second.

Three functions sit beside the lists, because a list cannot ask about one
child:

- `works_record(user, child)`: the social worker who holds it, or an Officer
  on any record. Replaces every "is this my record" comparison on the
  social-work side.
- `may_hold_records(user)`: the one rule for who can be a holder.
- `may_close_case(request, child)`: `OVERSEES_CASEWORK`, or the assigned
  psychologist. **New, and deliberately not `is_admin_or_assignee`.** That
  helper also decides who may summarise and confirm a psychologist's report
  (`clinical/serializers.py:200`, `assistant/views.py:457`). Adding the
  Officer to it would hand them the psychologist's documents. Two rules that
  look alike are still two rules.

`IsAdministrator` stays as it is and guards everything IT.
`IsAdminOrStaff` is renamed `KeepsRecords` (three call sites), so its name
does not lie once the Officer passes it.

**The browser's copy** is `frontend/src/config/roles.js`: the same lists under
the same names, one per line, plus `worksRecord(user, child)`. A backend test
reads the file and compares, the way `children/tests/test_intake.py` holds
`caseData.js` to `children/intake.py`. `config/nav.js` and the route guards in
`App.jsx` import from it instead of writing arrays.

**A guard against a second list:** a test reads the backend source and fails
on a tuple or list holding two or more `Role.` constants outside
`capabilities.py` (management commands included, migrations left out: a
migration freezes its own copy), and reads `frontend/src` for an array
literal holding two or more role names outside `config/roles.js`. A single
`role == Role.X` is still allowed: that is a branch, not a list.

"Must be a Staff account" is written out in four places today
(`children/serializers.py:139`, `children/transfer.py:121`,
`accounts/sms_notifications.py:130` and `:171`). All four become
`may_hold_records`, so decision 1 is one line whichever way it goes.

## What the Officer can do, door by door

"SW rule" means what a social worker may do on a record they hold.

### Records

| Door | The Officer | Where |
|---|---|---|
| Records, a child's page, Monitoring, Results & Reports, the header search | every record | `accounts/scoping.py:60`, one edit |
| Add record | yes. It belongs to the social worker picked in the form, or to nobody yet | `children/views.py:104` |
| Edit any record | yes, SW rule. The name still cannot change; intake rules as they are | `accounts/permissions.py:20`, `:61` |
| Custodian, number and consent to texts | yes | `children/custodian.py:83` |
| The duplicate check at intake | every match, with its link, as the ISA gets | `children/views.py:456` |
| "Reopen it - it becomes yours" | not offered. An Officer reopens in place and the holder stays | `children/views.py:330`, `:499` |
| Export Records or results, and the log line for it | yes | `children/views.py:435` |
| "is here" presence | yes, with the role's label | `children/views.py:227` |

### Who holds a record

| Door | The Officer | Where |
|---|---|---|
| The record form's Social Worker field | sets or changes it. The target must be a social worker account | `children/serializers.py:125` |
| Transfer records | yes. Both ends are social worker accounts, so an Officer is never an end | `children/views.py:383`, `children/transfer.py:119` |
| No social worker yet / holder's account inactive / psychologist request stuck / possible duplicates | on the casework desk | `accounts/admin_overview.py:314`-`472` |
| Remove a duplicate record | yes (decision 3) | `children/views.py:360` |
| Hold a record | no (decision 1) | |

### Asking a psychologist

| Door | The Officer | Where |
|---|---|---|
| Ask, ask someone else, withdraw, leave unassigned | yes, any record. Still asking, never assigning | `children/views.py:132`, `children/assignment_api.py:162` |
| See who was asked, and a decline's reason | yes | `children/serializers.py:175` |
| The list of requests | all of them | `children/assignment_api.py:113` |
| Accept or decline | never. Only the psychologist asked | `children/assignment_api.py:139` |
| The psychologist picker with caseloads | yes | `accounts/views.py:947` |

### How a case ends

| Door | The Officer | Where |
|---|---|---|
| Terminate | by case outcome, any record (decision 4) | `children/views.py:253`, `children/termination.py:75` |
| A psychologist's clinical reasons | never | |
| Reopen | yes. The holder is unchanged | `children/views.py:304` |

### The case study

| Door | The Officer | Where |
|---|---|---|
| Read every block, drafts included | yes | `case_study/access.py:91` |
| Print a draft or a final copy | yes | `case_study/views.py:352` |
| Start, write, make Final, Reopen | no. The record's social worker (decision 2) | `case_study/views.py:58`-`318` |
| The status-only card | stays the ISA's | |

### Clinical records

| Door | The Officer | Where |
|---|---|---|
| Read remarks, plans, results, interviews, pre-assessments, reports, consents, problems | yes, as a social worker reads them. The carry-history narrowing is the psychologist's and does not apply | `clinical/views.py:256` |
| Write any of them | no. The assigned psychologist (and the ISA, as now) | `clinical/views.py:275` |
| Case referral: file, replace, delete, summarise, confirm | yes | `clinical/views.py:541`, `accounts/permissions.py:103` |
| QR survey: start or close for any child; the forms offered follow the social worker's rule | yes | `clinical/views.py:660`, `:634` |
| Self-report flags: read and acknowledge | yes | `clinical/views.py:789` |
| A report check's finding that names another child | shown, since the Officer can see that child | `clinical/serializers.py:229` |
| Summarise or confirm a psychologist's report | no | `assistant/views.py:457` |
| Instruments and form templates | no | `accounts/permissions.py:41` |

### The calendar

| Door | The Officer | Where |
|---|---|---|
| See every session, with the child's name | yes | `scheduling/visibility.py:32` |
| Book, move, cancel for any child | yes, inside availability like a social worker | `scheduling/views.py:424`-`496` |
| Mark completed or no-show | no. The psychologist (and the ISA) | `scheduling/views.py:529` |
| Read availability, leave, slots, the month, openings | yes | |
| Write availability or leave | no (decision 5) | `scheduling/views.py:49`, `:320` |

### Dashboard, summary, audit

| Door | The Officer | Where |
|---|---|---|
| Dashboard | the agency's figures, with the casework desk on top | `clinical/reports_views.py:350` |
| Care gaps | the social worker's rules, over every record. Not the psychologist's gaps: a gap nobody at that desk can close is noise | `clinical/care_gaps.py:234` |
| Agency Summary, its CSV and its narrative | yes | `clinical/reports_views.py:282`, `assistant/views.py:619` |
| The bell and `/activity` | every line in the RECORD category; the per-record transfer lines in the trail. **Never** the USER or SECURITY categories, whatever `?category=` says | `activity/views.py:35` |

### The assistant

| Door | The Officer | Where |
|---|---|---|
| The chatbot | answers over every record. Names are masked the same way. **No prompt, tool description or `CHAT_SYSTEM` changes** | `assistant/tools.py:566` |
| Case brief facts, and "What to do next" on any active record | yes | `assistant/brief_facts.py:179` |
| The written brief; remark polish | no | `assistant/views.py:55`, `:123` |
| Record polish | yes | `assistant/views.py:178` |
| A case referral's summary (checked before it is shown) | yes | |
| Assistant settings, metrics, unanswered questions, the access log, model health | no | `IsAdministrator` |
| `STAFF_DRAFTING` | applies to the Officer exactly as to Staff | `assistant/guards.py:150` |

### Accounts

| Door | The Officer | Where |
|---|---|---|
| Ask for the role at sign-up, by form or Google | impossible | `accounts/serializers.py:329`, `accounts/google_auth.py:52` |
| Be given it | by the ISA: when approving a request, in Add user, or by changing a role | `accounts/views.py:826`, `accounts/serializers.py:154` |
| Sign in with Google | yes, once the account exists and is active | `accounts/google_auth.py:218` |
| My Profile: PRC license, mobile number, password | yes | `accounts/serializers.py:410` |
| Emails | none yet (decision 9) | `accounts/email_notifications.py:75` |
| Users, roles, access requests, credential hand-offs, Settings, test text and email, the admin console, Django admin | never | `IsAdministrator`, on 12 views |

## Six things that are new, not just a wider list

### 1. `GET /api/social-workers/`

The Social Worker field, Records' filter and the Transfer drawer all read the
list of social workers from `GET /users/` today. That is the user directory.
It is IT's and stays `IsAdministrator`. So the Officer needs a narrower door,
as `/api/psychologists/` already is for the psychologist picker.

For `OVERSEES_CASEWORK`. Staff accounts that are active or still hold an
active record: `[{id, name, active, records}]`, `records` being the count of
active records held. No email, no status beyond `active`. The ISA's screens
move to it too, so the ISA and the Officer cannot be shown different lists.

### 2. `GET /api/casework/overview/` and the casework desk

`accounts/admin_overview.py` already builds the four records queues and the
workload as separate functions (`_stuck_requests`, `_no_social_worker`,
`_archived_holder`, `_duplicates`, `_workload`). A second view serves only
those to `OVERSEES_CASEWORK`: `{generated_at, needs, workload}`. It never
carries people and sign-ins, health, the version, or the security log. A test
plants a sentinel in each of those and searches the whole response, as
`test_admin_overview.py` does.

On screen it is `pages/dashboard/CaseworkDesk.jsx`, mounted for the Officer
alone, with the queue tiles and Workload from the admin console and one
button, **Transfer records**. The Transfer drawer moves out of
`ConsoleActions.jsx` into its own file so both consoles use the one drawer.
Explicitly routed paths, both: the deploy probe in CLAUDE.md can tell them
from a 404.

### 3. A reading level for the case study

`case_study/access.py` has FULL (the holder), BLOCK_A (the assigned
psychologist) and STATUS (the ISA). The Officer gets a fourth, **READ**: every
section that applies, drafts included, the finals on file and their print;
`can_write` false, with "Only the social worker who holds this record can
change the case study." The answer is the holder's shape marked read-only
(`study.read_only`, which the editor already honours for a closed case), minus
the pre-fill seeds, which are offered to a writer. Check in a browser that the
editor's read-only state covers a draft and not only a closed case.

### 4. `may_close_case`

Described above. `termination.reasons_for` and `refusal` name their roles:
the psychologist's list, `OVERSEES_CASEWORK`'s list, and nothing for anyone
else.

### 5. A promotion must not strand a caseload

The likeliest first Officer is a senior social worker whose Staff account
holds twenty children. Change that account's role and the records are held by
an account that is no longer a social worker: Transfer refuses it as a source
("Choose a social worker (SW) account"), and no queue lists it. The same hole
exists today for Staff changed to Psychologist.

`UserWriteSerializer.validate_role` refuses a change **away from Staff** while
the account holds active records: "This account still holds 20 active records.
Move them first (Transfer records)." Closed records do not count: a closed
case another worker finds at intake is already reopened and taken over by
name.

The psychologist's side of the same hole (a psychologist changed to another
role while children are assigned) is not touched here. There is no bulk way to
reassign a psychologist's children, so a refusal would have no way out.

### 6. `status` is not a form field

Found while reading, and it exists today: `Child.status` is a writable field
on the record's serializer (`children/serializers.py:82`, not in
`read_only_fields` at `:99`), and an edit drops it only for a psychologist
(`:360`). So a Staff or ISA `PATCH {"status": "inactive"}` should close a case
with no reason, no note and no `TerminationRecord`, and the reverse should
reopen one without `reopen`. No screen sends it. Read from the code, not run.

It becomes read-only for every role: a case moves through `terminate` and
`reopen` only. Its own commit, with a test written first that shows the hole.
The Officer would otherwise inherit it over every record in the agency.

## Who is told

Nothing here needs a new email or text. What exists already handles "someone
other than the record's social worker did this":

- An Officer **books, moves or cancels** a session: the psychologist and the
  record's social worker are told as they are when the ISA does it
  (`scheduling/session_mail.py:167`), and the custodian's texts are unchanged.
- An Officer **asks a psychologist**: the psychologist's email says who asked
  and in what role. `_ASKER_ROLE` gains "the RACC Officer"
  (`accounts/email_notifications.py:742`). The answer goes to the record's
  social worker; on a record with none, to whoever asked, which for an Officer
  means the bell only, as for the ISA today.
- An Officer **transfers records**: the receiving worker's email and both
  bell sentences as now. The fallback name "The ISA" becomes the actor's role.
  The switch's description on My Profile says "the ISA or the RACC Officer".
  The catalogue **key** `records_transferred` does not change.
- An Officer **edits a record, asks a psychologist, or closes a case**: the
  record's social worker sees the line in their own bell, because their feed
  is every Child, Assignment and CaseStudy line on their records, whoever
  wrote it (`activity/views.py:74`).
- An Officer **files a referral or starts a survey** on someone's record:
  that worker's bell does not show it. Those lines are keyed by the
  referral's or the invite's own id, not the child's, so a social worker's
  feed cannot place them. It is the same today when the ISA does it, and it
  is left as it is; see "Not done, suggested".
- In the Officer's own bell the second copy of a transfer's sentence
  (`move_copy`) is left out, as it is for the ISA.

`KINDS` gets no Officer entries, so My Profile shows the Officer no email
switches, and `wants()` already refuses a role the kind is not for.

## The screens

The server decides; the screen only offers. But a button the server refuses,
or a screen that falls into another role's branch, is a defect.

- **`config/roles.js`, `config/nav.js`, `App.jsx`**: the lists; the Officer's
  rail and tabs are the social worker's (Dashboard, Records, Monitor,
  Calendar, Reports, Summary), plus `/activity` and `/profile`. Never
  `/users`, `/settings`, `/instruments`, `/pre-assessment`.
- **`ui/index.jsx`**: `ROLE_META.Officer`, `ROLE_ACCESS.Officer`, the
  `RoleBadge` dot. Not red (alerts) and told apart from the other three at a
  glance in the Users list.
- **The header**: the identity chip is sized against "ISA (Administrator)"
  with 30px in hand (CLAUDE.md, "The header search"). The new label is six
  characters longer. Measure at 1280, 1366 and 1440 signed in as the Officer;
  if it does not fit, the chip shows a short form ("RACC Officer") and menus
  keep the full label.
- **Dashboard**: the Officer gets the agency layout the ISA has, with
  `CaseworkDesk` where `AdminConsole` is. Quick actions are the social
  worker's three.
- **Records** (`pages/Children.jsx`): `isAdmin` there means three things and
  is split: *sees every record* (the Social worker column, filter, "No social
  worker yet", the export's column); *oversees casework* (the Social Worker
  field's list, Terminate, Remove duplicate); and the ISA alone (nothing on
  this screen). `canManage` becomes `KEEPS_RECORDS`.
- **The record drawer and form**: the "Next step" line uses `worksRecord`;
  "Ask the ISA (Administrator) to transfer it to you" becomes "Ask the RACC
  Officer or the ISA".
- **The child's page**: referral upload, survey and referral summary follow
  `KEEPS_RECORDS`; Print on the Case study tab is for whoever may print it;
  the Assistant log tab stays the ISA's, and so does `canAdvance` (the ISA or
  the assigned psychologist: a report's summary and its check). `CaseStudyTab`
  gives the Officer the read-only editor. With no survey form set up, the
  Officer is told what a social worker is told: ask the ISA to add one.
- **Results & Reports** (`pages/Report.jsx`): "Upload case referral" follows
  `KEEPS_RECORDS`. Reviewing a report's check stays the psychologist's and
  the ISA's.
- **Care gaps** (`config/careGaps.js:36`): the rule that sends a
  psychologist-only gap to the child's page instead of `/pre-assessment`
  asks "is this the ISA". It becomes "anyone but a psychologist", so no role
  is ever sent to a screen it cannot open.
- **Calendar**: `canBook` includes the Officer. Add availability, record
  leave, completed and no-show stay as they are.
- **Header search**: the social worker's actions, plus "No social worker yet"
  under Needs you. The colleague search reads `/users/` and stays the ISA's.
- **My Profile**: the license fields; the personal column only, as the ISA,
  because the right-hand lists would be the whole agency's; the email card is
  hidden for a role with no kinds, not for "the ISA".
- **Audit trail and bell**: the Officer's sentence ("Every record event in the
  agency") and the RECORD tab only.
- **Users and Access Requests**: both read `/api/roles/`, so the role appears
  in their lists by itself. They need its label, its description and its tone.
  Sign-up and the Google button keep offering two roles.
- **The ISA's console**: the people card counts Officers; nothing else.

Check each of the four roles in a browser at 1440, 1280 and 390, and run
axe-core on every screen that changed.

## What stays exactly as it is

- A social worker still sees only their own records. A psychologist still
  sees only their assigned children, under the carry-history control.
- The ISA loses nothing and gains nothing.
- Only acceptance writes `assigned_psychologist`.
- The case study is written by the record's social worker; the ISA sees its
  status; it is not in Django admin.
- An Officer account is never `is_staff` or a superuser. Django admin is the
  ISA's.
- No prompt, tool description or `CHAT_SYSTEM` byte changes, so nothing needs
  a new `ai_eval` run. The Officer's "what you can ask" sentence and starter
  questions are server-side tables (`assistant/tools.py:477`, `:517`), and the
  starters reuse labels already measured.
- No stored key is renamed: not a role, not an email kind, not a section.

## Work packages

In order. Each is one worker's job with its own commits, and the planner reads
what comes back before the next starts. Both checks before every commit:

```
cd backend && .venv/bin/python manage.py test --parallel 4 > ../tests.log 2>&1; echo $?
cd frontend && npm run lint && npm run build
```

(Linux paths; on the owner's PC, `.venv\Scripts\python.exe`. Never pipe a test
run through `tail`.)

### WP0. Baseline, and a table of every route

- `backend/requirements-dev.txt`: add `tblib`. Without it a failure inside a
  parallel worker can crash the runner with "cannot pickle 'traceback' object"
  and the failing test's name is lost. That happened once while this plan was
  written.
- `backend/config/tests/test_every_route_is_decided.py` and a data file
  beside it. The test walks Django's URL resolver for everything under
  `/api/`, and for each route and method asks as: nobody, the ISA, a social
  worker on their own child, a social worker on someone else's, the assigned
  psychologist, an account with no role, and an account whose role is a row
  called "Officer" **made in the test database only**. Writes go with an
  empty body inside a savepoint that is rolled back. Outcomes are four words:
  *past* (2xx, or 400 from validation), *refused* (401/403), *hidden* (404),
  *no such verb* (405); a list answer also records whether it had rows.
- **A route or verb that is not in the table fails the test** with "decide
  what each role gets here". That is this codebase's guard for the twelfth
  call site, like `STAFF_DRAFTING` and `KEEPS_IT`.
- **Done when:** the suite is green; the table is committed as today's truth;
  and the planner has compared its unknown-role column with the fail-open
  list at the top of this note, and corrected whichever is wrong.

### WP1. Name the capabilities, close the `else` branches

No behaviour changes for the three roles, and the lists do **not** contain
the Officer yet.

- New `accounts/capabilities.py`; `frontend/src/config/roles.js`, `nav.js`
  and `App.jsx` take their lists from the mirror; the pin test; the
  second-list guard.
- Every enumerated tuple becomes a named list, in: `accounts/scoping.py`,
  `permissions.py`, `serializers.py`, `google_auth.py`, `views.py`,
  `admin_overview.py`, `sms_notifications.py`; `children/views.py`,
  `serializers.py`, `custodian.py`, `termination.py`, `assignment_api.py`,
  `transfer.py`; `clinical/views.py`, `reports_views.py`, `serializers.py`,
  `care_gaps.py`; `scheduling/views.py`, `visibility.py`;
  `assistant/views.py`, `tools.py`, `brief_facts.py`,
  `management/commands/ai_eval.py`; `activity/views.py`.
- The four "must be a Staff account" checks become `may_hold_records`.
- Every `else` that meant "the Administrator" names the Administrator, and
  what is left over is refused or sees nothing: the five doors in the first
  table of this note, and the five behind a gate.
- `may_close_case` replaces `is_admin_or_assignee` in `terminate` and
  `closure_reasons` only.
- `status` read-only, as its own commit with its own test.
- **Done when:** every existing test passes unedited (imports for the renamed
  permission class aside); the route table's known columns have not moved;
  its unknown-role column is all *refused*, *hidden* or *no rows*.

### WP2. The role exists

- `Role.OFFICER`, and `Role.NAMES`, the rows that must exist;
  `accounts/migrations/0019_officer_role.py`, a data step that creates the
  row if it is missing and whose reverse does nothing (an account may hold
  it); `seed_initial_data` seeds from `Role.NAMES`. That is not `EVERY_ROLE`:
  one says which rows exist, the other who may read case files, and the
  Officer's row exists a work package before the Officer may read anything.
- `HOLDS_A_LICENSE`, `SIGNS_IN_WITH_GOOGLE`, `CLAIMABLE_AT_SIGNUP` take their
  final members. The Google door's last check (`google_auth.py:218`) reads
  `SIGNS_IN_WITH_GOOGLE`, not the claimable list: a Google-only account has
  no password, so a promoted one would otherwise be locked out for good.
- The role-change refusal for a Staff account holding active records.
- The ISA console's people counts.
- **Done when,** by test: the row is there after `migrate` on a database that
  already has accounts; "Officer" cannot be claimed at either sign-up door
  and the refusal reads like the Administrator's; the ISA can grant it at
  approval and in Add user; an active Officer signs in by password and by
  Google; Staff to Officer is refused with the count while records are held
  and passes once they are moved; the license saves; and in the route table
  the Officer is *refused* or *no rows* everywhere except the doors every
  signed-in account has.

### WP3. Every record, and a social worker's work on any of them

- `EVERY_ROLE`, `SEES_EVERY_RECORD`, `KEEPS_RECORDS` and `SOCIAL_WORK` gain
  the Officer. `works_record` goes in at the survey rule
  (`clinical/views.py:660`) and next steps (`assistant/brief_facts.py:179`).
  `shows_name`. The Officer's branch of the activity scope. The Officer's
  "what you can ask" and starters. Record polish. The export log.
- **Done when,** by test, an Officer: sees another worker's child and an
  unheld one at every door `children/tests/test_own_records.py` closes to a
  social worker; edits a record and the edit **asks** a psychologist (extend
  `test_a_pending_child_is_absent_from_every_door`); records a custodian;
  files and deletes a referral; starts a survey; books, moves and cancels and
  is refused completed and no-show; sees names on the calendar; gets the
  social worker's care gaps over all records; is refused every clinical
  write, the written brief and remark polish; gets `brief: "case"`; and has
  no USER or SECURITY line in the feed or the trail, with `?category=security`
  tried (a sentinel line of each kind is planted). `STAFF_DRAFTING`'s test
  runs as an Officer too.

### WP4. Oversight: who holds a record, how a case ends

- `OVERSEES_CASEWORK` gains the Officer: the Social Worker field, Transfer,
  Remove duplicate, `may_close_case`, the request list. `GET
  /api/social-workers/` and `GET /api/casework/overview/`.
- The sentences that send people to the ISA name the Officer first:
  `children/duplicates.py:166`-`168`, `children/views.py:258`, `:337`,
  `:374`, `:398`, `children/serializers.py:138`. `_ASKER_ROLE`, the fallback
  name and the switch's description in `email_notifications.py`.
- **Done when,** by test, an Officer: sets and changes a record's social
  worker and is refused a non-Staff target; transfers in bulk and is refused
  as either end; closes with a case outcome and is refused a clinical reason,
  with every reason offered accepted; reopens with the holder unchanged;
  removes a duplicate; reads the casework overview, which the sentinel test
  shows carries nothing of IT; and is refused `/api/admin/overview/`,
  `/users/`, `/roles/`, both test senders, assistant settings and the rest of
  `IsAdministrator`. A social worker is still refused all of WP4.

### WP5. The case study, read

- The READ level in `case_study/access.py`, its payload and its sentences;
  print for READ.
- **Done when,** by test, an Officer reads all three blocks of another
  worker's draft, prints a draft and a final, and is refused Start, the
  header, a box, Final and Reopen with the holder's sentence; the ISA is
  still status only; the psychologist still block A.

### WP6. The screens

Everything under "The screens", then the browser pass for all four roles.
**Done when** lint and build pass, each changed screen has been loaded as
each role at three widths, axe-core is clean on them, and the header fits.

### WP7. Demo account, documents

- `seed_demo_data`: one Officer account, in the style of the others, holding
  nothing. `demo_owners.active_staff` already deals only to Staff; a test
  says an Officer present on the branch is never dealt a child nor made a
  case study's preparer.
- `ai_eval`'s callers include the Officer, so the owner's next run says
  "NOT MEASURED" rather than nothing.
- `docs/LOCAL-SETUP.md` (the sign-in), the access matrix in
  `docs/CLOUD-DEPLOYMENT.md`, and CLAUDE.md: a section for the role, and the
  lines in "Each social worker's own records", "Closing a case", "The case
  study", "Names on the schedule", "Role names on screen", "Getting an
  account" and the test count that this changes.

## Traps for whoever builds it

- **`else` means somebody.** Every branch names its role; the remainder is
  refused.
- **`is_admin_or_assignee` is the clinical documents' rule.** Do not widen it.
- **`/users/` is never opened to the Officer.** Use `/api/social-workers/`.
- **Prompts and tool descriptions are routing logic.** Not one byte.
- **A list is not a rule about one child.** "Is this my record" is
  `works_record`, not membership of `SOCIAL_WORK`.
- **The demo Officer holds nothing**, and seeded data must still satisfy the
  rules the endpoints enforce.
- **Do not add email kinds for the Officer** to make a switch appear. Kinds
  are keyed by role and stored by key.
- **`hide_earlier_history` stays the psychologist's.**
- **The role row must never reach a database served by code older than WP1.**
  See the next section.

## Deploying it

Local work only, as always. For the day it goes to the demo: the hosted API
runs `migrate` on every start, and the old release keeps serving while the new
one builds. If the row from WP2 landed while pre-WP1 code was still answering,
an account made an Officer in that window would be read by the old `else`
branches as the ISA. So WP1 ships and is verified as deployed **before** any
push that carries the migration. Two pushes, not one. The migration itself is
additive and safe for a release still serving.

## Tests, in one place

- `accounts/tests/test_capabilities.py`: the lists, the JavaScript mirror, the
  second-list guard, the migration's row.
- `config/tests/test_every_route_is_decided.py`: the table, WP0 onward.
- An Officer's cases in the files that already pin each rule: children
  (`test_own_records`, `test_transfer`, `test_reopen`,
  `test_reopen_at_intake`, `test_termination`, `test_closure_reasons`,
  `test_remove_duplicate`, `test_assignment_requests`, `test_custodian`,
  `test_child_collab`, `test_one_record_per_child`, `test_api`,
  `test_demo_transfer`), case_study (`test_access`, `test_final`,
  `test_endpoints`, `test_demo`), and their counterparts in clinical,
  scheduling, assistant, activity and accounts.
- Fixtures: tests make roles with `Role.objects.get_or_create(role_name=...)`
  (`case_study/tests/base.py:21`, `children/tests/test_child_collab.py:10`),
  so an Officer in a test is one more call. No test reads the wall clock.

## Not done, suggested

- **The Officer's emails.** A weekly count of records with no social worker;
  a psychologist's decline on a record nobody holds; a transfer made by the
  ISA.
- **Reviewing a case study in the system.** Nobody approves an SCSR today; it
  is signed on paper. With an Officer there is at last somebody who could
  mark one reviewed and send it back with a note.
- **Telling a record's social worker when somebody else files its referral
  or starts its survey.** Today those lines go to the psychologist only.
- **Taking casework away from the ISA**, now that someone else can hold it.
  `children/admin.py` also registers the child in Django admin, where the
  ISA's superuser can set a holder or a psychologist outside every rule.
- **The psychologist's side of "a promotion must not strand a caseload".**
- **An Officer who also carries cases** (decision 1's other answer).

## What was checked for this plan, and what was not

- Read from the code on 16 Nov 2026: every role constant, permission class
  and role list in the backend, and every `role_name` branch in the frontend.
  The children and case-study apps were also mapped by an independent reader,
  whose 44 sites agreed with this note and supplied several of its traps.
- The other independent readers, and the review pass meant to try to break
  this plan, did not run: the helpers hit the account's usage limit. So this
  note has been checked by one reader, not several.
- **Not measured:** the fail-open list at the top, and the `status` hole.
  Both are read from the code. WP0's table measures the first; WP1's test
  shows the second.
- Baseline at this commit, on Linux: 3,794 tests found, green in 35 s with
  `--parallel 4`; `npm run lint` 0 errors and 25 warnings; `npm run build`
  passes. An earlier run of the suite, minutes before, died without naming
  its failure (the `tblib` point in WP0), at 23:16 Manila time. Whether that
  was the clock was not established.
