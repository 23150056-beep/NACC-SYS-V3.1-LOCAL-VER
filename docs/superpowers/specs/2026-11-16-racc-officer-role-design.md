# The RACC Officer: a Fourth Role, Above the Social Worker

**Date:** 2026-11-16
**Status:** Being built on `claude/sweet-wozniak-9fzjme`, with the owner's
decisions in. WP0 is in (`ae37c40`, 22 Nov 2026), WP1 is in (`c7eae2c`, `4d4416f`,
23 Nov) WP2 is in (25 Nov) and WP3 is in (`afa0935`, 26 Nov); WP4 is next. The
case study removal is merged (`b6940c3`) and has been live on the demo since 22
Nov; the lifecycle branch is not merged yet ("Before WP0"). An independent
review read this note against the code on 22 Nov 2026. What it changed is
marked "(review)", and where it asked a question, the planner's answer is
under "The planner's defaults".
**Read at:** commit `ab447f2` (`main`), so line numbers are that commit's. On
19 Nov the claims were checked again against `cloud-setup` (`48124fc`) and two
branches not yet merged into it: `claude/bold-carson-4oq44s` (the case study,
PRC license and Head of Office removed) and `claude/amazing-fermat-8hity4` (one
door for every status). Where those change what this note says, it says so.
Search by the symbol named; the line will have moved. Each work package's
brief carries the current lines.
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
default and it should stay. But it is not true everywhere. These doors decide
with an `else` that means "the Administrator", so a fourth role walks into the
ISA's branch without anybody deciding it. **Measured on 17 Nov 2026**, by
giving a test account a Role row the code does not name and asking as it; the
last row was read from the code, not run:

| Door | What an unknown role gets | Where |
|---|---|---|
| The bell and the audit trail | the whole audit stream. It read a security line and a user line, and `?category=security` gave it the security lines alone | `activity/views.py:81` |
| The appointments list | every session in the agency (names masked) | `scheduling/views.py:407` |
| Moving an appointment | `PATCH` answered 200, for the notes and for a new time. The own-child check asks Staff only | `scheduling/views.py:490` |
| The Dashboard | today's sessions and availability, unfiltered | `clinical/reports_views.py:379` |
| Availability and leave | every psychologist's hours, and their leave with its reason. These two lists ask only that somebody is signed in | `scheduling/views.py:40`, `:310` |
| Slots and the month, asked without a child (review) | any psychologist's bookable times | `scheduling/views.py:203`-`209`, `:262`-`268` (current lines) |
| The assistant's schedule answers | the agency's | `assistant/tools.py:566` |
| The assistant's availability answer (review) | every active psychologist's free windows, with their email addresses | `assistant/tools.py:1306` (current line) |

The same account was refused or shown nothing everywhere else it was tried:
Records, a child's page, Monitoring, the clinical records, the Agency Summary,
Users, roles, the admin console, booking, cancelling, closing, reopening,
Transfer, Remove duplicate.

**Measured again on 22 Nov 2026 by WP0's route table** (`ae37c40`): every
route and method under `/api/`, as seven accounts. The table bears out every
row above that a route can show, and adds the detail routes behind them: one
appointment (`GET`, and `PUT` as well as `PATCH`), one availability window,
one leave entry. Booking, filing a referral, starting a survey and writing
availability or leave let the unknown role through the gate, but its empty
body was refused. Read in the code, each of those then checks the child
against the caller's scope or names its roles, so a real body is refused too.
Everywhere else an authenticated door refused it or answered an empty list,
and the account with no role was refused at every authenticated door. The two
rows marked "(review)" need a query string or a tool call, which the table
does not send.

And these sit behind a gate today but would open the moment the gate admits a
new role: Add Record's `else` is the ISA's path (`children/views.py:123`); an
edit's `else` writes `assigned_psychologist` directly, with nobody asked
(`children/views.py:155`); closure reasons give any non-psychologist the ISA's
list (`children/termination.py:75`); the custodian rule treats "not ISA, not
Staff" as a psychologist (`children/custodian.py:83`); care gaps give any
non-Staff the ISA's list (`clinical/care_gaps.py:242`).

The code already knows this about itself. `accounts/authentication.py:37`
says roles are read as "if psychologist ... else everything" in a dozen
places, and that "rather than audit and invert a dozen call sites" it refuses
an account with **no** role at the one door they all come through. That
backstop asks whether there is a role at all. An Officer has one, so it does
not catch an Officer.

No screen can make such an account, because the only Role rows are the
three. It can be made, though (review): `Role` is registered in Django admin
(`accounts/admin.py:5`), where the ISA's superuser can add or rename a row,
and Users then accepts any role but the Administrator's. It becomes real the
day a fourth row is added. So the first thing the build does is the audit
that comment put off: close those branches, before the role exists (work
package 1). WP1 also makes `Role` read-only in Django admin, and widens the
backstop above to refuse any role the code does not know.

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
follows the social worker's rule, not the ISA's: the written brief and remark
polish.

## The owner's decisions (19 Nov 2026)

The owner took every suggestion: "proceed to all suggestion". The one
exception is the old decision 2, what the Officer may do with a case study.
The owner has removed the case study (SCSR) feature, so the question no longer
exists (`claude/bold-carson-4oq44s`, 17 Nov 2026). Each decision sits behind a
single named list or function, so changing one later is a small change.

| # | Question | Decided | The other way would cost |
|---|---|---|---|
| 1 | Does the Officer also hold records of their own? | **No.** They assign and work on everyone's; a holder is a Staff account. | Add the Officer to `HOLDS_RECORDS`; Add Record's default holder, the pickers, Transfer's two ends and Workload follow. About a day. |
| 2 | ~~What may the Officer do with a social worker's case study?~~ | **Does not apply.** The case study is removed. | - |
| 3 | May the Officer remove a duplicate record? It is the only delete of a child. | **Yes**, with the same typed case number. | One list entry less. |
| 4 | May the Officer close a case by outcome? | **Yes**, the ISA's list (`CASE_OUTCOMES`), any record. The ISA keeps it too. | One list entry less. Taking it from the ISA is a later decision. |
| 5 | May the Officer record a psychologist's leave or availability? | **No.** The ISA for anybody, a psychologist for themselves, as now. | Four checks (review): the two write checks and combine in `scheduling/views.py`, and the serializer's owner check. |
| 6 | The words on screen | **"SWO IV/OIC (RACC Officer)"** | Display only; one line. |
| 7 | How many Officers at once? | **Any number.** An OIC designation changes hands and can overlap. No hand-over rule like the single administrator's. | - |
| 8 | Who edits the agency details (name, address, contact)? | **Still the ISA**, in Settings. The Head of Office fields went with the case study. | One permission. |
| 9 | Which emails does the Officer get? | **None yet**, like the ISA. Candidates are under "Not done, suggested". | New catalogue keys. |
| 10 | The child's Assistant log tab (who had the model read a record) | **Still the ISA only.** | One permission, one tab. |
| 11 | A Staff account that still holds active records cannot be changed to another role until they are moved | **Yes.** See "A promotion must not strand a caseload". | Drop one check. |

Decisions 1, 3, 4 and 6 were explained to the owner one by one first; the
others are the plan's defaults, accepted with the rest.

### The planner's defaults (22 Nov 2026)

The two backend readers asked seven questions and raised one judgement call;
the frontend reader asked five more. None blocks WP1. Each is answered by the
reading of the owner's decisions that fits them best, and the owner can
overrule any of them:

| # | Question | Default |
|---|---|---|
| a | A promoted worker's **closed** records come back active, still theirs, when the ISA or an Officer reopens them in place. Who holds them then? | Whoever held them, but they are **listed and can be moved**. The queue "held by an archived account" becomes "held by an account that cannot hold records" (inactive, or no longer a social worker), and Transfer's *from* end takes any account that holds active records; only the *to* end must be an active social worker. This also covers today's hole, a Staff account changed to Psychologist. |
| b | Decision 11 refuses a change **away** from Staff while records are held. Does that include Staff to Psychologist, which the ISA can do today? | Yes. Decision 11's words cover every change away from Staff, and a psychologist cannot hold records either. It is a deliberate change to what the ISA can do. |
| c | May the Officer read a psychologist's leave **reason**? | Yes, as a social worker does today (rule 2). |
| d | The approval email when a Google applicant is made an Officer | "approved ... as a Social Welfare Officer IV/OIC (RACC Officer)". Today it would say "as Officer". |
| e | The per-account email limits (60 an hour, 150 a day), for an Officer acting on every record | Unchanged, and raised only if an Officer actually meets them. A transfer is one email however many records move. |
| f | "What to do next" for an Officer | On any active record they open, like the record's social worker (rule 2), through `works_record`. |
| g | "How many RACC Officers are there?" in the chat | Answered only within "everyone". Adding "officer" to `count_people` changes the tool list, which needs the owner's `ai_eval` run. |
| h | `CHAT_SYSTEM` says the user is "a psychologist, a staff member or an administrator" | Left byte-identical on purpose (no prompt changes). Its comment in `prompts.py` is corrected, which is not a prompt byte. |
| i | The Officer's dot colour | A new violet token. Red means an alert and green means healthy. Its text tokens are checked against white, `--ink-50` and `--ink-100`, as CLAUDE.md asks. |
| j | The header chip's short form | "RACC Officer". The full "SWO IV/OIC (RACC Officer)" in the menu, the rail's card and Users. |
| k | The ISA's "Clinical team this range" card on the Officer's Dashboard | Kept, as part of the agency layout. |
| l | Should Records' social-worker filter, and the desk, list deactivated holders? | Only those still holding active records, marked "(inactive)". The Social Worker field never offers them. |
| m | A warning when the ISA grants the role at Access Requests | Yes, as for Psychologist: "This account will see every child's record in the agency." |

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
   the session, starts a survey. The
   record's social worker hears of the booking by email and text, and sees the
   request in their bell, as they do today when the ISA acts on their record
   ("Who is told" has the two that stay quiet).
6. A case ends with the family reunified: **Terminate**, with the outcome.

What the Officer never sees: Users, Settings, Access Requests, the admin
console, a psychologist's pre-assessment wizard, the instruments catalogue.

## How it is built: capabilities, not role names

About 140 lines outside the tests name a role constant, and about 260 lines in
35 frontend files branch on a role (review; an earlier count said 360 in 46). Adding `Role.OFFICER` to each by hand
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
| `CLAIMABLE_AT_SIGNUP` | Psychologist, Staff | what a stranger may say they are |
| `SIGNS_IN_WITH_GOOGLE` | Officer, Psychologist, Staff | the Google door, for an account that already exists |
| `WRITES_CLINICAL` (review) | Administrator, Psychologist | writes a clinical record; remark polish |
| `MANAGES_AVAILABILITY` (review) | Administrator, Psychologist | combines availability windows (writing one is two single checks) |
| `INSTRUMENT_MANAGERS` (review) | Administrator, Psychologist | `INSTRUMENT_MANAGER_ROLES`, moved here unchanged |
| `RECORDS_OWN_SESSION_OUTCOMES` (WP1a) | Administrator, Psychologist, Staff | records completed or no-show on a session the account holds as its psychologist, whatever its role is now. The Officer never joins it (rule 6) |

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

**The lists are tuples, read through the module** (`capabilities.KEEPS_RECORDS`,
never `from accounts.capabilities import KEEPS_RECORDS`) and never copied into
a constant at import time, so a test can widen one list for one test (WP1).

**The door they all come through** (WP1, planner, 22 Nov 2026).
`accounts/authentication.py` already refuses an account with no role. It also
refuses one whose role is not in `EVERY_ROLE`, at every authenticated door, at
sign-in and at refresh: "This account's role is not set up in this version of
NACC SYS." A role row that reaches a database before the code names it (a
migration that runs ahead, a row added in Django admin, a fifth role one day)
then reaches nothing until someone deliberately puts it in `EVERY_ROLE`; for
the Officer that is WP3. It does not replace naming the branches: once the
Officer is admitted, every `else` it could fall into must already name its
role.

**The browser's copy** is `frontend/src/config/roles.js`, which already exists
(review). It holds `INSTRUMENT_MANAGER_ROLES`, imported by `App.jsx:110` and
pinned by `config/tests/test_cross_stack_roles.py`, whose `_js_list` reads an
`export const NAME = ['...']` of string literals and nothing else. WP1b adds
the same lists under the same names, in that form, plus
`worksRecord(user, child)`, and extends that test rather than writing a second
one. `config/nav.js`, the route guards in `App.jsx` and the seven screens
that hold role arrays today (`Children.jsx:155`, `ChildProgressReport.jsx:165`
and `:169`, `Schedule.jsx:247`, `Report.jsx:30`, `Signup.jsx:26`,
`Login.jsx:14`) import from it instead of writing arrays, each a one-line swap
with no behaviour change.

**A guard against a second list:** a test reads the backend source and fails
on a tuple, list or set holding two or more roles (`Role.` constants or the
role-name strings) outside `capabilities.py`, and on a list imported by name
from it. Management commands are included. Tests and migrations are left out
(review): a migration freezes its own copy and a test builds fixtures. The
test also reads `frontend/src` for an array literal holding two or more role
names outside `config/roles.js`. A single `role == Role.X` is still allowed:
that is a branch, not a list. So is an array holding one role name among
other words, such as a table's headings (`AgencySummary.jsx:451`,
`PsychReportPrint.jsx:108`; review).

"Must be a Staff account" is written out in four places today
(`children/serializers.py:139`, `children/transfer.py:121`,
`accounts/sms_notifications.py:130` and `:171`). All four become
`may_hold_records`, so decision 1 is one line whichever way it goes. A fifth
picks the console's Workload by `role=Staff` (`accounts/admin_overview.py:767`,
review) and reads `HOLDS_RECORDS`.

## What the Officer can do, door by door

"SW rule" means what a social worker may do on a record they hold.

### Records

| Door | The Officer | Where |
|---|---|---|
| Records, a child's page, Monitoring, Results & Reports, the header search | every record | `accounts/scoping.py:60`, one edit |
| Add record | yes. It belongs to the social worker picked in the form, or to nobody yet. Picking is the Social Worker field (below), so from WP3 until WP4 an Officer's new record belongs to nobody (review) | `children/views.py:104`, `children/serializers.py:127`-`143` |
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
| Transfer records | yes. The *to* end is an active social worker account, so an Officer is never one. The *from* end is any account holding active records, so a record stranded on a re-roled account can be moved (default a) | `children/views.py:383`, `children/transfer.py:119` |
| No social worker yet / held by an account that cannot hold records / psychologist request stuck / possible duplicates | on the casework desk. The second queue was "holder's account inactive"; it also lists a holder that is no longer a social worker (default a) | `accounts/admin_overview.py:314`-`472` |
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

All of this opens in WP3, with the ask itself (review): the request list
(`SEES_EVERY_RECORD`), withdraw and who-was-asked (`KEEPS_RECORDS`). Asking in
WP3 and withdrawing only in WP4 would leave an Officer able to ask and not to
take it back.

### How a case ends

| Door | The Officer | Where |
|---|---|---|
| Terminate | by case outcome, any record (decision 4) | `children/views.py:253`, `children/termination.py:75` |
| A psychologist's clinical reasons | never | |
| Reopen | yes. The holder is unchanged; a holder who cannot hold records puts it in the casework queue (default a). A `KEEPS_RECORDS` door, so it opens in WP3 (review) | `children/views.py:304` |
| Close or reopen by editing the record | never, for anyone, **once the lifecycle branch is merged**: an edit refuses a status change. Until then both directions are open on this branch (section 5) | `children/lifecycle.py`, `config/lifecycle.py` (lifecycle branch) |

On the lifecycle branch, Terminate and Reopen move the status through
`CHILD_LIFECYCLE.move()`, a conditional update that answers 409 to the second
of two clicks, and a closed case can no longer ask a psychologist. Those rules
are role-blind, so the Officer gets them by passing the same doors; nothing
in them needs an Officer branch.

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
| See every session, with the child's name | yes: `shows_name` reads `SEES_EVERY_RECORD`, the psychologist, or `works_record` | `scheduling/visibility.py:32` |
| Book, move, cancel for any child | yes, inside availability like a social worker. On the lifecycle branch only a scheduled session moves or is cancelled, and a session never changes child | `scheduling/views.py:424`-`496` |
| The booking drawer's psychologist picker (review) | yes, as a social worker: `KEEPS_RECORDS`. Refused, the drawer fails open, so the greyed names and their reasons would silently go | `scheduling/views.py:315`-`327` (current lines), `pages/schedule/psychologistPicker.js:31` |
| Mark completed or no-show | no. The psychologist (and the ISA): `RECORDS_OWN_SESSION_OUTCOMES`, which the Officer never joins | `scheduling/views.py:529` |
| Read availability, leave, slots, the month, openings | yes | |
| Write availability or leave | no (decision 5). Decided in four places (review): the two write checks, combine, and the serializer's owner check | `scheduling/views.py:49`, `:320`; current `:60`, `:106`, `:448` and `scheduling/serializers.py:196`-`200` |

### Dashboard, summary, audit

| Door | The Officer | Where |
|---|---|---|
| Dashboard | the agency's figures, with the casework desk on top, and today's sessions and availability across the agency, with names. In WP1 the ISA's branch is written as `SEES_EVERY_RECORD`, so WP3 reaches it. The Staff branch stays `== Role.STAFF`, because it filters on the records the user holds (review) | `clinical/reports_views.py:350` |
| Care gaps | the social worker's rules, over every record. Not the psychologist's gaps: a gap nobody at that desk can close is noise | `clinical/care_gaps.py:234` |
| Agency Summary, its CSV and its narrative | yes | `clinical/reports_views.py:282`, `assistant/views.py:619` |
| The bell and `/activity` | every line in the RECORD category; the per-record transfer lines in the trail. **Never** the USER or SECURITY categories, whatever `?category=` says | `activity/views.py:35` |

### The assistant

| Door | The Officer | Where |
|---|---|---|
| The chatbot | answers over every record, tool by tool below. Names are masked the same way. **No prompt, tool description or `CHAT_SYSTEM` changes** | `assistant/tools.py:566` |
| Case brief facts, and "What to do next" on any active record | yes | `assistant/brief_facts.py:179` |
| The written brief; remark polish | no | `assistant/views.py:55`, `:123` |
| Record polish | yes | `assistant/views.py:178` |
| A case referral's summary (checked before it is shown) | yes | |
| Assistant settings, metrics, unanswered questions, the access log, model health | no | `IsAdministrator` |
| `STAFF_DRAFTING` | applies to the Officer exactly as to Staff | `assistant/guards.py:150` |

What each tool answers an Officer (review). Three of them carry a sentence
written for a social worker behind a single `== Role.STAFF`, and mapping it to
`SOCIAL_WORK` would tell an Officer "these are your own records".

| Tool | The Officer |
|---|---|
| `list_my_appointments` | the agency's sessions, as the ISA's (`SEES_EVERY_RECORD`), with names |
| `get_statistics` | no "these are your own records" note (`tools.py:908`-`915` stays `== Role.STAFF`) |
| `list_care_gaps` | the social worker's rules over every record; its own empty sentence, "Nothing outstanding in any record." |
| `list_unassigned_children` | the agency's sentence |
| `find_availability` | every psychologist, as Staff |
| `count_people` | Officers counted only within "anyone" (default g) |
| the child summary, concern search, self-report flags | every record |

The Officer's four starters are chosen from labels that are already
`CHAT_CASES` and pass (review: several current starters are not cases). A
starter never reaches the model anyway: chips run through
`/assistant/followup/` (`StarterQuestionsTest`).

### Accounts

| Door | The Officer | Where |
|---|---|---|
| Ask for the role at sign-up, by form or Google | impossible | `accounts/serializers.py:329`, `accounts/google_auth.py:52` |
| Be given it | by the ISA: when approving a request, in Add user, or by changing a role. A Google applicant approved as one is emailed "as a Social Welfare Officer IV/OIC (RACC Officer)" (`_role_words`; review, default d) | `accounts/views.py:826`, `accounts/serializers.py:154`, `children/notifications.py:374` |
| Sign in with Google | yes, once the account exists and is active, from WP3, when the role is admitted | `accounts/google_auth.py:218` |
| My Profile: mobile number, password, profile links | yes. The PRC license fields are gone with the case study | `accounts/views.py` `MyProfileView` |
| Emails | none yet (decision 9) | `accounts/email_notifications.py:75` |
| Users, roles, access requests, credential hand-offs, Settings, test text and email, the admin console, Django admin | never | `IsAdministrator`, on 12 views |

## Five things that are new, not just a wider list

### 1. `GET /api/social-workers/`

The Social Worker field, Records' filter and the Transfer drawer all read the
list of social workers from `GET /users/` today. That is the user directory.
It is IT's and stays `IsAdministrator`. So the Officer needs a narrower door,
as `/api/psychologists/` already is for the psychologist picker.

For `SEES_EVERY_RECORD` (review: Records' filter needs the names, and it is
a *sees every record* door; the Social Worker field and Transfer, which are
`OVERSEES_CASEWORK`, read the same list). Staff accounts that are active, and
any account that still holds an active record: `[{id, name, active, records,
may_hold}]`, `records` being the count of active records held and `may_hold`
whether it can be given more (default a: a re-roled holder is listed so its
records can be moved, never as a target). No email, no status beyond
`active`. The ISA's screens move to it too, so the ISA and the Officer cannot
be shown different lists.

Moving the screens off `/users/` changes every field they read (review):
`role_name` and `status` give way to `may_hold` and `active`, and the name
to `name`. Records' picker and filter keep showing active social workers
only, as now (`Children.jsx:217`-`220`); the filter also lists a holder that
still holds active records, marked "(inactive)" (default l). The Transfer
drawer keeps counting from the `/children/` list it ticks, as CLAUDE.md
requires, and never shows `records`.

### 2. `GET /api/casework/overview/` and the casework desk

`accounts/admin_overview.py` already builds the four records queues and the
workload as separate functions (`_stuck_requests`, `_no_social_worker`,
`_archived_holder`, `_duplicates`, `_workload`). On `cloud-setup` the second
and third share one counting query, passed in as `tally`, so the new view
builds that tally too. A second view serves only
those to `OVERSEES_CASEWORK`: `{generated_at, needs, workload}`. It never
carries people and sign-ins, health, the version, or the security log. A test
plants a sentinel in each of those and searches the whole response, as
`test_admin_overview.py` does.

Three things the console's functions do that this view must not (review):

- **Queues are picked by key**, `stuck_requests`, `no_social_worker`,
  `archived_holder`, `duplicates`, never by `group`: `names_to_fix` carries
  the group "records" but names accounts and links to `/users?edit=`.
- **People are named with `accounts.display.display_name` or
  `entered_name`, never the console's `_person`**, which falls back to the
  email address on purpose because its answer is the administrator's alone
  (`admin_overview.py:115`-`119`, and `_short` through it). The sentinel
  test adds an account with no name and asserts its address appears nowhere.
- **`_workload` takes the tally too** (`no_psychologist`), not only the
  second and third queues. The view's query count is pinned in
  `clinical/tests/test_endpoint_query_counts.py`, as the console's is.

On screen it is `pages/dashboard/CaseworkDesk.jsx`, mounted for the Officer
alone, with the queue tiles and Workload from the admin console and one
button, **Transfer records**. The Transfer drawer moves out of
`ConsoleActions.jsx` into its own file so both consoles use the one drawer.
Explicitly routed paths, both: the deploy probe in CLAUDE.md can tell them
from a 404.

Lifting the tiles out is a refactor, not a move (review). `Tile`,
`QueuePanel`, `NeedsYouNow` and `Workload` are private to `AdminConsole.jsx`.
`NeedsYouNow` hard-codes the ISA's two groups (`:178`-`181`), always draws
`ConsoleActions` as its footer (`:410`-`412`) and takes the assistant's
health and the access count. Workload's "No social worker yet" row is read
from `needs`, not from `workload` (`:1103`-`1104`). So: the shared parts go to
`pages/dashboard/consoleParts.jsx`; `NeedsYouNow` takes `groups` and `footer`;
the overview's load and sequence logic (`:1053`-`1093`) becomes one hook. The
desk has one group of four tiles: the four queues above, with `needs`
carrying `no_social_worker` so Workload's row works. The drawer leaves with
its success path (`moved()`, `KeepRefs`, `NAMED`, `num`, `plural`, now in
`ConsoleActions.jsx:536`-`560`) as `TransferDrawer({onClose, onMoved})` and a
shared `useTransferDone`. **Done means the ISA's console is pixel-identical
at 1440 and 1100, before and after.**

### 3. `may_close_case`

Described above. `termination.reasons_for` and `refusal` name their roles:
the psychologist's list, `OVERSEES_CASEWORK`'s list, and nothing for anyone
else.

### 4. A promotion must not strand a caseload

The likeliest first Officer is a senior social worker whose Staff account
holds twenty children. Change that account's role and the records are held by
an account that is no longer a social worker: Transfer refuses it as a source
("Choose a social worker (SW) account"), and no queue lists it. The same hole
exists today for Staff changed to Psychologist.

`UserWriteSerializer.validate_role` refuses a change **away from Staff** while
the account holds active records: "This account still holds 20 active records.
Move them first (Transfer records)." That includes Staff to Psychologist,
which the ISA can do today, as a deliberate change (default b). The count is
`Child.objects.filter(social_worker=instance, status=ACTIVE)`, imported inside
the function (`accounts/scoping.py` says why). The account's row is locked
(`select_for_update`) there and in Transfer's lookup of its *to* end, so a
transfer racing the role change cannot slip a record past it (review).

**Closed records do not count, and that leaves a way round it** (review).
Reopening in place keeps the holder, for the ISA and for an Officer, so a
promoted worker's closed case can come back active and theirs. Only a social
worker reopening at intake takes it over. So the queue "held by an archived
account" becomes "held by an account that cannot hold records" (inactive, or
not in `HOLDS_RECORDS`), and Transfer's *from* end accepts any account that
holds active records (default a). A record stranded this way, or by a Staff
to Psychologist change made before this build, is then listed and can be
moved. WP2 tests it: promote, reopen one of their closed records as the ISA,
and the record is in the queue and moves.

The psychologist's side of the same hole (a psychologist changed to another
role while children are assigned) is not touched here. There is no bulk way to
reassign a psychologist's children, so a refusal would have no way out.

### 5. `status` is not a form field

Found while reading, and it existed at `ab447f2`: `Child.status` is a writable field
on the record's serializer (`children/serializers.py:82`, not in
`read_only_fields` at `:99`), and an edit drops it only for a psychologist
(`:360`). No screen sends it. **Measured on 17 Nov 2026:**

- A social worker's `PATCH {"status": "inactive"}` on their own active record
  answered 200. The record was stored inactive, its `case_status` still said
  `pre_assessment`, and no `TerminationRecord` was written: a case closed with
  no reason, no note and nobody named, by an account that may not close cases.
  The ISA's did the same.
- `POST /api/children/` with `"status": "inactive"` answered 201 and stored a
  record that was closed from the start.
- ~~The way back is not open~~ (review): it is. `get_queryset` keeps closed
  records out of an edit only when `?include_archived=true` is absent
  (`children/views.py:184`-`188`), so `PATCH
  /api/children/<id>/?include_archived=true {"status": "active"}` answers 200
  and reopens a case outside `reopen()`, with `case_status` still
  "terminated" and no reopen line. Both directions are open.

It becomes read-only for every role, on create and on edit: a case moves
through `terminate` and `reopen` only. Its own commit, with a test written
first that shows the hole. The Officer would otherwise inherit it over every
record in the agency.

**To be closed by the lifecycle branch** (`claude/amazing-fermat-8hity4`,
"Give each status lifecycle one door", 17 Nov 2026), which does more than this
section asked. It is not merged yet (see "Before WP0"), so until it lands the
hole is open on this branch and on `cloud-setup`, as it always was. `config/lifecycle.py` declares each status as a table of legal
moves, for children, users and appointments alike. An edit that sends a
different status is refused with a sentence naming the right door, while the
record's own status sent back is accepted (the record form does that). A create
that sends anything but "active" is refused ("A new record is always open."),
and Terminate and Reopen are conditional updates that answer 409 to a lost race.

This branch had its own fix, commit `56bd9dc`, which made `status`
read-only. **It is reverted** (19 Nov 2026), because the two cannot be merged
together. They merge with no text conflict, but read-only drops `status`
before the lifecycle's check can see it, so the lifecycle's refusals never
fire and its tests fail. WP1 no longer carries this item.

**WP3 waits for the lifecycle** (review). WP3 is where the Officer may save
any record, and with this hole open that means the whole agency's statuses,
both ways. If the lifecycle is still out when WP2 is done, the planner decides
then between waiting and building WP3 with its Officer status test marked as
an expected failure until the merge. Either way the lifecycle merge's
acceptance check tries both directions as a social worker, the ISA and an
Officer.

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
  is every Child and Assignment line on their records, whoever wrote it
  (`activity/views.py:74`; it still reads the CaseStudy lines written before
  the removal).
- An Officer **files a referral or starts a survey** on someone's record:
  that worker's bell does not show it. Those lines are keyed by the
  referral's or the invite's own id, not the child's, so a social worker's
  feed cannot place them. It is the same today when the ISA does it, and it
  is left as it is; see "Not done, suggested".
- In the Officer's own bell the second copy of a transfer's sentence
  (`move_copy`) is left out, as it is for the ISA.
- **Wording** (review): the `case_request` switch's description, "When a
  social worker or the ISA asks you", becomes "a social worker, the RACC
  Officer or the ISA" (`accounts/email_notifications.py:76`-`78`); the "No
  social worker yet" queue's hint, "Only you can open them", becomes "Only
  the ISA and the RACC Officer can open them" (`admin_overview.py:406`-`407`).
- **The email limits** count by actor (`accounts/email_notifications.py:516`-`560`).
  An Officer acting on every record meets them sooner than one social
  worker. They stay as they are (default e).

`KINDS` gets no Officer entries, so My Profile shows the Officer no email
switches, and `wants()` already refuses a role the kind is not for.

## The screens

The server decides; the screen only offers. But a button the server refuses,
or a screen that falls into another role's branch, is a defect.

- **`config/roles.js`, `config/nav.js`, `App.jsx`**: the lists; the Officer's
  rail and tabs are the social worker's (Dashboard, Records, Monitor,
  Calendar, Reports, Summary), plus `/activity` and `/profile`. Never
  `/users`, `/settings`, `/instruments`, `/pre-assessment`.
  From WP1b `nav.js` reads the named lists, so the rail follows the lists the
  Officer joins in WP3 and WP4: check that they open what this bullet says
  and nothing else.
- **`ui/index.jsx`**: `ROLE_META.Officer` (with `short`, `color` and `tone`),
  `ROLE_ACCESS.Officer`, and every other table keyed by role, which neither
  the guard nor the pin can see (review): `RoleBadge`'s own `ROLES` map
  (`ui/index.jsx:558`-`562`, which falls back to Staff's amber), the
  console's `ROLE_CHIP` (`AdminConsole.jsx:701`-`705`), the avatar tones
  (`Users.jsx:504`, `ConsoleActions.jsx:171`, `AdminConsole.jsx:658`) and the
  audit trail's `SUBTITLE` (`AuditTrail.jsx:36`-`40`). They read one source,
  `ROLE_META`, and a check fails on a role-keyed table that lacks a name in
  `EVERY_ROLE`. The dot is a new violet token (default i). The icon is one
  already in `ui/icons.js`, or lint's `check-icons.mjs` fails. The long form
  goes in the role select's option text and as the first `ROLE_ACCESS` line,
  since Users and Access Requests show those bullets, not a description.
  Staff's bullet strings are reused word for word where the access is the
  same, because `RoleAccessPanel` works out Gains and Loses by string
  equality (review).
- **The header**: the identity chip is sized against "ISA (Administrator)"
  with 30px in hand (CLAUDE.md, "The header search"). The new label is six
  characters longer: about 40px in the chip's uppercase 9.5px with its
  tracking. Its line has no `white-space: nowrap`, so it would wrap inside
  the 38px pill rather than overflow (`AppHeader.jsx:545`; review). So the
  chip reads `ROLE_META.Officer.short`, "RACC Officer" (default j), with
  `nowrap`, and everything else keeps `roleLabel`. Measure the chip's height
  as well as its width, and that no tab sits under the search bar, at 1280,
  1366 and 1440.
- **Dashboard**: the Officer gets the agency layout the ISA has, the
  "Clinical team this range" card included (default k), with `CaseworkDesk`
  where `AdminConsole` is. Quick actions are the social worker's three.
- **Records** (`pages/Children.jsx`): `isAdmin` there means three things and
  is split: *sees every record* (the Social worker column, filter, "No social
  worker yet", the export's column); *oversees casework* (the Social Worker
  field's list, Terminate, Remove duplicate); and the ISA alone (nothing on
  this screen). `canManage` becomes `KEEPS_RECORDS`.
- **The record drawer and form**: the "Next step" line uses `worksRecord`;
  "Ask the ISA (Administrator) to transfer it to you" becomes "Ask the RACC
  Officer or the ISA". Four more sentences become untrue (review) and read
  "..., the RACC Officer or the ISA": the Social Worker field's hint
  (`ChildForm.jsx:1045`), `Children.jsx:812`, `CustodianFields.jsx:52` and
  `Schedule.jsx:1652`.
- **The child's page**: referral upload and the referral summary follow
  `KEEPS_RECORDS` (`ChildProgressReport.jsx:165`, `:229`, `:1149`). **The
  survey does not** (review; as first written this broke psychologists): its
  forms are fetched for psychologists too (`:169`, `canStartSurvey`), so that
  fetch reads `EVERY_ROLE`, the survey card reads `KEEPS_RECORDS` or
  `canWrite` (`:1101`, `:1115`), and the no-form toast asks `SOCIAL_WORK`,
  not `=== 'Staff'` (`:277`), so the Officer is told what a social worker is
  told: ask the ISA to add one. The Assistant log tab stays the ISA's; a
  report's summary and its check (`canAdvance`) stay with the ISA and the
  assigned psychologist.
- **Results & Reports** (`pages/Report.jsx`): "Upload case referral" follows
  `KEEPS_RECORDS`. Reviewing a report's check stays the psychologist's and
  the ISA's.
- **Care gaps** (`config/careGaps.js:36`): the rule that sends a
  psychologist-only gap to the child's page instead of `/pre-assessment`
  asks "is this the ISA". It becomes "anyone but a psychologist", so no role
  is ever sent to a screen it cannot open.
- **Calendar**: `canBook` includes the Officer, and so does the booking
  drawer's psychologist picker (`psychologistPicker.js:31`, the backend's
  `KEEPS_RECORDS`). Add availability, record leave, completed and no-show
  stay as they are.
- **Header search**: the social worker's actions, plus "No social worker yet"
  under Needs you. The colleague search reads `/users/` and stays the ISA's.
- **My Profile**: the personal column only, as the ISA, because the
  right-hand lists would be the whole agency's; the email card is hidden for a
  role with no kinds, not for "the ISA".
- **Audit trail and bell**: the Officer's sentence ("Every record event in the
  agency") and the RECORD tab only. The bell's tabs are a ternary that gives
  any role but the ISA and Staff the psychologist's "All" alone
  (`AppHeader.jsx:285`; review). The Officer gets All and Records.
- **Users and Access Requests**: both read `/api/roles/`, so the role appears
  in their lists by itself. They need its label, its `ROLE_ACCESS` lines and
  its tone. Granting it at Access Requests warns, as granting Psychologist
  does (`AccessRequests.jsx:293`): "This account will see every child's
  record in the agency" (default m). Sign-up and the Google button keep
  offering two roles.
- **The ISA's console**: the people card counts Officers, as "RACC Officer"
  and "RACC Officers" (`ROLE_CHIP`); nothing else.

Check each of the four roles in a browser at 1440, 1280 and 390, and run
axe-core on every screen that changed. Until the demo seeds an Officer (WP3),
the local one is made in Users: a Staff account holding nothing, changed to
Officer. `worksRecord` has no JavaScript test runner to pin it (review), so
the browser pass checks it: an Officer on another worker's record, a social
worker on their own and on someone else's.

## What stays exactly as it is

- A social worker still sees only their own records. A psychologist still
  sees only their assigned children, under the carry-history control.
- The ISA loses nothing and gains nothing.
- Only acceptance writes `assigned_psychologist`.
- Once the lifecycle branch is merged, a status changes only through its
  lifecycle door (Terminate, Reopen, the appointment actions, the account
  actions), for the Officer as for everyone. Until then it does not (section
  5), which is why WP3 waits for it.
- An Officer account is never `is_staff` or a superuser. Django admin is the
  ISA's.
- No prompt, tool description or `CHAT_SYSTEM` byte changes, so nothing needs
  a new `ai_eval` run. The Officer's "what you can ask" sentence and starter
  questions are server-side tables (`assistant/tools.py:477`, `:517`), and a
  starter runs without the model (`/assistant/followup/`). `CHAT_SYSTEM`
  still says the user is one of three roles, on purpose (default h).
- No stored key is renamed: not a role, not an email kind.

## Handing it over

The owner's answers are in. A build session starts with three steps, in this
order:

1. **Before WP0** (below): land the case study removal in `cloud-setup`, and
   the lifecycle work once its session is done. The removal is in; the
   lifecycle is still out, and WP3 waits for it (section 5).
2. Have a reader who did not write this note try to break it against that
   code, one area at a time: accounts, clinical and scheduling; children and
   the assistant; the frontend. Done on 22 Nov 2026 (the last section).
3. Then WP0. Done (`ae37c40`).

### Before WP0: what landed (19 Nov 2026)

- **The removal: merged.** `5618af3` merges it into `cloud-setup`, with
  `children/0037_merge_case_study_drop_and_renames` joining the two migration
  heads. 3,614 tests pass, and the demo's migrate was rehearsed on a database
  holding 13 case studies (they are dropped cleanly). `b6940c3` brings it into
  the Officer branch. The push of `cloud-setup` itself is the owner's: the
  session's permission classifier refused it.
- **The lifecycle: held back.** Another session pushed `e6beb21` to it at
  15:01 UTC on 19 Nov ("Bring pre-assessments, survey links and summaries
  onto one door"), so it is still being worked on. That commit also collides
  with `cloud-setup`'s pre-assessment step tracker (`ce961ab`, `8a57e6e`,
  `99e26c6`) on behaviour, not text: the wizard's step numbering (Review 4.5
  and Done 5 there, Review 5 and Done 6 here) and how `complete()` records
  the last step (the lifecycle's `move()` against `step_times.finished`).
  That is for that session or the owner to settle. WP0 to WP2 do not wait
  for it; WP3 does (section 5). When it lands, the Officer branch merges `cloud-setup` again, and
  this note's rows for surveys and summaries are read again, since `e6beb21`
  changes those doors.

The rest of this section is what was found before the merges, kept for
whoever lands the lifecycle.

The Officer is built on code without the case study, so two of the owner's
branches go into `cloud-setup` first. Neither is merged on 19 Nov 2026:

- `claude/bold-carson-4oq44s`: removes the case study (`case_study` app,
  `children/0035_drop_case_study`), then the PRC license and Head of Office
  fields (`accounts/0019_remove_license_and_head_of_office`).
- `claude/amazing-fermat-8hity4`: one door for every status
  (`config/lifecycle.py`; see section 5 above).

Both branch from `ab447f2`, so they do not contain the twenty-odd commits
`cloud-setup` has gained since. Previewed on 19 Nov with `git merge-tree`,
merging them into `cloud-setup` meets:

- **Two migration heads in `children`.** `cloud-setup` added
  `0035_rename_without_known_parents_foundling` and
  `0036_rename_birth_statuses_child_suffix`, and the removal adds its own
  `0035_drop_case_study`, all after `0034`. Django refuses two leaves, so the
  merge needs a merge migration (`manage.py makemigrations children --merge`),
  then `makemigrations --check --dry-run` printing nothing.
- **The removal against `cloud-setup`**: CLAUDE.md, `frontend/src/utils/time.js`,
  and nine case-study files that `cloud-setup` edited and the removal
  deletes (`case_study/sections.py`, `demo_case_studies.py`, five of its tests,
  `components/caseStudy/model.js`, `config/scsr.js`). The deletion wins. Then
  search the merged tree for `case_study`, `caseStudy`, `scsr` and
  `CaseStudy` in anything `cloud-setup` added since `ab447f2`.
- **The lifecycle against `cloud-setup`**: `accounts/views.py` and
  `frontend/src/pages/Users.jsx`, where `cloud-setup`'s user-management fixes
  and the lifecycle's account moves touch the same code. Keep both: the
  lifecycle's moves, and the fixes' messages and checks.
- Then the full suite, lint and build on the result.

Pushing `cloud-setup` redeploys the demo, so the owner says when. The Officer's
own migration is then `accounts/0020`, after the removal's `0019`.

## Work packages

In order. Each is one worker's job with its own commits, and the planner reads
what comes back before the next starts. Both checks before every commit:

```
cd backend && .venv/bin/python manage.py test --parallel 4 > ../tests.log 2>&1; echo $?
cd frontend && npm run lint && npm run build
```

(Linux paths; on the owner's PC, `.venv\Scripts\python.exe`. Never pipe a test
run through `tail`.)

### WP0. Baseline, and a table of every route (done, `ae37c40`, 22 Nov 2026)

- `backend/requirements-dev.txt`: `tblib`. Without it a failure inside a
  parallel worker can crash the runner with "cannot pickle 'traceback' object"
  and the failing test's name is lost. That happened once while this plan was
  written, and not since.
- `backend/config/tests/test_every_route_is_decided.py` and
  `route_table.json` beside it. The test walks Django's URL resolver for
  everything under `/api/` (210 route-methods) and asks each as: nobody, the
  ISA, a social worker on their own child, a social worker on someone else's,
  the assigned psychologist, an account with no role, and an account whose
  role is a row called "Officer" **made in the test database only**. The
  tokens are real, minted as sign-in mints them. Writes go with an empty body
  inside a savepoint that is rolled back; the clock is pinned, the cache
  cleared before each request, and texts, email, the model runtime and the
  brief prefetch thread replaced.
- The words: *past* (2xx: the request did something), *invalid* (400, 409,
  415, 422: the gate let it through and the handler turned the empty body
  down, which says nothing about a real body), *refused* (401/403), *hidden*
  (404), *verb* (405), *unavailable* (503). A `past` list adds `:rows` or
  `:empty`. Any other 5xx fails the test as a bug. The table covers
  route-level gates only; a door that depends on the body (the Social Worker
  field, the custodian, the edit's ask branch, closure reasons, transfer
  ends, the chatbot's tools) is pinned by unit tests in the apps' own files.
- **A route or verb that is not in the table fails the test** with "decide
  what every role gets here", and a changed cell fails naming both words.
  That is this codebase's guard for the twelfth call site, like
  `STAFF_DRAFTING` and `KEEPS_IT`.
- Done: the suite is green (3,622 tests), and the comparison also passes with
  the wall clock moved to Sat 23:58, Sun 00:02, Wed 09:30 and a day seven
  months on. The unknown-role column was compared with the table at the top
  of this note, which it bears out ("Measured again on 22 Nov 2026").

### WP1. Name the capabilities, close the `else` branches

No behaviour changes for the three roles, and the lists do **not** contain
the Officer yet. In two halves (planner, 22 Nov 2026): the backend, WP1a,
then the browser, WP1b, whose brief is written from the frontend review.

**WP1a, the backend** (done, `c7eae2c`, 23 Nov 2026). Built as below, then
read by three reviewers, fixed and read again. Three things changed on the
way, by the planner: recording a session's outcome reads its own list,
`RECORDS_OWN_SESSION_OUTCOMES` (the session's own psychologist, whatever
their role is now, but never an Officer); the Google door's two claim sites
read `CLAIMABLE_AT_SIGNUP` and only its last sign-in check
`SIGNS_IN_WITH_GOOGLE` (the first mapping had all three on the sign-in list,
which from WP3 would have let a stranger ask for Officer through Google, and
no reviewer caught it); and an unknown role refused at sign-in counts toward
the lockout like any refused sign-in. 3,699 tests pass, also with the clock
on a Sunday; the route table's six known columns did not move by one cell.

- New `accounts/capabilities.py` with the lists and the three functions; the
  pin test and the second-list guard (`accounts/tests/test_capabilities.py`).
- Every enumerated tuple becomes a named list, in: `accounts/scoping.py`,
  `permissions.py`, `serializers.py`, `google_auth.py`, `views.py`,
  `admin_overview.py`, `sms_notifications.py`; `children/views.py`,
  `serializers.py`, `custodian.py`, `termination.py`, `assignment_api.py`,
  `transfer.py`; `clinical/views.py`, `reports_views.py`, `serializers.py`,
  `care_gaps.py`; `scheduling/views.py`, `visibility.py`;
  `assistant/views.py`, `tools.py`, `brief_facts.py`,
  `management/commands/ai_eval.py`; `activity/views.py`.
- The four "must be a Staff account" checks become `may_hold_records`, and
  the console's Workload reads `HOLDS_RECORDS`.
- Every `else` that meant "the Administrator" names its role, and what is
  left over is refused or sees nothing: the doors in the first table of this
  note, and the five behind a gate. **A branch that serves the ISA today is
  written with the list whose question it answers** (review), so adding the
  Officer to that list later reaches it: the Dashboard's agency branch and
  the request list read `SEES_EVERY_RECORD`, the appointments list
  `KEEPS_RECORDS`, the closure reasons' ISA branch `OVERSEES_CASEWORK`. Add
  Record is a `KEEPS_RECORDS` door, at its gate and at its record keeper's
  branch, while naming the record's social worker stays the serializer's
  `OVERSEES_CASEWORK` rule (planner, 23 Nov: the first brief said
  `OVERSEES_CASEWORK` for both, which would have refused an Officer's Add
  Record from WP3 to WP4, against the Records table). The ISA's full audit
  stream stays `== Role.ADMINISTRATOR`; WP3 gives the Officer its own
  branch.
- Two elses are kept on purpose, each with a comment saying so (review):
  `brief_kind` (any non-psychologist means "case", pinned by
  `test_case_brief.py`), and the assistant's `_CAN_ASK` and `_EXAMPLES`
  defaults until WP3 adds the Officer's entries.
- `may_close_case` replaces `is_admin_or_assignee` in `terminate` and
  `closure_reasons` only.
- **The backstop** ("The door they all come through"): an unknown role is
  refused at authentication, sign-in and refresh. `Role` becomes read-only
  in Django admin.
- **The route table gains an eighth account, the probe**: a role row that
  the test patches into `EVERY_ROLE` and nothing else. With the backstop, the
  unknown-role column can only say "refused"; the probe's column is what an
  `else` that grants by default would show, now and for every door added
  later.
- ~~`status` read-only on create and on edit~~: done by the lifecycle branch,
  see section 5. The lifecycle's own tables (`config/lifecycle.py` and the
  three apps' `lifecycle.py`) are role-blind and stay so; where a door they
  guard also checks a role (Staff cancel only their own children's scheduled
  sessions), that check is the one that becomes a named list.
- The availability and leave viewsets ask for `EVERY_ROLE` on every action,
  slots and the month included, not merely for a sign-in (review).
- **Done when:** every existing test passes unedited (imports for the
  renamed permission class and the moved instrument list aside); the route
  table's six known columns have not moved by one cell; the unknown-role
  column is *refused* at every authenticated door; the probe's column is
  *refused*, *hidden* or empty except at the doors every signed-in account
  has (their own profile, password, phone and email switches, the agency's
  name, what they can ask, the address lists) and what every role reads by
  design (availability and leave); and each closed door has a unit test, as
  the probe, that fails with the fix reverted.

**WP1b, the browser** (done, `4d4416f`, 23 Nov 2026). Built as below; the
three roles' rail, screens and sign-up choices were identical in a browser
before and after. Found on the way: the API's 401 carried only `detail`, so
the sign-in page could not tell "role not set up" from a wrong password; the
unknown-role and no-role refusals now carry `code` in the body. Left for
later: `nav.js`'s Summary header tab is a single `['Staff']` (WP5 decides the
Officer's), and the role-keyed tables, boolean chains and the inline
works-record rule in `ChildDrawer.jsx` are WP5's inventory.

`config/roles.js` gains the lists under the same
names, as `export const NAME = ['...']` string literals, and `worksRecord`;
`config/tests/test_cross_stack_roles.py` is extended to pin all of them (its
`valid` set read from the capabilities); `nav.js`, `App.jsx` and the seven
screens with role arrays swap to the named lists ("The browser's copy");
the frontend half of the guard. `INSTRUMENT_MANAGER_ROLES` follows the
backend's new name. **Done when:** lint and build pass, the pin test passes,
and each changed screen loads for the three roles in a browser with no
console error and its content drawn.

### WP2. The role exists

**Done (25 Nov 2026).** Built as below, reviewed two ways (no blocker, no
major), fixed and read again; 3,739 tests pass and the route table did not
move. The migration was rehearsed on a database with accounts: it adds the
Officer's row and touches nothing else. Decided by the planner on the way:

- **WP2 never deploys without WP3.** From WP2 the ISA can grant the Officer
  in Users and Access Requests, and until WP3 such an account is refused at
  every door (a Google applicant would be emailed that access was approved).
  Harmless locally; on the demo it would be a role that does nothing.
- **Workload** still lists social workers by role, so a promoted account
  that holds a reopened record is in the queue but not in the Workload
  column (the total is not short). WP4's casework desk lists every holder.
- **The empty queue's hint** still says "deactivated account"; WP5's wording
  pass renames it. The tile reads "Held by an unavailable account".
- **Only Transfer locks the account it gives records to.** The Social Worker
  field, a worker's own new record and a reopen at intake do not, so a
  record could reach an account in the instant its role changes. Accepted:
  the queue lists it and Transfer moves it.


- **From WP1** (23 Nov): `seed_initial_data` and the console's people counts
  read `EVERY_ROLE` today and move to `Role.NAMES`; the pin tests
  (`test_cross_stack_roles.py`, `test_capabilities.py`) take their valid
  role names from `Role.NAMES`, since a role row can exist before it is in
  `EVERY_ROLE` (the Officer, from here to WP3); and the role-assigning doors
  (`RoleListView`, approve, `UserWriteSerializer.role`) accept only a role
  in `Role.NAMES`.
- `Role.OFFICER`, and `Role.NAMES`, the rows that must exist;
  `accounts/migrations/0020_officer_role.py` (after the removal's `0019`), a
  data step that creates **only the Officer's** row if it is missing
  (review: about 255 tests make the other three with `Role.objects.create`,
  which a migrated row would break) and whose reverse does nothing (an
  account may hold it); `seed_initial_data` seeds from `Role.NAMES`. That is
  not `EVERY_ROLE`: one says which rows exist, the other who may read case
  files, and the Officer's row exists a work package before the Officer may
  read anything. Tests always fetch the Officer's row with `get_or_create`,
  and "the row is there after migrate" runs in a `TestCase`, since a
  `TransactionTestCase` flush removes it.
- `CLAIMABLE_AT_SIGNUP` stays Psychologist and Staff, and a test says
  "Officer" cannot be claimed. `SIGNS_IN_WITH_GOOGLE` gains the Officer in
  WP3, with the other lists: until then the backstop refuses an Officer at
  every door, so a Google sign-in would only be turned away. The Google
  door's last check (`google_auth.py:218`) reads `SIGNS_IN_WITH_GOOGLE`, not
  the claimable list: a Google-only account has no password, so a promoted
  one would otherwise be locked out for good.
- The role-change refusal for a Staff account holding active records
  (section 4: every change away from Staff, with the row locked); the queue
  that lists a holder who cannot hold records, and Transfer's *from* end
  (default a). `_role_words` gains the Officer (default d), and
  `test_cross_stack_roles.py`'s `valid` set gains it.
- The ISA console's people counts
  (`accounts/tests/test_admin_overview.py:82` lists the three roles and gains
  the fourth).
- **Done when,** by test: the row is there after `migrate` on a database that
  already has accounts; "Officer" cannot be claimed at either sign-up door
  and the refusal reads like the Administrator's; the ISA can grant it at
  approval and in Add user; Staff to Officer (and to Psychologist) is
  refused with the count while records are held and passes once they are
  moved; a promoted worker's reopened record is in the queue and moves; and
  an Officer's sign-in and every authenticated door answer *unknown_role*
  until WP3 (the backstop), as the route table's Officer column shows.

### WP3. Every record, and a social worker's work on any of them

**Done (`afa0935`, 26 Nov 2026), on the owner's word without the lifecycle.**
The owner asked for WP3 before the lifecycle branch landed, so the status
hole is closed for the Officer alone: an Officer's edit whose status differs
from the record's, and an Officer's create with any status but "active",
are refused with the lifecycle's own sentences. The other roles keep the
hole until the lifecycle merges. **At that merge,**
`children/tests/test_officer_records.py`
`test_the_other_roles_are_not_changed_by_it` fails by design: flip its 200
and 201 assertions to 400, or delete it. Also decided: an explicit
`"social_worker": null` on Add Record passes for `KEEPS_RECORDS` (it means
the same as leaving the key out), and `works_record` reads a new list,
`WORKS_ANY_RECORD` (the Officer). Reviewed three ways (no blocker, no major),
fixed, read again; 3,827 tests pass. In the route table only the Officer's
column moved (103 cells, all from *refused*), and at no door is it open
where a social worker on their own child is not. In a browser the demo
Officer (`c.dizon@racco1.gov.ph`) drew every screen with no error; the
wording on them is still the social worker's or the ISA's (WP5).

Left for WP5: every role's "what you can ask" sentence still offers a
breakdown by "stage", removed on 2 Nov (all four drop it together); the
duplicate check calls every match "yours" for the Officer and the ISA; and
`test_a_pending_child_is_absent_from_every_door` asserts a 404 on
`/api/appointments/next-slots/`, a route that does not exist (the Officer's
copy uses the real one).


- **Starts once the lifecycle branch is merged**, or on the planner's word
  (section 5).
- `EVERY_ROLE`, `SEES_EVERY_RECORD`, `KEEPS_RECORDS`, `SOCIAL_WORK` and
  `SIGNS_IN_WITH_GOOGLE` gain the Officer, in `capabilities.py` and
  `roles.js` alike, which also lets it past the backstop. `works_record`
  goes in at the survey rule (`clinical/views.py:660`) and next steps
  (`assistant/brief_facts.py:179`). `shows_name`. The Officer's branch of the
  activity scope. The Officer's "what you can ask" and starters, and each
  tool's answer as in the assistant's table. Record polish. The export log.
  `seed_demo_data` gains one Officer account holding nothing (moved here from
  WP6, review: WP3 to WP5 need one).
- With those lists come (review): reopen, check-duplicate, the request list,
  withdraw, who was asked and a decline's reason, and the booking drawer's
  psychologist picker.
- **To decide here** (WP1a): an Officer's Add Record that sends
  `"social_worker": null` is refused (400) until WP4, because the serializer
  lets only `OVERSEES_CASEWORK` (or a social worker's own new record) send the
  key at all. The record form never sends it, so the screen is not affected;
  an API client is. Decide whether a null should pass for `KEEPS_RECORDS`.
- **Done when,** by test, an Officer: signs in by password and by Google;
  sees another worker's child and an unheld one at every door
  `children/tests/test_own_records.py` closes to a social worker; adds a
  record without a social worker and it belongs to nobody; edits a record
  and the edit **asks** a psychologist (extend
  `test_a_pending_child_is_absent_from_every_door`), and withdraws a request
  on another worker's record; reopens with the holder unchanged; records a
  custodian; files and deletes a referral; starts a survey; books, moves and
  cancels, with the psychologist picker answering, and is refused completed
  and no-show; sees names on the calendar and the agency's sessions and
  availability on the Dashboard; gets the social worker's care gaps over all
  records; is refused every clinical write, the written brief and remark
  polish; gets `brief: "case"`; has no USER or SECURITY line in the feed or
  the trail, with `?category=security` tried (a sentinel line of each kind
  is planted); and cannot change a record's status by an edit, either way.
  `STAFF_DRAFTING`'s test runs as an Officer too, on a child another worker
  holds. Each test that loops over exactly three roles takes the Officer
  (review): in assistant `test_chat_panel.py` (`StarterQuestionsTest`),
  `test_role_access.py`, `test_followups.py`, `test_case_brief.py`,
  `test_record_polish.py`, `test_staff_drafting.py`; in children
  `test_answer_email.py`, `test_assignment_requests.py`, `test_transfer.py`;
  and `ai_eval`'s NOT MEASURED loop. The route table's Officer column is
  rewritten, and each cell that moved is read against this note's tables.

### WP4. Oversight: who holds a record, how a case ends

- `OVERSEES_CASEWORK` gains the Officer: the Social Worker field, Transfer,
  Remove duplicate, `may_close_case`. `GET /api/social-workers/` and
  `GET /api/casework/overview/`, with section 2's three rules.
- The sentences that send people to the ISA name the Officer first:
  `children/duplicates.py:166`-`168`, `children/views.py:258`, `:299`,
  `:337`, `:374`, `:398`, `:470`, `children/serializers.py:138`,
  `children/assignment_api.py:163`-`164`, `children/custodian.py:91` (review:
  four were missing), and the no-social-worker queue's hint. `_ASKER_ROLE`,
  the fallback name, and the `case_request` and `records_transferred`
  switches' descriptions in `email_notifications.py`.
- **Done when,** by test, an Officer: sets and changes a record's social
  worker and is refused a non-Staff target; transfers in bulk and is refused
  as either end; closes with a case outcome and is refused a clinical reason,
  with every reason offered accepted; removes a duplicate; reads the casework
  overview, which the sentinel test shows carries nothing of IT and no email
  address; and is refused `/api/admin/overview/`,
  `/users/`, `/roles/`, both test senders, assistant settings and the rest of
  `IsAdministrator`. A social worker is still refused all of WP4.

(There was a WP5 for the Officer's reading of a case study. It went with the
feature, and the packages after it moved up by one.)

### WP5. The screens

Everything under "The screens", then the browser pass for all four roles.
Every role-keyed table has its Officer entry (a check fails on a missing
one), and the Officer's icon is in `ui/icons.js`.
**Done when** lint and build pass, each changed screen has been loaded as
each role at three widths, axe-core is clean on them, the header fits (the
chip's height measured, not only its width), and the ISA's console is
pixel-identical at 1440 and 1100 to before the casework desk was lifted out
of it.

### WP6. Demo account, documents

- The demo Officer arrives in WP3 (above). Here: `demo_owners.active_staff`
  already deals only to Staff; a test says an Officer present on the branch
  is never dealt a child.
- `ai_eval`'s callers include the Officer, so the owner's next run says
  "NOT MEASURED" rather than nothing.
- `docs/LOCAL-SETUP.md` (the sign-in), the access matrix in
  `docs/CLOUD-DEPLOYMENT.md`, and CLAUDE.md: a section for the role, and the
  lines in "Each social worker's own records", "Closing a case", "Names on
  the schedule", "Role names on screen", "Getting an account" and the test
  count that this changes.

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
branches as the ISA. So WP1 ships and is verified as deployed, on both
`nacc-v3-demo-api` and `nacc-v3-demo-web` (they deploy independently;
review), **before** any push that carries the migration. Two pushes, not
one. The migration itself is additive and safe for a release still serving.

**WP2 and WP3 go together** (25 Nov): WP2 alone lets the ISA grant a role
that reaches nothing.

**And the Officer does not reach `cloud-setup` before the lifecycle does.**
Until the lifecycle lands, a record's `status` can be set by an ordinary save
(section 5), and an Officer can save any record, so shipping the Officer first
would hand that hole to one more account over the whole agency.

## Tests, in one place

- `accounts/tests/test_capabilities.py`: the lists, the JavaScript mirror, the
  second-list guard, the migration's row.
- `config/tests/test_every_route_is_decided.py`: the table, WP0 onward.
- An Officer's cases in the files that already pin each rule: children
  (`test_own_records`, `test_transfer`, `test_reopen`,
  `test_reopen_at_intake`, `test_termination`, `test_closure_reasons`,
  `test_remove_duplicate`, `test_assignment_requests`, `test_custodian`,
  `test_child_collab`, `test_one_record_per_child`, `test_api`,
  `test_demo_transfer`, and the lifecycle branch's `test_case_lifecycle`),
  and their counterparts in clinical, scheduling (with
  `test_appointment_lifecycle`), assistant, activity and accounts (with
  `test_user_lifecycle`).
- Fixtures: most tests make the three roles with `Role.objects.create`, some
  with `get_or_create` (`children/tests/test_child_collab.py:10`). The
  Officer's row is always `get_or_create`, because from WP2 the migration has
  made it already (review). No test reads the wall clock.

## Not done, suggested

- **The Officer's emails.** A weekly count of records with no social worker;
  a psychologist's decline on a record nobody holds; a transfer made by the
  ISA.
- **Telling a record's social worker when somebody else files its referral
  or starts its survey.** Today those lines go to the psychologist only.
- **Taking casework away from the ISA**, now that someone else can hold it.
  `children/admin.py` also registers the child in Django admin, where the
  ISA's superuser can set a holder or a psychologist outside every rule.
- **The psychologist's side of "a promotion must not strand a caseload".**
- **An Officer who also carries cases** (decision 1's other answer).
- **The demo import rehomes an Officer's actions** onto the child's
  psychologist (`import_demo_data.rehome_people`: "anyone else falls to the
  child's psychologist"), so an Officer's closure or request in an exported
  fixture shows as the psychologist's. It is the same for the ISA today
  (review).

## What was checked for this plan, and what was not

- Read from the code on 16 Nov 2026: every role constant, permission class
  and role list in the backend, and every `role_name` branch in the frontend.
  The children and case-study apps were also mapped by an independent reader,
  whose 44 sites agreed with this note and supplied several of its traps.
- The other independent readers, and the review pass meant to try to break
  this plan, did not run before 22 Nov: the account's usage limit stopped
  them twice within minutes.
- **Reviewed, 22 Nov 2026**, by three readers who did not write it, one area
  each, against the merged code: accounts, activity, clinical and
  scheduling; children, the assistant and the work packages; the frontend.
  All three found it sound with fixes. The one blocker (gating the survey on
  `KEEPS_RECORDS` would have broken psychologists), 18 major findings and 35
  doors it had missed are written in above where they apply, marked
  "(review)"; the questions they raised are answered under "The planner's
  defaults".
- **Two clock faults, found while building WP1a** (23 Nov 2026). WP0's
  brief fixture took its date from the real clock, so one cell of the
  committed table would have failed on exactly the pinned day, which none of
  the four travel dates was. And one of WP1a's new tests would have failed
  the whole suite every Sunday, and every Monday before 08:00. Both are
  fixed. The travel runs now include the pinned day itself, a Sunday just
  after midnight and a Monday before eight.
- **Measured, 17 Nov 2026:** the doors in the first table of this note (all
  but the assistant's schedule answer) and the `status` hole, with a throwaway
  test of about forty requests as five accounts. It was not committed; WP0's
  table is the permanent version and covers every route, which the probe did
  not.
- **Read, not run:** the five else-branches that sit behind a gate today;
  everything about the frontend; every claim about what an Officer "would"
  get once a list is widened. Nothing in this plan has been executed as an
  Officer, because the role does not exist.
- **Checked again on 19 Nov 2026**, after the owner said the case study is
  removed: the diffs from `ab447f2` to `cloud-setup` and to the two unmerged
  branches, for every file this note cites. The rules the note relies on
  still hold there: an unknown role still gets the ISA's care gaps
  (`alerts_for`) and still passes the appointment move check, which asks
  Staff only. What changed is written in where it applies. The merges
  under "Before WP0" were previewed with `git merge-tree`, not made.
- Baseline at this commit, on Linux: 3,794 tests found, green in 35 s with
  `--parallel 4`; `npm run lint` 0 errors and 25 warnings; `npm run build`
  passes. An earlier run of the suite, minutes before, died without naming
  its failure (the `tblib` point in WP0), at 23:16 Manila time. Whether that
  was the clock was not established.
