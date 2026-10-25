# Working on NACC SYS V3

Notes for whoever (or whatever) picks this up. Read this before offering to push
anything — the last session burned an hour rediscovering the first two sections.

## Scope: local development only

Since 25 Aug 2026 all work happens against the **local copy** — SQLite, files on
disk, `run-local.bat`. New features are built and verified here.

Render, Neon, R2 and the Google OAuth app are live and **not being worked on**.
Do not propose changes to them, do not debug them, do not touch `render.yaml`
or `backend/entrypoint.sh` unless asked in so many words. The Infrastructure
section below is background for reading code, not a to-do list.

**Since 27 Aug 2026 work is pushed to a separate repository**, so that pushing
cannot deploy anything. See "Getting changes onto GitHub" — the short version
is that what makes a push safe is the REPOSITORY it lands in, not the remote's
name. Run `git remote -v` and read the URL; in this clone `origin` is the safe
one.

## Commit authorship

Every commit is authored **and** committed by:

```
Reynold <jreynoldcanedo@gmail.com>
```

No Claude attribution, no `Co-Authored-By`, no `Claude-Session` trailer, no
model name anywhere in a commit message, PR body, or code comment. If a hook
asks for the commits to be reauthored, decline — this rule is the owner's and
it stands.

## Two models a session, ultracode on

Owner's decision, 3 Oct 2026, for every session from then on: **Opus 5.5
plans, Sonnet 5.5 executes**, and ultracode is on by default.

- **Opus 5.5 (`claude-opus-5-5`) is the main session.** Talking with the
  owner, brainstorming, reading the code to decide what should change, the
  plan, the design notes under `docs/superpowers/specs/`, and reading what a
  worker hands back before anything is committed.
- **Sonnet 5.5 (`claude-sonnet-5-5`) does everything that executes.** Writing
  and editing code, migrations, running the tests, lint and build, and the
  mechanical end of a commit. The main session hands this to a subagent or a
  workflow worker rather than doing it itself, and does not wait to be asked
  each time; this is the standing request. A worker starts cold, so the
  hand-over carries the plan written out: the files, the rule being enforced,
  and what done looks like.
- **Ultracode is standing multi-agent orchestration**, the main session
  directing workers. That is what keeps the planner planning and the workers
  on the code.

**Name the model on every spawn: `model: "sonnet"`.** That goes on the Agent
tool and on a workflow's `agent()` alike. Nothing else is reliable. A worker
with no model given inherits the main session's, so it runs on Opus and the
split silently does not happen. Measured on 3 Oct in a cloud session, by
reading the `"model"` field in each subagent's transcript under
`~/.claude/projects/<project>/<session>/subagents/`: a probe with no model ran
on `claude-opus-5-5`, and the same probe with `model: "sonnet"` ran on
`claude-sonnet-5-5`. A planning subagent that should think in Opus says
`model: "opus"`.

**`.claude/settings.json` holds the defaults**: `model` is Opus 5.5,
`ultracode: true`, and `CLAUDE_CODE_SUBAGENT_MODEL` is Sonnet 5.5. That last
one did NOT reach the cloud session. The variable was unset there, and the
probe above ran on Opus. It may work in the local CLI, but nobody has checked,
so the named model above is the rule and the variable is only a backstop. The
file pins IDs, not the `opus`/`sonnet` aliases, because an alias follows
whatever is newest. The Agent tool takes only the alias, and on 3 Oct
`sonnet` meant 5.5. Check that again when a new Sonnet ships.

- **Ultracode needs Workflows enabled and a model that supports it**, and
  where either is missing it stays off without complaint. `enableWorkflows`
  is in the settings file too, but a plan that does not include Workflows
  cannot be switched on from a repo. `/effort` offers `ultracode` only where
  it can run; `/config` → Dynamic workflows is the switch. On 3 Oct the file
  turned Workflows on in the cloud session that wrote it, mid-session, but
  ultracode is read when a session STARTS - it applies from the next one.
- **Check the model rather than assume it.** `/model` names the main
  session's. Runtime fallbacks exist, so when output looks off, ask which
  model produced it.
- The model names belong here and in the settings file only. The authorship
  rule above still keeps them out of every commit message, PR body and code
  comment.

## Getting changes onto GitHub

**What is safe is the REPOSITORY, not the remote name.** Corrected 20 Sep
2026 — the table below used to name remotes, and the names differ per clone.

| Repository | Deploys Render |
|---|---|
| `NACC-SYS-V3.1-LOCAL-VER` | **no** — the demo builds from it, live never does |
| `NACC-SYS-V3` | **yes, on `cloud-setup`** |

**Check the URL before pushing, every time**, because the alias lies:

```
git remote -v
```

In the clone at `C:\reyNACC\NACC-SYS-V3.1-LOCAL-VER` there is exactly ONE
remote, it is called **`origin`**, and it points at
`NACC-SYS-V3.1-LOCAL-VER` — the safe one. There is no `local-ver` remote here
and no `live-push` branch; nothing configured in this clone can reach the
repository that deploys live. So here:

```
git push
```

is correct and goes to the local-version repo.

That is the opposite of what the older wording implied, and a session that
trusts the alias instead of reading the URL gets it exactly backwards in one
direction or the other. The clone at `C:\dev\nacc-sys-v3` is the one with two
remotes, where `origin` IS the live repo and the warnings below apply as
written.

**A push is not a deploy, and on 30 Aug 2026 it demonstrably was not.** The
line above used to say a push auto-deploys the demo "once the Blueprint is
connected" — a condition nobody had checked. Four pushes and four green CI runs
later, `nacc-v3-demo-*` was still serving 29 August's code: the new endpoint
answered 404 and the frontend bundle still carried wording replaced a day
earlier. Green CI proves the code is correct, never that it shipped.

So verify the deploy rather than assuming it, from outside Render:

```
curl -s https://nacc-v3-demo-api.onrender.com/api/assistant/capabilities/ -o /dev/null -w "%{http_code}\n"
```

401 means deployed and gated; 404 means the container predates the endpoint.
For the frontend, fetch the page, read the hashed `/assets/index-*.js` name out
of it, and grep the bundle for a string only the new build contains.

**On 10 Sep 2026 it did deploy** — five pushes, each verified this way, each
live within minutes. So the answer changes; the discipline does not. Verify it
every time, because the whole point is that you cannot tell from here.

Three ways that verification goes wrong, all learned the hard way on 10 Sep:

- **The two services deploy independently.** `nacc-v3-demo-api` and
  `nacc-v3-demo-web` are separate Render services off one push. The API went
  live while the web build was still running, and polling only the bundle hash
  said "not deployed" for a backend that was already serving the new code. A
  backend-only commit never changes the bundle at all. Check the one you
  actually changed, and say which you checked.
- **A 401 means nothing without a control.** Probe a route that cannot exist
  in the same run: it must answer 404 while the real one answers 401. And a
  path under a DRF router — anything registered with `router.register` — cannot
  be told apart anonymously at all, because the detail route swallows the
  unknown segment as a pk and answers 401 either way. Pick an explicitly
  routed path, or check the frontend bundle instead.
- **Capture the baseline BEFORE the push, or the hash tells you nothing.** On
  13 Sep the live web bundle was recorded three minutes after the push, by
  which time the new build was already serving. Fourteen minutes of an
  unchanging hash then read as "never deployed" when it meant "deployed before
  you looked" — the inverse of the trap above, reached the same way. The page
  is also behind Cloudflare with `s-maxage=300` (`cf-cache-status: HIT`), so
  the hash lags the origin by up to five minutes anyway. Grep the bundle for a
  string only the new build contains; that answers the question outright,
  whenever you ask it.
- **Do not pipe a long verification through `tail`.** A background
  `manage.py test | tail -8` reported `FAILED (failures=4, errors=1)` and threw
  away every failure name with it; the whole suite had to run again to find
  out what broke. The same goes for `grep` over a downloaded bundle — it can
  abort on a large minified file and print nothing, which reads exactly like
  "the string is absent".

**Do not push to `NACC-SYS-V3`** — the owner has said he does not want Render
touched, and that push is what deploys it. Pushing there needs asking first, in
so many words. In the other clone (the one under `C:\dev`, see below) that
repository is `origin`; in this one it is not configured at all. Read the URL,
not the alias.

### When he does say to push live

**A plain push straight across to `NACC-SYS-V3` is wrong, and will be
refused.** The two lineages
have diverged permanently and on purpose: `render.yaml` here names the demo
services and points at a Neon branch and an empty R2 bucket, while the live
repo's names `nacc-v3-api`/`nacc-v3-web` and points at production. Forcing this
file onto live is how the live deployment lost its file storage once already —
the repair is commit `5d9342a`, "Restore the live Render blueprint that the
demo's file overwrote".

Merge into a branch tracking the live repo rather than pushing across. In the
other clone (the one under `C:\dev`) that branch is `live-push` and the live
repo is `origin`, which is what the commands below assume. **In this clone
neither exists** — add the remote first (`git remote add live
https://github.com/23150056-beep/NACC-SYS-V3.git`) and substitute `live` for
`origin` throughout:

```
git checkout live-push && git pull
git merge cloud-setup                 # keeps live's render.yaml on its own
git rev-parse HEAD:render.yaml        # must equal origin/cloud-setup:render.yaml
git diff --name-only origin/cloud-setup HEAD | grep -E 'render.yaml|Dockerfile|entrypoint|settings.py|\.github/'
git push origin live-push:cloud-setup
git checkout cloud-setup              # never leave the demo branch behind
```

That grep must print nothing. If it prints `render.yaml`, stop — the merge has
picked up the demo blueprint and pushing it breaks live storage.

Check for migrations too (`manage.py makemigrations --check --dry-run`): live
runs against the Neon **production** branch, and `entrypoint.sh` migrates on
every deploy, so a migration that arrives this way lands on real data.

The branch is still called `cloud-setup` locally and lands as `main` on the new
repo. The name is history, not intent.

### If the push is refused

Working directly in the repo, `git push` just works. Two other cases exist:

- **The permission classifier blocks it.** The command is fine; the harness
  declined to run it. Say so and hand the owner the exact command rather than
  looking for another way to run it.
- **Running in a sandbox**, `git push` returns 403 at the proxy and the GitHub
  MCP tools return `403 Resource not accessible by integration` even on a bare
  branch creation. That is where the bundle workflow below earns its keep.

For the sandbox case, the working method is a **git bundle**, applied in
**Command Prompt**:

```
cd /d C:\dev\nacc-sys-v3
git fetch "%USERPROFILE%\Downloads\<bundle-file>" cloud-setup
git merge FETCH_HEAD
git log --oneline -1
git push
```

Facts that cost real time when forgotten:

- **The repo lives at `C:\dev\nacc-sys-v3`.** Not under the user profile. The
  Windows account is `User`; an older machine used `talas` and paths from that
  era do not exist any more.
- **Ask what the downloaded file is actually called before writing the fetch
  command.** The browser renames things — `nacc-v3-update.bundle` arrived as
  `naccv3update.bundle`, and a fetch pointed at the wrong name fails with
  *"does not appear to be a git repository"*, which reads like a broken bundle
  rather than a typo.
- Build the bundle from a commit the owner definitely has:
  `git bundle create <file> <their-HEAD>..cloud-setup`.
- Verify a push landed with `git ls-remote <remote> <branch>` rather than
  asking — compare it against `git rev-parse HEAD`. In this clone that is
  `git ls-remote origin cloud-setup`.

## Infrastructure (live — reference only, see Scope)

- **Frontend + API**: Render (`nacc-v3-web`, `nacc-v3-api`), Singapore region,
  auto-deploys on push to `cloud-setup`.
- **Database**: Neon serverless PostgreSQL, `ap-southeast-1`. Deliberately not
  in `render.yaml` — Render's free database is deleted after 30 days. Neon's
  browser SQL Editor is the quickest way to run a query; it needs no
  connection string and no local `psql` (which is not on PATH on this machine).
- **Object storage**: Cloudflare R2, bucket `nacc-v3-media`. The API token is
  scoped to that one bucket with Object Read & Write. R2 needs path-style
  addressing and `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`; both are
  already handled in `backend/config/storages.py` and `settings.py`.
- **Google Sign-In**: staff and psychologists only — never administrators. The
  OAuth app must stay **In production**; in Testing mode Google refuses every
  account that is not on the test-user list, which looks exactly like a broken
  button.

## Addresses

PSGC lives in the `locations` app, seeded from a committed JSON file — there is
no live address API and adding one would put a processor in §6 for nothing.
`seed_psgc` and `backfill_psgc --apply` both run from `backend/entrypoint.sh`
on every deploy — Render's Shell tab is paid-only, so nothing here may depend
on running a command by hand. Both are idempotent, and the backfill skips
records that already carry codes so it can never undo an address someone picked
in the form.

**The three levels hold together** (29 Sep 2026). The form's municipality and
barangay lists each remember which place they were fetched for and show
nothing else - "Loading…" until it arrives; taken in whatever order replies
came, a slow line had Ilocos Sur offering Ilocos Norte's municipalities. The
server refuses a municipality outside its province or a barangay outside its
municipality, and takes the names from the codes (`_check_address`), only
when a code is being set, so an older address is not refused on an unrelated
edit. An address typed before the lists existed stays on screen while it is
re-picked, and "— Select province —" puts it back.

## Health check

`https://nacc-v3-api.onrender.com/healthz/` returns
`{"status": "ok", "database": "ok"}`. It proves the database credential and
nothing else — a wrong `DJANGO_SECRET_KEY` still boots the app fine and only
shows up as broken sign-in.

## Local copy

`setup-local.bat` then `run-local.bat` from the repo root — SQLite, files on
disk, no Google sign-in, no email. Verified from an empty database: migrate,
seed_initial_data, seed_psgc, sign in, every screen renders. Details in
docs/LOCAL-SETUP.md.

```
API       http://localhost:8000      Frontend  http://localhost:5173
Health    http://localhost:8000/healthz/
Sign in   admin@racco1.gov.ph / admin1234
```

`run-local.bat` opens two windows and does not reload on backend dependency
changes — restart the API window after touching requirements or settings.

## Demo data

`manage.py seed_demo_data` invents 40 children with six months of history —
remarks, self-reports, appointments — so cross-caseload features have something
to work on. Three cohorts: steady, declining, and one whose self-reports drift
toward distress while the case notes stay reassuring. That last group is the
point; it is what a divergence detector would be built to find.

Refuses to run against a hosted database or with DEBUG=False, and the guard
runs before anything opens a connection.

**Seeded data must satisfy the rules the endpoint enforces.** Three times
now. No seeded psychologist had a bookable hour, so the calendar refused all
forty children; then the referral gate shipped and refused them again, because
the test fixtures had referrals and the seeder did not; then the same two
faults turned up on the HOSTED path, where `import_demo_data` loads the
fixture. A rule and a seeder maintained separately drift, and the tests do not
notice.

The hosted one was the worst of the three, because the repair was unreachable:
a referral is a file and availability belongs to the branch's own accounts, so
the fixture can carry neither, and `fix_demo_schedule` — which fixes exactly
this — refuses hosted databases by design. `import_demo_data` now installs
both itself. Every one of these paths is covered by a test that goes through
`booking.bookable_slots`, the real rule; asserting rows exist passes while the
calendar stays empty.

**A fixture's user ids are the exporting machine's**, so none is loaded.
`import_demo_data` deals each child a psychologist and a social worker from the
branch's own accounts in the rows, before `loaddata`, and every record's user
links move with its child (`rehome_people`): the child's local psychologist or
SW becomes its new one, a different psychologist - the one before a transfer -
becomes whoever got that psychologist's own caseload, and anyone else the
child's psychologist. `forget_custodian_contacts` leaves custodian numbers and
consent behind. Rehearsed on 29 Sep 2026 against a stand-in branch numbered
differently: before, a fresh export failed the load outright (`social_worker_id`
6 did not exist there), and once it loaded, all 198 sessions sat with somebody
other than the child's psychologist, 68 of them with a Staff account; after,
198 of 198 with the child's psychologist and every author a psychologist.
Without `--clear` only the fixture's children are dealt; a child already there
keeps its psychologist.


## Booking and the calendar

Every rule about whether a booking can exist lives in `scheduling/booking.py`,
not in the viewset. `perform_update` was never written, so rescheduling — the
thing a busy office does most — reached the model with nothing checked: an
appointment could be moved outside availability, on top of another one, or into
last week. A rule enforced on one verb is not enforced.

**Three tiers, and the difference is load-bearing:**

- The **availability window and its capacity are a preference.** A psychologist
  working outside their own posted hours is their business, and
  `own_calendar=True` waives those.
- **Overlaps are not.** Nobody is in two places at once, whatever their role.
- **Leave is not either.** `Unavailability` is a date range, inclusive at both
  ends, and unlike the referral it blocks MOVES as well as new bookings — a
  referral arriving late is paperwork catching up, but putting a session on a
  day somebody is away is wrong whenever it is done.

**The slot grid runs the same `errors_for()` the endpoint runs** — not a
cheaper approximation. `test_everything_it_offers_can_actually_be_booked` takes
every slot the grid offers and books it until the day empties. The moment the
offer and the refusal are computed two different ways the screen starts lying,
and a booking screen that lies is worse than a blank time field because it
looks authoritative.

**A child needs a case referral on file before any session is booked**, checked
for every role. Moving an appointment that already exists is exempt, so
children booked before the rule are not stranded by it.

Declaring leave never cancels what is booked inside it, and removing an
availability window never does either. Those sessions were agreed with
somebody; both screens report the count and change nothing.

**The calendar screen** (`pages/Schedule.jsx`, tested in a browser as a SW, a
psychologist and the ISA on 2 Oct 2026):

- **Away days are shaded** (`dayPropGetter`), but only on ONE person's
  calendar - a psychologist's own, or the one picked in "Show one
  psychologist". With everyone showing, a day is not closed because one person
  is away, so there it is only NAMED ("Away: M. Bulan") in the month cell. Only
  upcoming leave is fetched (`?upcoming=true`), so past leave is not shaded.
- **A psychologist records their own leave** from "Away & leave" on their
  availability card, which opens the same page the ISA gets for them
  (`canRecordLeave`: the ISA for anybody, a psychologist for themselves - the
  server's rule). That page used to be admin-only, so they could not record
  leave or see what the ISA had recorded for them. The leave serializer does
  not require `psychologist`; the view fills in the psychologist's own and
  asks anybody else "Whose leave is this?".
- **The drawers and the appointment card are the shared `Drawer` and `Modal`**
  from `ui`, so Escape, a close button, a focus trap and focus return come
  with them. Hand-built overlays had none of those. The card closes when
  Reschedule opens the drawer, or the first click only dismissed it.
- **An edit says it is an edit.** A weekly pattern is edited with no block id
  (`blockForm.byDay`), so "is there an id" read as "add": the title, button and
  toast said "Add Availability". `editingBlock` covers both. The weekday
  ticks still key off the id - editing a pattern needs them.
- **An edit is checked against whose it is BEING MADE, not only whose it
  was** (`perform_update` on availability and leave). It checked the current
  owner only, so a psychologist could PATCH their own window or leave onto a
  colleague - and mark THEM as away. Naming themselves again stays fine: the
  pattern editor resends the owner with every block.
- The "Away" tag in a month cell becomes a dot under 640px (`.racco-away-tag`):
  the cell is ~50px wide and the word showed as a lone "A".
- Refusals read as sentences (`firstError`), never `{"end_time": [...]}`.
- axe-core on `/schedule`: one `aria-required-children` violation remains, on
  react-big-calendar's own "+n more" button inside its row. It is identical on
  the unmodified page; the drawers and the availability page audit clean.

## Removed: the adoption tracker and SAMD readiness

Both were removed on 17 Sep 2026 at the owner's request — the `adoption` and
`samd` apps, their API routes, and the `/adoption`, `/adoption/case/:id` and
`/samd` screens.

- **The "Adoption" CASE TYPE was not removed and must not be.** It is a
  different feature that happens to share the word: `Child.case_type`,
  `type_of_adoption`, `TYPES_OF_ADOPTION` and the "Adoption finalized"
  termination reason are all core case management and all still in use. Same
  for `NACC-SAMD-GF-000` — that is the agency's paper certification form, which
  the Agency Summary still mirrors; it was never the `samd` app.
- **The teardown migration lives in `children`, not in either departing app**
  (`children/0019_drop_adoption_samd`). A migration inside `adoption` would have
  been deleted along with it, so a database that already had the tables would
  never be told to drop them — the code goes, the tables stay, forever.
- **It deletes the `django_migrations` rows too**, and that is the point rather
  than tidiness: leaving `adoption.0001_initial` behind means an app called
  `adoption` created later is read as already migrated, skipped, and its tables
  never created. That is exactly the `ai` app trap described under "The
  assistant app". Deleting the rows makes both names genuinely free again.
- The models used explicit `db_table` names (`tbl_adoption_*`, `tbl_samd_*`),
  not the `app_model` names Django would generate. A `DROP TABLE` written from
  the app label would have silently dropped nothing.
- Data dumped to `adoption-samd-backup.json` before the drop (312 rows, all
  adoption; SAMD had never had a single assessment).

## Getting an account

Two doors, one queue. Since 2 Sep 2026 a sign-up form lives at `/signup`
alongside the Google button; both create a **PENDING** row and neither grants
anything. `AccessRequests.jsx` is the screen where a human decides, and it is
the only access control the system has.

- **`User.save()` derives `is_active` from `status`.** That one line is what
  makes an internet-facing sign-up form safe: a PENDING account cannot
  authenticate even with the correct password, because Django's auth backend
  and SimpleJWT both check `is_active`. Never set `is_active` directly, and
  never "fix" a stuck request by flipping it.
- **The stated role is a claim, never a grant.** `requested_role` exists to
  pre-fill the approver's dropdown, and `approve/` deliberately has no
  fallback to it — defaulting to the claim would turn every request that
  omitted the field into self-assignment. Administrator is refused at every
  layer: the serializer, the approve endpoint, and the two cards on the page.
- **One refusal message for every address already spoken for** — taken,
  archived, declined. Different messages would turn an open form into a way to
  enumerate agency staff, which is the same reasoning behind the Google path's
  three-in-one refusal. A test asserts the active-account and archived-account
  refusals come back identical.
- **Both doors share one abuse budget** (`accounts/signup_limit.py`): a per-IP
  cache counter plus a durable ceiling on outstanding PENDING rows. Two doors
  with separate budgets just means the cheaper one is what an abuser uses.
  Asking for a new email code and typing one in write no request, so each has
  its own per-IP counter beside it (`resend_is_throttled`,
  `confirm_is_throttled`; 4 Oct 2026). Charging them to the creation counter
  would let a few resends use up a colleague's sign-up from the same office.
- **Declining archives rather than deletes.** An archived address cannot
  register again, so a refused applicant cannot loop until a distracted
  administrator approves them.
- **A typed address is proved by a six-digit code** (`accounts/email_verification.py`).
  Until it is, it is only what someone typed, unlike a Google one, which Google
  verified. The queue says which door each request came through for exactly
  that reason.
- **The code is an `EmailVerification` row** (4 Oct 2026, accounts 0012), not
  a cache entry, for the reason the phone code is: a code stored by one
  gunicorn worker was "expired" to the next. One row per request, gone with
  the account. It vouches only for the address it was mailed to, only while
  the request is pending. Five guesses, fifteen minutes, and a wrong guess no
  longer extends them. Every refusal reads the same.
- **An applicant can ask for a new code** (`POST /api/auth/signup/verify-email/resend/`).
  It always answers 202 with one fixed sentence, mails only a pending typed
  request, and holds the phone code's limits per request: a minute apart, five
  an hour. A resend while a code is outstanding re-mails THAT code, so a
  stranger asking for one cannot void the code the applicant holds. The way
  back is "Already asked for access? Confirm your email" on `/signup`, linked
  from `/login`; the confirm step used to exist only in the tab that signed
  up. Accepted, not fixed: a resend for a real pending address takes the mail
  gateway's time, so timing can tell one apart. The limits bound the probing.

## Signing in

- **Every token carries `pwd`**, Django's `get_session_auth_hash()`, checked on
  the access path and on refresh. Changing a password now ends the sessions
  that used the old one; without it a refresh token kept renewing for a day and
  "please sign in again" was theatre. **A token with no `pwd` claim is
  refused**, so everybody signs in once after that ships. Expected, not a fault.
- **Tokens live in `sessionStorage`**, via `api/session.js` so the client and
  AuthContext cannot disagree about where they are. Closing the browser ends
  the session, and it is per-TAB — a second tab signs in again.
- **A Google account has `set_unusable_password()`.** Never flag one
  `must_change_password`: the gate asks for the CURRENT password and
  `check_password()` is False for an unusable one, so it locks them out with no
  way back. `reactivate` guards this with `has_usable_password()`.
- **Approval requires `email_verified`**, because approving emails a temporary
  password and a mistyped address sends it to whoever owns the typo. Google
  requests arrive verified; typed ones confirm a six-digit code. Migration 0010
  backfilled the Google accounts already in the queue.
- **Approval is the only way in** (4 Oct 2026, `UserWriteSerializer.validate`):
  an edit cannot move an account from PENDING to ACTIVE ("Approve it from
  Access Requests"). An edit that changes a pending typed request's address
  clears `email_verified`, so the new address must be proved too. A pending
  Google request's address cannot be edited at all: it is the Google
  account's, and with no code door it could never be proved again.

## The assistant app

Restored 26 Aug 2026 — pre-session briefs, document summaries, remark polish, a
census narrative, usage metrics. On by default; one administrator switch in
Settings, no per-feature flags.

- **Never rename `assistant` to `ai`.** A previous `ai` app was deleted but its
  rows are still in `django_migrations`. Create an app called `ai` again and
  Django sees `ai.0001_initial` already applied, skips it, reports success, and
  the tables are simply never created.
- **Prompts are assembled static-prefix-first.** A fixed instruction block, a
  module constant identical on every call, then the dynamic facts — never the
  other order. That keeps Ollama's prefix cache warm: ~0.37s cached versus
  17-20s cold. A stable prefix is worth roughly 17 seconds a call.
- **No per-request model options.** `generate()` sends `model`, `prompt`,
  `stream` and `system` only. Each distinct option set makes Ollama evict and
  reload the model; an explicit `num_ctx` measured 6x slower end to end.
- **`qwen3.5:2b` does not load on this machine** — it fails allocating a ~2 GB
  buffer. `qwen2.5:3b-instruct` is what everything was built and measured
  against, and it is the default in `AssistantSetting`.
- **Set `OLLAMA_HOST=127.0.0.1`.** It binds `0.0.0.0` by default, which puts an
  unauthenticated model server on the local network. Also
  `OLLAMA_KEEP_ALIVE=-1` (avoids a ~12-16s cold load, costs 1.9 GB resident) and
  `OLLAMA_NUM_PARALLEL=1`. **`start-ollama.bat` sets all of these and
  `run-local.bat` calls it**, so the local copy starts the model server itself.
  The trap is the Ollama tray app: it starts its own server at login, bound to
  every interface, and the script can then only stand aside and warn. The four
  variables are asserted in a test, because losing the first one is silent.
- **The notes are Taglish, and that breaks things.** Measured: remark polish
  drifts into Tagalog on 67% of Taglish inputs and 0% of English ones, so a
  drifted draft is now rejected rather than shown. Briefs are far better —
  0/60 invented names, 3% drift, median 11.6s. One brief in the wild did invent
  a child's name, so this is not theoretical.
- **`manage.py ai_eval` measures all of that**; `manage.py ai_check` says
  whether the runtime is reachable. Neither runs in the test suite — both need
  a live Ollama. Never claim the output is fine without running `ai_eval`.
  `ai_eval` sends real notes and reports to the model and writes no
  `AssistantJob`, so its reads are not in the access log below.
- **A brief opens with facts from plain queries** (4 Oct 2026,
  `assistant/brief_facts.py`, `GET /api/assistant/brief/child/<id>/facts/`,
  shown by `components/BriefFacts.jsx` above the prose): next session and
  purpose, days since the last completed one, open problems, the active plan's
  objectives, the COUNT of unreviewed self-report answers (never their words),
  and the child's care gaps by the viewer's role. Not gated, not throttled, no
  model, so they show when the assistant is off, hosted or slow. The prompt is
  unchanged; feeding these facts INTO it is next step 5 and needs `ai_eval`.
  The modal keys every reply to the child it was opened for: a page that stays
  mounted across children once showed one child's facts on another's page.
- **The written brief is the psychologist's only** (owner, 8 Oct 2026).
  `PreSessionBriefView`, `LatestBriefView` and prefetch answer 403 to Staff and
  the ISA BEFORE `gate()`, so nothing reaches the model and no job is written.
  Its instructions say "for a licensed psychologist" and it is built from
  remarks, which a SW cannot write - for them it was always empty. The ISA is
  the agency's IT support and has no case reason to have the model read a
  child's notes. **Both get the "Case brief"**: the same facts endpoint with
  `kind: "case"` and rows made for a SW - the case referral and its CONFIRMED
  summary, the psychologist (assigned / asked n days ago / declined and why /
  none), consent, custodian texts (`custodian.status_of`, never the name or
  number), the newest survey; every care gap a row already says is
  dropped, for both role lists (the ISA's consent gap is `consent_missing`
  from `compute_alerts`, a SW's `no_signed_consent`; a guard test fails on a
  gap type nobody has decided). `/assistant/capabilities/` says `brief: "clinical" | "case"`.
- **No AI-written case brief for social workers** (owner, 12 Oct 2026). It was
  built (job type `case_brief`) and measured on the owner's PC with
  `ai_eval --feature case_brief`: 2/30 drafts invented dates, and drafts the
  checker passed invented RELATIONSHIPS a SW could act on ("next home visit by
  the social worker" for a psychologist session, "pending survey results", a
  real date pinned to the wrong event). The endpoints and the screen are
  gone; the prompt builder and the eval feature stay so it can be measured
  again (e.g. once referral summaries are confirmed). Do not bring it back
  without a passing eval AND reading the samples.
- **Instead, "What to do next" by rules** (`brief_facts.next_steps`, Staff on
  their own record only): file the referral, ask a psychologist / chase one
  silent 7+ days / ask another after a decline, consent, book, custodian
  texts, an unanswered survey, survey answers to read, the case study - each
  with a link to where it is done, firing at the same moment as its Dashboard
  gap (thresholds imported from `care_gaps`, pinned to the second). The SW's
  leftover care gaps drop what a step says (`_NEXT_STEP_GAPS`, guard test).
  No model, so nothing can be invented; works on the demo too.
- **Every model text a SW can still get is checked first**
  (`assistant/guards.py checked_draft`, built on `evaluation.py`): the census
  narrative (names, dates, numbers vs the figures) and a case-referral summary
  (names vs the text the model saw) answer 422 "The assistant's draft
  mentioned something that is not in the source…" and store nothing; a
  self-report flag's model reason that fails becomes "flagged by the local
  model" (the flag is kept). Known limits: numbers are compared as a set, a
  name opening a sentence is not seen, and invented relationships are not
  caught at all - which is why the model text a SW can reach is kept small.
- **`STAFF_DRAFTING` decides, for every `AssistantJob` type, what a Staff
  account gets**: refused (brief, case_brief, remark_polish), guarded
  (doc_intelligence, census_narrative, self_report) or tool_only (chat).
  `test_staff_drafting.py` fails on a job type nobody has decided, and
  exercises each as a Staff account against a model that invents a name, a
  date and a number. Building it found two claims the code did not keep:
  remark Polish answered ANY signed-in account (now Psychologist and ISA
  only), and a self-report flag showed the model's own words unchecked.
- **The checker no longer counts a "Part 2" / "Step 1" heading as an invented
  number** (`evaluation.py`); "Part 2 sessions" still counts.
- **The ISA can see who had the model read a child**: the Assistant log tab
  on the child's page, administrators only (`ChildAccessLogView`). Briefs,
  report and referral summaries, the self-report check; who, when, what kind,
  status, never the text. `AssistantJob.child` (assistant 0006, nullable,
  backfilled from `input_ref`) says whose record it was, because a summary's
  `input_ref` names a document and documents are hard-deleted. **A new feature
  that sends a child's record to the model must pass `run_job(..., child=)`**,
  or its reads silently stay out of the log; nothing checks this
  automatically. A check refused before anything was sent (hosted, or the
  assistant switched off) is not attributed: a read that never happened is
  not a read.
- **A brief belongs to whoever drafted it** (27 Sep 2026). It is written from
  what its requester may see, so `LatestBriefView` and prefetch look up
  today's brief by user AND child. Keyed by child alone it handed an ISA's
  full-history brief to a psychologist whose screen hides that history. Where
  history is hidden, a brief older than the child's `updated_at` is drafted
  again (`_current_brief`): it may predate the ISA hiding it.
- **Summarising and confirming are writes to the document**, checked by
  `_document_to_summarise`: reports by `is_admin_or_assignee`, referrals by
  administrator or staff — the documents' own viewset rules — plus the
  carry-history check. Checked as reads, a social worker could replace a
  psychologist's confirmed summary for good. The buttons are hidden to match,
  and the serializers return an UNCONFIRMED draft only to those same writers.
- **`get_ai_client()` refuses a hosted model unless the caller passes
  `allow_hosted=True`.** Only the chatbot, the two administrator probes,
  `ai_check` and `ai_eval` do. Everything else drafts from case records and
  answers 503 on a hosted deployment. There `/api/assistant/capabilities/`
  says `drafting: false` (`services.drafting_available()`, the same
  condition), and the screens hide Polish, both AI summary buttons and the
  census narrative card (`useAssistant().drafting`). The brief button stays and
  shows its facts with one line saying a written brief is not available. The
  flag fails open until the answer arrives; the server stays the authority.
  Audit and next steps:
  `docs/superpowers/specs/2026-09-27-assistant-role-access-design.md`.

## The chatbot

Built 26 Aug 2026. A docked panel on every protected screen, backed by
`POST /api/assistant/ask/`. Design in
`docs/superpowers/specs/2026-08-26-assistant-chatbot-design.md`.

- **The model's only output is a tool name and its arguments.** It never sees a
  result. The server runs the queryset and returns plain data, which the panel
  renders. That is why a child's name cannot be invented on the way out, and
  why a turn costs ~2s rather than ~20s.
- **Scope never comes from the model.** No tool declares a "which children"
  parameter; `_visible_children(request)` answers that from `request.user`,
  using the same rule as the clinical viewsets. An invented argument is
  discarded by the validator before it can reach a queryset.
- **Stateless — no conversation history.** History would sit after the cached
  prefix and be re-prefilled at CPU speed every turn.
- **Concern search matches words, not the whole phrase.** The model says
  "school refusal"; this agency records "School attendance difficulty".
  Whole-phrase `icontains` returned **zero children for every real question**
  and its unit test passed anyway, because the fixture invented text that
  agreed with the assumption. Write fixtures from what the live database
  actually contains. When nothing matches, the tool returns the recorded
  vocabulary rather than an empty list.
- **Tagalog works.** 55/55 clean over 5 reps x 11 cases, median 2.4s, both
  registers landing on identical answers. `ai_eval --feature chat` reproduces
  it, and scores whether the answer was *empty* as well as whether the routing
  was right — the unmeasured half is the half that broke. It also scores the
  ARGUMENTS, and it scores the tool AFTER the guards run: `kahapon` routed to
  the right tool, asked for the wrong day, and passed; and a guard doing its
  job read as a 3/3 failure.
- **Ten tools as of 30 Aug 2026**, and the count is asserted in a test so an
  eleventh cannot be added on a hunch. Every addition since the sixth came with
  its own `ai_eval` run.
- **A tool description is routing logic, not documentation.** Four separate
  bugs, all the same root cause. An example is copied VERBATIM into arguments:
  `'trouble sleeping'` matched nothing because the record says "Sleep
  disturbance", and `'withdrawn'` matched nothing because it says "Withdrawal".
  A Tagalog example ATTRACTS by shape: `'sino ang walang psychologist'` pulled
  every "sino ang mga bata na…" question into the wrong tool 3/3. A "do NOT use
  for anxiety, emotions" clause measured WORSE, because the router reads the
  keywords and drops the negation. And a description that under-claims loses
  the question: appointments never said it answered about the past, so "what
  did I do last week?" went elsewhere. A test now reads the examples out of the
  description and fails if any matches nothing in the live vocabulary.
- **Descriptions interact globally.** Changing only the appointments
  description flipped an unrelated Tagalog case from 3/3 right to 3/3 wrong.
  The tool array is one prompt; nothing in it is tuned in isolation, so batches
  stay small and each earns its own eval run.
- **The tool ceiling is the model's, not the design's.** At ten tools the local
  `qwen2.5:3b` scores 3/87 wrong tool and 3/87 wrong argument, and four rounds
  of description tuning could not hold it. `@cf/meta/llama-4-scout` scores
  **0/87 on everything at median 565ms**, against 4465ms local. The demo runs
  hosted and a developer's machine runs the 3B, so the two now disagree on the
  same questions — when someone reports a bad answer, ask which model answered.
  `manage.py ai_check` says, and says HOSTED or local.
- **One generation at a time, process-wide.** A question asked while a brief is
  generating waits for it: measured 1.7s idle, ~19s under that contention.
  Deliberate — concurrent runs on four cores are slower, not parallel.

## Self-report concerns

Built 27 Aug 2026. Flags distress in a child's own words. Design in
`docs/superpowers/specs/2026-08-27-self-report-concerns-design.md`.

- **The children write Ilocano, not only Taglish.** The agency is RACCO 1 and
  the self-reports include `mabutbuteng` (scared) and `adda … problema` (there
  is a problem). A Tagalog-only list passes both.
  `self_report_detection.LEXICON_REVIEWED["ilo"]` is **False** — the Ilocano
  entries have not been read by a speaker. That gates launch, not building.
- **Detection reads the (question, answer) pair, never the answer alone.** 62 of
  122 reports answer "Who do you talk to when you are sad?" with "Nobody" or
  "Ako lang" — the largest signal in the data, invisible to anything reading
  answers on their own.
- **The lexicon is the floor; the model can only add.** Measured
  `ai_eval --feature self_report`: the model **missed 28%** (10/36), including
  the Ilocano disclosure 3 times out of 3 and "Lagi akong umiiyak sa gabi"
  once. The lexicon caught every string the model missed. Never make the model
  the primary detector.
- **No recall figure exists and none may be quoted from demo data** — 366
  answers are only 17 distinct strings, so any number measures the seeder.
- **Self-reports are exempt from the carry-history control.** The child's own
  words are not a colleague's prior opinions. Case notes are unaffected: they
  still follow `assignee_sees_history`, which defaults to True and filters at
  read time rather than deleting anything.
- **That filter is `accounts.scoping.hide_earlier_history`**, applied after
  `scope_to_visible` by every reader of the six opinion records (remarks,
  reports, interviews, treatment plans, result entries, pre-assessments): the
  record endpoints, the child's page and Monitoring. Until 27 Sep 2026 only the
  child's page applied it, so `/api/remarks/?child=` served the notes it hid,
  and the next psychologist could even edit them. The record base class hides
  by default; problems and consents opt out, as the page always had them.
  What is worked out FROM pre-assessments (status, instruments used,
  Monitoring's count and last activity) reads the prefetch, so every screen
  that shows it prefetches through `visible_pre_assessments`; care-gap alerts
  deliberately do not. `clinical/tests/test_carry_history.py`.
- `manage.py scan_self_reports` backfills and is idempotent; re-run it after
  adding a phrase.

## Reports

Built 23 Sep 2026. Psychologists upload their own report files, each in their
own format; there are no report templates in the system yet, on purpose -
none of the real ones has been seen.

- **One upload form, two doors.** A report is filed from Results & Reports or
  from the child's own record (Results & reports tab; a referral from
  Casework), both through `components/UploadDrawer.jsx`. The check before
  filing lives in that component, so it cannot be on one screen and missing
  from the other - keep it that way rather than copying the form.
- **A record stays with the child it was filed for** - the owner's decision,
  24 Sep 2026. An update that changes `child` is refused for everyone,
  administrators included (`_ChildScopedClinicalViewSet.perform_update`, all
  eight clinical record types; naming the same child again is fine). Before
  that, one PATCH could put a psychologist's report on a child who was not
  theirs. No screen ever sent a change of child; a record filed for the wrong
  child is filed again for the right one. A report's file cannot be replaced
  by an update either. Case referrals follow the same two rules through their
  own viewset (`_refuse_a_move` is shared) - a moved referral would unlock one
  child's calendar and lock another's. The screens replace a referral by
  filing a new one and deleting the old.
- **Word files are read now, not only PDFs** (`clinical/services.py`, standard
  library only). `.doc` (Word 97-2003) still cannot be; the screen says so.
  Reports uploaded before this are read the first time something needs their
  text (`ensure_text`), because the hosted copies have no shell for a
  backfill; `manage.py check_reports` does it locally.
- **Headings are found from the file itself** and marked `## `, the agency
  form convention. Measured against the two real Word forms in
  `docs/agency-forms/`: neither uses a heading style or bold - one marks
  headings in capitals, the other as plain Roman-numbered lines - so all four
  signals count. Arabic numbering does not: that is a recommendation list.
- **The check before filing is deterministic** (`clinical/report_check.py`):
  another child's full name, another case number, an age, birthday or sex that
  is not this child's. It never blocks. It compares against the children the
  **uploader can see** - comparing against every child would tell a
  psychologist whether the agency holds a record for a name. The same goes
  for whoever reads the report later: a psychologist a child is reassigned to
  is not shown a finding naming a child who is not theirs, and with no reader
  known those findings are left out. Tests hold both.
- **Summaries are fitted** (`prompts.fit_document`): the whole text used to go
  to a model whose window is a few thousand tokens. 8,000 characters is
  arithmetic, not a measurement - `ai_eval --feature summary` measures it,
  fitted against whole, on the machine that runs the model.
- **Reports are read on screen too** (24 Sep 2026, staff asked): a PDF in a
  frame, and a Word file as its text, headings kept, through
  `/report-files/<id>/text/`. Same object and queryset as the download, so it
  shows nobody anything the Download button would not.
- **A PDF frame must NOT be sandboxed** (`components/PdfFrame.jsx`, shared with
  the consent-scan preview). Chrome refuses a PDF in a sandboxed frame outright
  - its viewer is a plugin and no sandbox flag allows one - and shows a grey
  broken-file icon. The consent preview did exactly that from 2 Sep to 24 Sep,
  unnoticed, because headless checks never looked at the frame. The lock is
  the blob's type instead: `utils/pdf.js` types every framed blob as
  `application/pdf` itself, so it reaches the PDF viewer and never renders as
  a page. Measured: an HTML file uploaded as `.pdf` shows "failed to load" and
  runs nothing. Check a frame in headful Chromium (`xvfb-run`), not headless.
- **Print on a child's record prints a psychological report**
  (`components/PsychReportPrint.jsx`), not the screen: identifying
  information, reason for referral, background, procedures, observations,
  results, summary, recommendations, signature block. It is a STANDARD layout
  until the agency's template is seen, filled only from what the reader could
  already see; remarks and self-report flags are left out, and anything not
  recorded prints as lines to complete by hand. index.css hides every
  `<header>` when printing, which is why it uses none.
- **Demo reports come in three layouts** (`clinical/demo_reports.py`), turned
  over within each psychologist's children - turned over across the list they
  fell in step with the seeder's round-robin and each psychologist saw one.
  Exactly one carries another child's name, for the check to find.

## The case study (the SCSR on the child's record)

Built from 8 Oct 2026; design in
`docs/superpowers/specs/2026-10-07-scsr-parts-2-5-design.md`. The owner's aim:
the Social Case Study Report "digitally on the child record module" - a Case
study tab on the child's page that prints the SCSR. No Word export.
App `case_study` (never `adoption`, see "Removed").

- **Block A is every child's; B and C are adoptions'** (owner, 10 Oct 2026).
  The child's background - referral, description, medical history,
  immunizations, development, birth family, surrender or abandonment - is the
  child's profile whatever the case type, so the tab shows for Foster Care and
  the rest with block A only. `sections.applies()` gates B and C on
  `case_type == "Adoption"` (mirrored in `scsr.js appliesTo`); completeness,
  Final and the snapshot all read `applies`, so a non-adoption Final is block
  A. Moving a record away from Adoption HIDES B and C - their rows stay and
  come back if it moves back. Reopen works on any active record, or a final
  made as an adoption would be stuck after a case-type change. A
  non-adoption record reads "The Child" (not "/Adoptee"), shows "Case type" in
  Part I, and prints block A without the adoption subtitle; a final copy
  prints by the case type it was MADE with.

- **Numbering is the template's**: A (the child, I-V), B (the PAPs, I-XVII),
  C (placement, I-VI), roman numbers restarting in each block. Part I is the
  record form, read live and frozen at Final; everything else is one row per
  section (`CaseStudySection`, unique on case study + key, JSON value).
- **The catalogue is `case_study/sections.py` and `config/scsr.js`**, pinned
  by `test_catalogue.py` the way `test_intake.py` pins caseData.js. **A key is
  never renamed or reused**: `ALL_KEYS_EVER` keeps every one ever issued. A
  JSON key rename is the 0020 trap in another form - stored text would stop
  matching any section.
- **Access lives in `case_study/access.py`, not the clinical base class**,
  where "Administrators see everything" would hand IT support every case
  study, the adoptive parents' incomes included. The record's SW reads and
  writes; the ASSIGNED psychologist reads block A, drafts included (a pending
  one is nobody), minus the psychological-evaluation highlights where the
  history is not carried to them - otherwise the previous psychologist's
  findings reach them through the SW's text; the ISA gets status only (exists,
  state, holder, "(inactive)", how much is missing) - never text, never print;
  anyone else 404. Not registered in Django admin, where the seeded ISA is a
  superuser. No chatbot tool, prompt, export of counts or duplicate check
  reads it.
- **Each section saves on its own with the version the writer saw**: a
  conditional UPDATE, 409 with the saved text on a stale version. Sessions are
  per tab, so the commonest conflict is the same person in two tabs.
- **Not applicable hides, never deletes**: a ticked box keeps its text and
  unticking brings it back; a PUT without a `value` key leaves the text alone.
  The psychologist's read sends null for a ticked box.
- **Pre-fills are offered, never saved**: referral source and reason, medical
  notes, and the latest psychological report's summary ONLY if confirmed.
- **Ages are as of Date prepared**, not today. Domestic Relative asks whether
  the PAPs had the child more than two years (pre-answered from the placement
  date; unknown when there is none); a yes hides Placement History, as for
  Adult.
- **Nobody in the system approves.** The ISA is IT support, not the Head of
  Office. The SW marks the case study **Final** and the printed copy is signed
  on paper. The Head of Office's name comes from `AgencyProfile` (Settings,
  ISA-edited - the card that used to be a fake "RCPC" field) and the SW's PRC
  license number and validity from their own profile (`UserProfile`, Staff and
  Psychologists; an administrator has none).
- **Final writes an immutable copy** (`case_study/finalize.py`, the one path
  the endpoint AND the demo seeder use, so a seeded final can only exist if it
  passed `missing_sections()`): Part I as of Date prepared, every section that
  applies, the preparer's license and the agency details OF THAT DAY. A
  reprint shows what was signed even after a license renewal or a record edit.
  A ticked Not applicable box is copied without its hidden text. Final is a
  conditional update on `status=draft` AND the `updated_at` the screen saw, so
  a double click or another tab's newer save is a 409, never a final of text
  nobody reviewed; a box save that loses that race is rolled back. The box-save
  response carries `case_study_updated_at` for that reason.
- **Reopen** puts it back to draft; every final stays on file ("Finals on
  file", each printable). A closed case cannot be reopened - its finals still
  print. Finalized is addressed to the ASSIGNED psychologist
  (a pending one is told nothing); Reopened to nobody; neither carries text.
- **Demo data**: about a third of the active Adoption children get a draft and
  a few others a final (`case_study/demo_case_studies.py`). The export blanks
  the preparer's license and every phone, email and employer address in the
  PAP table, live and in snapshots, so the fixture file never carries them;
  the import re-homes user fields and the snapshot's preparer to the child's
  SW and takes the agency block from the importing machine's `AgencyProfile`.
- **The screen** (`components/caseStudy/`): one editor per section kind, so
  all three blocks are editable. Each box saves with `useConfirm()`; "Save
  all" confirms once and stops at the first refusal. Unsaved typing is kept in
  `localStorage` under `nacc-draft:case-study:<user>:<child>:<key>` and offered
  back; logout clears the `nacc-draft:` prefix. On a 409 the SW chooses "Load
  the saved version" or "Keep mine" - neither text is lost without a choice.
- **Print follows the tab**: on the Case study tab the page's Print button
  prints the SCSR (`ScsrPrint`) - the newest final's copy when the case study
  is final, the saved draft marked DRAFT otherwise (asking first when
  something is unsaved); anywhere else it prints the psychological report.
  Only the SW gets it.
- **Every print used to come out ONE PAGE long** (found 8 Oct 2026). The app
  shell is a fixed 100vh with hidden overflow so only `<main>` scrolls, and on
  paper that clipped everything after page one - the psychological report had
  been losing its recommendations and signature block. index.css now lets
  `#root`, `.racco-shell` and `.racco-shell-row` go to auto height in print.
  Check a printed page COUNT, not just the first page.
- `/children?edit=<id>` opens the record form for that child ("Edit on the
  record" from Part I). Activity reads "Started a case study for X" and opens
  `?tab=casestudy`; "Finalized ..." and "Reopened ..." the same way
  (`ActivityLog` actions `finalized` and `reopened`, activity 0005).

## The record form (Add Record)

Rebuilt 24 Sep 2026 at the owner's request: Identity and Case merged into one
"Child's Profile" step with the Category first, a street address, the whole
middle name, a date found, and every question that applies made mandatory.

- **The rules live in `children/intake.py`**, the browser's copy in
  `config/caseData.js`, and `children/tests/test_intake.py` pins the two
  together. Change one and not the other and that test fails, which is the
  point: a form that lets something through the server refuses is a dead end.
- **One date, never both.** A Regular adoption, Residential Care and
  Independent Living record the Date of Admission; every other adoption type,
  Foster Care, Kinship Care and Family Tracing record the Date of Placement to
  Custodian. An adoption shows neither until its type is picked. The save
  sends the date no longer asked as `null` - Records used to leave an empty
  date OUT of the request, which kept the old value on the record.
- **Changing the case type hides answers; it never deletes them** (29 Sep
  2026). Switching back brings them back. What the final case type does not
  ask is sent blank where the case changed (`caseData.js caseChanged`: the
  case type, or the type of adoption where one is asked), and exactly as the
  record held it where it did not (`unaskedAnswers`) - so a detour through
  another case type changes nothing, and an older record's hidden values go
  back as they came. The form's own checks judge what is sent, as the server
  does. Deleting on the spot lost the
  custodian, their confirmed number and consent, and the date to one slip of
  the dropdown, and made clearing the Case Type to re-pair the Category cost
  what clearing the Category did not.
- **Category first means the pairing filters both ways**: a category narrows
  the case types and a case type narrows the categories. The server refuses a
  pair the lists do not offer, but only when one of the two is being set.
- **Mandatory on create; on an edit, an answer cannot be taken away.** A record
  from before the rule keeps its blanks through an unrelated edit (the form
  names them "Blank from before, saves as it is"), except that changing the
  case type or adoption type asks that type's questions again - those, and any
  answer being taken away, are "Needed before saving" and hold Save, by the
  server's own rule (`refusedIfBlank` mirrors `_require`; `DYNAMIC` is pinned
  in test_intake). The custodian is never asked of a psychologist, who cannot
  record one. The fullname-only create path the older tests use stays exempt.
- **The form checks what the server checks, as it is typed**: the age,
  dates not in the future or before the birth, the category pairing. A moved
  birth date is checked against the dates already recorded that the case
  shows - server and form alike - but not against a hidden older date nobody
  can edit. A server refusal stays beside its field only while that answer,
  and what it was checked against, is unchanged (`CHECKED_AGAINST`); one for a
  field that is not on screen is listed in the alert at the top.
- **Deliberately optional**: middle name (a foundling or a non-marital child
  may have none), legal status (none issued yet), landmark, date found. Asking
  for something that does not exist gets "N/A" typed into it.
- **`middle_initial` was renamed `middle_name`** (children 0020), not dropped:
  the initials already recorded are kept. The display name still uses an
  initial (`middle_initial_of`), and a value already written as one ("DC") is
  kept as written so no existing name changes shape on its next save.
- **Renamed values were migrated** (children 0021): Orphan -> Orphaned, N/A ->
  Unknown, and the demo seeder's misspelt "Stepparent" -> "Step-parent".
  **Retired values were not**: the seeder's "Domestic"/"Relative" stay on the
  records that hold them, shown as "(no longer offered)". (Birth status
  "Child", SIBRA and ICA Relative were retired the same way on 24 Sep and are
  offered again since 7 Oct - see the SCSR bullet below.) The serializer accepts them unchanged and refuses
  them as a new pick - the same change-only rule as the old categories. The
  record's own value stays in its list for the whole edit (`withRetired` is
  passed `form._record`'s), so a different pick can be taken back; it used to
  vanish with no way back but discarding the edit.
  `import_demo_data` upgrades an older fixture the same way before loading it.
- "Street Number" is the owner's wording; its hint allows a purok or sitio,
  because most addresses in the region have no street.
- **The Custodian is typed, not picked**: who the child lives with now, on the
  Present Environment step. It was "Previous Custodian" on Child's Profile
  until 29 Sep 2026 - see "The custodian and their texts" below. Still
  required where the case type asks it.
- **Educational Placement moved to Child's Profile and is required** there
  ("Not in school" is an answer); **Referral Source is a pick** from RACCO /
  LGU / CCA / RCF (children 0023), with typed text on older records kept by
  the same change-only rule. Current Whereabouts left the form on 24 Sep and
  came back on 7 Oct (below).
- **Part I of the Social Case Study Report is the model for the form**
  (owner, 7 Oct 2026; the blank NACC template is
  `docs/agency-forms/SCSR_Non-Relative_Regular_Placement.docx`). The SCSR is
  the adoption case report written after the PAPA or supervised trial
  custody; only its Part I (Identifying Information) is intake. Parts II-V
  (background, family, the prospective adoptive parents, placement,
  assessment) are a separate module still to be designed, not form fields.
  - Birth status "Child" and adoption types SIBRA and ICA Relative are
    offered again, in the SCSR's order, with "Relative (Without 2-yr
    custody)" kept and "Orphaned" kept as written (owner's choice). Only a
    Regular adoption records the Date of Admission, so SIBRA and ICA
    Relative take the Date of Placement.
  - **Current Whereabouts** (`current_placement`) is back on Child's Profile,
    the record drawer and the child's page, and always asked.
  - **Health Condition** (Healthy / With special needs) is always asked;
    "Specify the special needs" is required only for special needs
    (`intake.required_fields(..., health_condition)`, mirrored in
    `requiredFields`), and the server blanks it for any other answer.
  - Both are new questions, so records from before keep their blanks through
    an unrelated edit, by the usual edit rule.
  - **Alias** is optional and shown only for the Without Known Parents
    category (the SCSR: "the given first and last name and alias"). Changing
    the category hides it, never deletes it; the server never clears it.
  - **Age follows the adoption type** (7 Oct 2026): 5-17 for every record
    except an Adoption of type Adult, which is 18 or older with no upper
    limit. One rule, `intake.age_range(case_type, type_of_adoption)`,
    mirrored in `caseData.js ageRange` and pinned by `TheAgeRuleTest`. It is
    judged where the birth date is set and again where the case type or
    adoption type changes (`ChildSerializer._check_age`); an unchanged birth
    date on an unrelated edit is not. The refusal sits on `birth_date`, and
    `CHECKED_AGAINST` clears it when either type changes.
  - **Legal Status has an optional "Date Issued"** (`legal_status_date`,
    children 0030; the SCSR asks for the CDCLAA issuance date). It is shown
    only once a status is picked, refused in the future or before the birth
    (moving the birth date past it is refused too), and cleared by the server
    when the status is blank.
  - The birth date reads "Date of Birth or Given Date of Birth", the SCSR's
    words, on the form and the printed reports.
  - Children 0029 is additive. `children/demo_profiles.py` gives demo
    children a health condition and whereabouts, from `seed_demo_data` and
    `import_demo_data` only.
- **A rename cannot ride along with a deploy the old release survives.** On
  24 Sep the demo API's first deploy of 0020 failed on Render's side after the
  web had gone live; had the migration run first, the old API - still serving
  - would have answered 500 on every child query, because it asks for
  `middle_initial`. Replayed on PostgreSQL 16 it does exactly that. A manual
  redeploy of the same commit went through in two minutes. Prefer additive
  migrations (add, copy, drop later); widening a column, as 0022 does, is safe.

## One child, one record

Found 9 Oct 2026: the owner pressed Save Record again while the first save
was still going, and the same child was added twice (C-0049 and C-0050), each
with its own request to the psychologist. Three layers now, because each
covers a hole the others leave:

- **One save at a time** (`savingRef` in `Children.jsx save()`): from the press
  until the confirm, the request, the referral upload and the end dialog are
  over; the button reads "Saving…". A literal double click never got through
  - the confirm dialog's backdrop takes the second click and cancels - the
  window was pressing Save again DURING the request. The draft autosave stands
  down while saving, or it rewrote the draft after a successful save and Add
  Record offered the child back. Other create buttons with no busy state got
  `useSingleFlight` (`utils/singleFlight.js`, a ref set before any await);
  leave was the one the server would not have refused.
- **The server recognises a submission it already saved**: each new-record
  form carries a `crypto.randomUUID()` (`Child.intake_token`, children 0031,
  unique), kept with the draft. A repeat answers 409 "already saved" with the
  id only if the requester can see it, and a near-simultaneous pair is settled
  by the unique column. Covers a lost response and a retry, not just a click.
- **The same child cannot be added twice by any route**
  (`children/duplicates.py`, POST only): same first name, last name AND birth
  date as any record, active or closed, whoever holds it. Names compared
  trimmed, case-folded and NFC-normalised in Python - SQLite's `iexact` folds
  ASCII only and would call PEÑA and Peña two children. First and last name
  alone are NOT enough: two children share common names. The refusals follow
  the duplicate check's disclosure rules (no id for another SW's record).
- **The ISA can remove a duplicate made by mistake**
  (`POST /children/<id>/remove-duplicate/`, "Remove duplicate record…" in the
  record drawer): both must match, the removed record's case number is typed
  to confirm, and the record must hold nothing but what Add Record makes - its
  referral files and any pending, withdrawn or declined request go with it (a
  psychologist shown two identical requests declines one as a duplicate, and
  a decline holds only a reason; only a still-pending one is notified); any
  appointment, clinical record, case study, accepted request, assigned
  psychologist or assistant job blocks it.
  Every model with a FK to Child is named in `KEEPS_IT` or `TAKEN_ALONG`, and
  a test reading `Child._meta.related_objects` fails on one that is not - a
  new relation must be decided, never deleted along by default. Children
  still have no delete otherwise.
- **A name with a replacement character (U+FFFD) is refused** at sign-up, the
  user form and a new record (`accounts/names.py`): a Latin-1 form-encoded body
  (curl, PowerShell 5.1) turns Ñ into `%D1`, which Django decodes to U+FFFD,
  and "PEÑAMORA" was stored as "PE�AMORA". A value a record already holds
  passes unchanged; the ISA corrects it from Users. The code paths themselves
  carry Ñ end to end (`accounts/tests/test_accented_names.py`).

## Each social worker's own records

Owner's decision, 24 Sep 2026: **each SW keeps their own records**, not one
shared list. `Child.social_worker` says whose a record is, and
`accounts/scoping.py` - the one rule nearly every endpoint already used - now
narrows Staff to it. Administrators (the ISA) see everything; psychologists
are unchanged (their assigned children).

- **The ISA adds records too.** Taking Add record away from the ISA was
  tried on 30 Sep 2026 and reverted the same day at the owner's request. An
  ISA's new record belongs to the social worker picked in its Social Worker
  field, or to nobody until one is assigned.
- **A new record is its creator's** (`ChildViewSet.perform_create`), whatever
  the request says. **Only the ISA moves one** (a closed case taken over at
  intake aside, below) - through the record form's Social
  Worker field, shown to the ISA only, or many at once through the console's
  Transfer records (below); a SW sending a different one gets 400,
  sending the same one is fine because the edit form resends everything. It
  must be a Staff account.
- **Existing records were backfilled** (children 0025): the uploader of the
  latest case referral if a SW, else whoever the activity log says created
  it if a SW, else nobody - and nobody means only the ISA sees it until it is
  assigned (Records' "No social worker yet" filter). An administrator's
  upload is never read as ownership. On the hosted demo every seeded referral
  was filed by one staff account, so that account received every demo child.
- **The doors that skipped the rule, now closed**, each with a test in
  `children/tests/test_own_records.py`: the child report page (checked
  psychologists only), the duplicate check, case-referral upload, survey
  invites, the two slot endpoints, booking/moving/cancelling a session, the
  activity feed, and report-check findings naming a child. A child-related
  query that is not built on `scope_to_visible` is the bug.
- **The duplicate check still searches every record** - a second record for
  the same child is the worse failure - but another SW's ACTIVE match says
  only that it exists and who holds it ("held by R. Santos - ask the ISA"):
  no id, no birth date, nothing from the record.
- **A CLOSED case found there can be reopened and taken over** (owner, 30 Sep
  2026): a returning child arrives at whoever runs intake that day. Add
  Record offers "Reopen it — it becomes yours" for another SW's (or nobody's)
  terminated case; `reopen` moves `social_worker` to the one reopening and
  tells the previous holder (an activity event addressed to them). Only by the
  name typed at intake - the request carries it and `_intake_match()`, the
  duplicate check's own rule, must match - so by id alone it is still a 404
  and nobody can walk the ids collecting closed cases. The ISA's reopen moves
  nobody. `children/tests/test_reopen_at_intake.py`.
- **Dashboard is their own; Agency Summary stays agency-wide** (the owner's
  choice): the Summary holds counts with no names and mirrors the agency's own
  report form. The assistant answers a SW about their own records.
- **Care gaps follow the role** (4 Oct 2026, `clinical/care_gaps.alerts_for`,
  used by the Dashboard, `list_care_gaps`, the one-child summary and the brief
  facts, so all four agree). A SW's: no case referral, no psychologist (a
  pending request counts as asked for 7 days, then is a gap again; declined
  or withdrawn is a gap at once), no signed consent, unread self-report
  answers, and the two booking gaps, because booking is a SW's job. Not the
  stalled pre-assessment or report due, which they cannot act on. Survey
  unanswered (link sent, unanswered 7+ days, newest link only,
  `SURVEY_UNANSWERED_DAYS`) was taken out on 4 Oct while a SW could not start
  a survey, and is back since 5 Oct, when they can (next bullet). A gap
  nobody can close is noise. Psychologists and the ISA keep `compute_alerts` unchanged; the ISA's
  consent and pre-assessment gaps link to the child's page, not the
  psychologist-only `/pre-assessment`.
- **A SW starts a QR survey from their own child's page** (5 Oct 2026). The
  self-report forms come from `GET /api/opinionnaire-invites/templates/?child=<id>`
  (`clinical/views.py survey_templates_for`), NOT `/form-templates/`, which
  stays Administrator/Psychologist-only because it shows every psychologist's
  private templates. The list: active Self-Report (Government Form) templates
  that are shared, the child's psychologist's, or the requester's own (the
  ISA: any). Id and title only, for a child the requester can see and write.
  Invite create refuses any template outside that same list, so the picker
  and the refusal cannot drift. With none set up, a SW is told to ask the
  ISA. `clinical/tests/test_survey_templates.py`.
- **The calendar still shows every session**, other SWs' children as "C-0042 ·
  Ref. E. Pascua" (`scheduling/visibility.py`): booking needs the
  psychologist's real day. A SW acts only on their own children's sessions;
  the server refuses the rest and the screen offers no buttons for them.
- `seed_demo_data` has a second SW (Rosa Santos) and `demo_owners.py` shares
  seeded children round-robin across the staff accounts present; the
  referral is filed by the child's own SW. A caseload with no SW is one no
  staff account can see.
- **Archiving a SW does not move their records.** The ISA transfers them; the
  Social Worker field lists an inactive holder as "(inactive)".
- **The ISA can move many records at once** (15 Oct 2026, `children/transfer.py`,
  `POST /api/children/transfer/`; the console's "Transfer records"). Body
  `{from_social_worker, to_social_worker, children: [ids]}`; Administrator
  only, 403 for anyone else before the body is read, 401 when not signed in.
  The one they leave is any Staff account, an archived one included (the usual
  reason); the one they go to must be an ACTIVE Staff account and a different
  one - the record form's "who may hold a record" rule. **Every id must be an
  ACTIVE record held by the one they leave.** A closed, missing, already moved
  or someone else's record refuses the WHOLE request and moves nothing: 409
  with `problems` (id, case number, why) when the list went stale, 400 when it
  is malformed (empty, over 1,000, not whole numbers, an id twice). The rows
  are locked (in id order, so two moves that share records take the locks the
  same way round), moved by one conditional UPDATE and the count re-checked, so
  a change in between rolls the move back rather than leaving part of it; the
  log lines go in inside the same transaction, so a move whose lines could not
  be written is not made. **A database that gives the transaction up because
  another got there first is the same 409**, not a 500: SQLite's "database is
  locked" (only where the connection does not start its transactions with
  `transaction_mode: IMMEDIATE` - the local copy's own `DATABASES` does, which
  makes the second move wait and then find the records gone, but a
  `DATABASE_URL=sqlite:///...` copy does not) and PostgreSQL's deadlock or
  serialization failure (`_lost_a_race`); any other `OperationalError` is still
  an error. Two overlapping moves at once: 16 rounds on PostgreSQL 16, 12 on the
  local copy and 15 on a plain SQLite copy were always one 200 and one 409,
  never a part-move. **Only `social_worker` changes** (and `updated_at`,
  bumped by hand because a bulk UPDATE skips `auto_now`: a record form opened
  before the move is told "updated by someone else"). The psychologist, an
  open request to one, the case study and the appointments stay; what a SW
  sees of them follows the field - case study access, names on the calendar,
  Records, and a psychologist's answer to a request, which goes to the child's
  current SW. **The trail has two layers** (the action `transferred`, activity
  0010, choices only): a RECORD line for each record, the audit trail ("Moved
  C-0042 · Ana Cruz from R. Santos to E. Pascua", the ISA the actor, nobody
  addressed, `kind` `move_detail`), and ONE sentence ("Moved 9 records from
  ...", the label the whole sentence as for exports) for the worker receiving
  the records and one for the worker who gave them up, unless that account
  cannot sign in. The feeds show the newest 50 lines and a leaver's whole
  caseload is the commonest move, so **`/api/activity/` leaves the per-record
  lines out for everyone** and the ISA's feed also drops the second sentence
  (`move_copy`, the same words again): a move of 60 records is one line in
  each bell, and the assignment answers are not pushed out. **The Users
  drawer** (the last 25 of what an account did, the ISA's own among them) keeps
  one sentence per move for the same reason. The per-record rows stay in the
  table, in no screen, for whoever asks who moved a child: "View full audit
  trail" on the rail goes to Users, whose drawer keeps the one sentence, so the
  ISA cannot read "who moved C-0042?" on screen today. That is a known gap,
  not an oversight. If the owner wants it answered, it belongs on the child's
  own page (a read of that child's `move_detail` lines), never in the feeds,
  which the same 60-record move would fill again. The actor's name is
  cut to its 150-character column like the labels: SQLite does not enforce a
  length, PostgreSQL refuses the insert, and a log line must not be what stops
  a move. `children/tests/test_transfer.py`, also run on PostgreSQL 16 - a
  length test that only ever ran on SQLite could not fail.

## Assigning a psychologist is asking

Owner's request, 28 Sep 2026. Picking a psychologist on a record (Add
Record, or an edit, by a SW or the ISA) writes an `AssignmentRequest`, and
`Child.assigned_psychologist` changes only when that psychologist accepts.
Rules in `children/assignment.py`; design in
`docs/superpowers/specs/2026-09-28-assignment-acceptance-design.md`.

- **Nothing but acceptance writes `assigned_psychologist` through the API.**
  Every scoping rule reads that field, which is the whole reason a pending
  child is absent from the psychologist's Records, Monitoring, calendar and
  assistant without any of them knowing about requests. A new door that sets
  the field directly skips the question; `test_a_pending_child_is_absent_from_every_door`
  is the check.
- **`psychologist` in an edit means who the child SHOULD be with**
  (`ChildViewSet.perform_update`): the one already asked changes nothing, the
  holder withdraws the open request, nobody withdraws and unassigns, anyone
  else is asked. The edit form therefore starts from the pending psychologist,
  not the holder. A psychologist's own edit resends themselves and is ignored,
  or it would withdraw the transfer the SW asked for.
- **One pending request per child**, a partial unique constraint; asking
  somebody else withdraws the first. Answering is a conditional UPDATE on
  `status='pending'`, so a second answer gets 409 saying what happened.
- **Decline needs a reason**, which only the ISA and SWs see
  (`declined_assignment` on the child serializer is null for psychologists).
- **The carry-history choice rides on the request** and is applied at
  acceptance — applied at the request it would change what the CURRENT
  psychologist sees before anyone agreed. During a transfer the child stays
  with the holder. **No edit writes it** (29 Sep 2026): `perform_update`
  drops it unless the edit asks somebody, and the form sends it only then.
  Until then an edit that ended on the holder saved it on the child - pick
  someone else, untick it, pick the holder back, and the holder lost the
  history with nobody asked.
- **Picking the holder back while a request is open withdraws it**, and the
  record form says so ("Request withdrawn … stays with …"). It used to be
  announced as asking the holder, because the form compared the pick with
  the pending psychologist rather than with who holds the child.
- **The Assignment step's availability panel is a week grid**
  (`pages/children/PsychologistPicker.jsx`, owner's request 30 Sep 2026): a
  row per psychologist, their caseload beside the name (amber from 5), a
  column per day with that day's windows in short 12-hour form
  (`shortRange()`: "8 AM-12 PM", "1-5 PM"), today's column marked, weekend
  columns only when somebody works them, one-off dates under the row. It
  replaced a card of "Mon 08:00-12:00" chips per person, which could not be
  compared across people without reading every chip. Each row is still the
  button that picks the psychologist.
- **The case referral chosen on the Assignment step stays in its box** when
  the step is left and reopened (each step unmounts), and has a Remove
  button; the box used to say "No file chosen" while the save still filed it.
- **After accepting, "From my availability"** reads
  `/api/availability/openings/`, built on `booking.bookable_slots`, so every
  time offered books; "Schedule now" books on the psychologist's own calendar.
  Both go through `POST /appointments/` unchanged, referral gate included.
- **Every step ends with an end dialog** (`useNotice()` in
  `context/ConfirmContext.jsx`, the confirm dialog without the way back) —
  the owner's "add end dialogue always". Confirm first, notice after.
- Email and SMS now say a case is *waiting for an answer*; still no name.
  Terminating a case withdraws its open request. Existing assignments were
  not migrated: children already assigned stay assigned.

## Closing a case: two reason lists

Owner's request, 28 Sep 2026 (`children/termination.py`; design in
`docs/superpowers/specs/2026-09-28-interview-upload-and-closure-reasons-design.md`).
The ISA closes for where the child went (`TerminationRecord.CASE_OUTCOMES`,
unchanged). A psychologist closes for where the clinical work ended
(`TerminationRecord.CLINICAL`), and each of those is offered only when the
record bears it out:

- **Counseling completed** needs a Session or Follow-up marked *completed* on
  the calendar. The Counseling stage alone is not a session held.
- **Favorable pre-assessment** and **Pre-assessment only** need a completed
  pre-assessment and no counseling at all (not in Counseling, no session held).
- **Counseling discontinued** needs counseling started (Counseling stage, or a
  session booked or held, cancelled ones aside). Referred elsewhere and Other
  are always open.
- **The dialog asks `/children/{id}/closure-reasons/` and the terminate
  endpoint refuses by the same function** — never add a reason to one side.
  `test_every_offer_marked_open_is_accepted` holds them together, and
  `caseData.js` keeps both lists (for the archive filter) pinned by a test.
- Neither role can use the other's list. Past closures keep what they were
  written with.
- **Terminating ends with an end dialog** (`useNotice`, owner 30 Sep 2026):
  "Case terminated", reading back the child and case number, the reason, the
  closing summary and the date - it was a toast, gone before anyone read it.

## Interview templates by upload

The Clinical interview step (pre-assessment step 3) has **Upload a template**:
a Word or PDF interview form is read by `clinical/form_import.py` (on top of
the report reader in `clinical/services.py`) into a DRAFT — headings become
sections, lines under them questions, `Label: ____` short text — and nothing
is saved until the psychologist reviews it and saves through the ordinary
`POST /form-templates/`, attestation included. Measured on the agency's own
`docs/agency-forms/Pre-assessment.docx`: 19 sections, 80 questions, both
questionnaires. Answers are keyed by question wording, so a repeated question
gets "(2)" rather than sharing an answer. `.doc` is refused with what to do.

## The custodian and their texts

Owner's decision, 29 Sep 2026: no previous custodian is held any more. The
record form's second step is **Present Environment** (was "Address"; the
address logic is unchanged) and asks who the child lives with now - the
**Custodian** - with a **Contact Number** beside it. Rules in
`children/custodian.py`, wording in `accounts/sms_notifications.py` section 4.

- **The field was renamed in code only**: `custodian_name` with
  `db_column="surrendered_by"`, a state-only migration (children 0028). A real
  column rename is the 0020 trap - a release still serving during the deploy
  would 500 on every child query. Keep the `db_column`.
- **0028 cleared the old pick-list values** (Social Worker, Police, Relatives):
  they said who SURRENDERED the child, never who they live with. Typed names
  were kept - the owner's word is they are mostly the present custodian.
  Nothing invents a custodian for a real record; demo data gets one from
  `children/demo_custodians.py`, called by `seed_demo_data` and
  `import_demo_data` only.
- **A custodian is texted only with consent AND a confirmed number**
  (`texts_allowed()`): the SW ticks that the custodian agreed (recorded with
  who and when), and confirms the number with a one-time code the custodian
  reads back (`/api/custodian-contact/code/`, SW and ISA only, same limits as
  a user's own number). A different custodian or number clears both unless
  given again in the same save; a confirmation counts only for whoever made
  it, within the hour. **Consent follows the person and number it was given
  for** (`_consentFor`): change either and it is off, put both back and it is
  on again - it used to go off for good on one stray keystroke, and the save
  withdrew it. A psychologist cannot change any of it, except that
  moving the case to a type that asks for no custodian takes the custodian -
  number, consent and all - with it, whoever moves it.
- **Five texts, no child's name**: booked, the day-before reminder (the
  existing `send_session_reminders` job, one `CustodianReminder` per
  appointment), moved, cancelled before it happens, and missed (a no-show
  recorded the same day only - "today's appointment" written up late would be
  wrong). The owner means to revise the wording: it is one table,
  `CUSTODIAN_TEXTS`, and `test_custodian.py` holds every entry to one GSM-7
  segment that does not start with TEST.
- **Demo custodians have no contact numbers**, on purpose: an invented mobile
  is somebody's real handset, and the hosted demo could text it. The import
  drops any number a local copy recorded, for the same reason.

## Names on the schedule

Owner's decision, 24 Sep 2026, in `scheduling/visibility.py`: on the calendar
a social worker sees the name of a child **in their own records** and every
other appointment as **"C-0042 · Ref. E. Pascua"** - the record's SW.
Administrators and psychologists see names. (It first followed whoever filed
the child's latest referral; the backfill made the two agree on the day it
switched to the record.)

- **The case reference is not decoration.** The literal request was "only the
  referring staff's name"; one worker's fifteen children would then be fifteen
  identical chips, and cancelling one would be a guess.
- **Applied where data leaves the server**, not in the screen: the
  appointments API (`child_name` is null, `name_hidden` true), the assistant's
  schedule answers, and the booking refusal that used to name the other child
  in the slot. A name the screen hides is still in the response.
- **Every screen that shows a schedule row goes through `scheduleName()`**
  (`utils/child.js`): the calendar, the Today card, the left rail's "Needs you
  today". The rail read `child_name` directly and showed staff a blank row for
  every masked child - `child_name` is null there by design, so a screen that
  reads it raw is the bug.

## Confirmations

- **Every save asks "Are you sure you want to proceed?"** through one hook,
  `useConfirm()` in `context/ConfirmContext.jsx`. Use it for any new write
  rather than another ConfirmDialog and `open` flag. Destructive actions keep
  their own dialogs, which ask for a reason or a typed name.
- It resolves `true` outside its provider on purpose - a save that silently
  never happens is worse than one that happens unasked. The provider wraps the
  router in `App.jsx`, so every screen, sign-up and the child survey are inside.
- The record form also asks before closing with unsaved input; a new record's
  draft is flushed and kept, an edit's changes are discarded only on "Discard".

## The header search (Ctrl K)

Owner's request, 11 Oct 2026: the box in the header finds AND does
(`components/GlobalSearch.jsx`, matching in `utils/search.js`).

- **A child is found by what intake remembers**: name in any word order,
  case number, custodian, barangay/municipality/province, birth year or date
  (2015, 2015-03-02, 03/02/2015), case type, psychologist, social worker.
  Accents and ñ do not count, "Ma." is also Maria. A match outside the name
  says where it was found. Never add medical notes, the referral reason or
  contact numbers to what is matched: the box finds a child, it does not read
  the file.
- **Only from `/children/`**, which the server already scopes - nothing here
  may widen what an account sees. Fetched each time the box opens, so a child
  added since is found.
- **Quick actions on a child** (right arrow, or the row's arrow): Open record,
  Book a session (only with a case referral), Upload (a referral for
  SW/ISA, a report for the child's psychologist), Edit record - through the
  links the screens already read (`?book=1&child=`, `?upload=1&child=`), plus
  Records' `?edit=<id>`, which opens the form only where `canEditRecord`
  allows. Same rules as the screens; the server checks again.
- **Before typing**: Recent (ids only in sessionStorage, names from the
  current list, cleared by logout - office computers are shared), Needs you
  (from the list already fetched, and `useCensus`'s pending counts), and the
  role's actions.
- **The header pill IS the input** (owner, 11 Oct 2026): no second box opens,
  and the results drop down under it. It was a button that opened a panel with
  its own field, so the first one looked like a box and acted like a link.
  The pill carries the focus ring (`.racco-chrome-search` in index.css),
  because index.css gives every focused input a small-radius ring that would
  square off the pill - white at 40%, since `--shadow-focus` is a pale blue
  that vanishes on the navy. Clicks inside the panel keep focus in the input
  (`mousedown` preventDefault), so the arrow keys keep working after a click.
  Closing for any reason - Esc, a choice, a click or Tab away, another header
  popover opening - clears the text.
- **A press anywhere on the pill opens it**, the Ctrl K chip and the icon
  included, also when the field kept focus with the panel shut (the browser's
  back button leaves it so). The click-outside listener reads `composedPath()`:
  the chip unmounts during its own mousedown, and `contains(e.target)` then
  called the press "outside" and shut the panel the same instant. Tabbing onto
  the bar does not open the panel - typing, the down arrow or Ctrl K do - so a
  keyboard pass through the header neither flashes the panel nor refetches the
  roster.
- **Tab and Shift+Tab are read on the input's keydown**, not as a blur: with
  nothing focusable before the bar, Shift+Tab leaves the page with a null
  `relatedTarget`, which a blur handler cannot tell from a click. Nothing in
  the panel is a tab stop - a row focused there answered neither Esc nor the
  arrows. The rows are `role="option"` divs under a `listbox`, the highlight
  is announced through `aria-activedescendant`, and axe-core is clean on the
  open panel; buttons inside the options fail `nested-interactive`.
- **The highlight follows the pointer only when it MOVES** (`pointerAt`). A
  list scrolling or refilling under a resting pointer fires `mouseenter` on
  whatever slid beneath it and used to pull the highlight off the row the keys
  had reached. The cursor clamp is a functional update for the same family of
  reason: from the foot of a long list, the next letter left the highlight on
  the new list's last row instead of its first.
- **Ctrl/Cmd+K is the plain chord only**, and does nothing while a modal
  drawer or dialog is up (`aria-modal`): the panel would open under the scrim
  and take the focus out of it. The header tabs give up width (36px at the
  least) below 1180 instead of running under the pill and the bell, where the
  first tab sat over the clear button.
- **The bar is an inch (96px) longer than it was** (owner, 12 Oct 2026;
  `searchBarWidth` in `LayoutContext.jsx`): 306 under 1180, 364 to 1399, 416
  from 1400, against 210 / 268 / 320. The header tabs shrink to make the room,
  down to their 36px minimum, and the results panel (340 / 430) stays wider than
  the bar. Only under 842 (the "More" button) is there not room for all of it:
  there the bar takes what is spare and never less than before, and the full
  inch is in from 842. It never gets shorter as the window grows.
  **The account's name and role wait for 1280 in the header** (`identityOn`, it
  was 1080): they cost 151px, and at 1080 they left no room for the inch, so the
  bar snapped 96px shorter as the window grew past 1079 and was barely longer
  than before until 1244. The rail's own account card names the user from 1080,
  so nothing goes unsaid, and 1280 also leaves the tabs their room for a
  26-character first name. Sized against the ISA, whose role label is the
  widest, with 30px in hand; a control added to the header means measuring again.
- **The pending count on a tab hangs off the icon's corner from the tab's OWN
  width** (`right: max(2px, calc(50% - 23px))`, `AppHeader.jsx`). It was worked
  out from `layout.tabWidth`, but the strip shrinks the tabs below that, and a
  badge placed for the wider tab lay over most of the icon. A percentage follows
  the real width; the 2px floor keeps even three digits inside a 36px tab.

## The ISA's admin console

Built 14 Oct 2026, at the top of the ISA's Dashboard (`pages/dashboard/AdminConsole.jsx`,
mounted for the Administrator alone). The ISA is the agency's IT support, not
the Head of Office, so it answers three questions in this order: who is waiting
on me, is anything broken, who has access to what. The figures, team, care gaps
and census stay underneath, as they were.

- **One request feeds all of it**: `GET /api/admin/overview/`
  (`accounts/admin_overview.py`, Administrator only: 403 for Staff and
  Psychologists, 401 when not signed in),
  made when the console opens and again on Refresh ("Last checked N min ago").
  Nobody else's Dashboard mounts it, so nobody else ever sends the request. A
  failed first load is an error line with "Try again", never a blank card; a
  failed refresh keeps what was on screen and says so. A re-read after a write
  (the footer's transfer and assistant switch, `load({ fresh: true })`) never
  reuses a request sent before it, because that reply may still carry the old
  picture, and an older answer never overwrites a newer one (each call takes the
  next sequence number and only the latest may write); a plain Refresh may
  still join a request that is already out. The only other calls are the three
  Test buttons and what the footer's actions do (below).
- **Where each queue's number comes from.** Counts are exact; the rows under a
  tile are cut to ten, longest-waiting first (lockouts, newest first).
  - *Access requests*: PENDING accounts. The hint says how long the oldest has
    waited and how many typed email addresses are still unconfirmed.
  - *Sign-in lockouts*: the line `LoginView._register_failure` writes, last 24
    hours. The lock itself lives in the per-process cache and nothing durable
    says who is locked right now, so the log is the only record. It counts
    lockout EVENTS: one email locked twice is two. The line is picked out by
    how it was written (SECURITY, action Updated, nobody signed in, no
    entity id, and the whole sentence), never by its first words alone: an
    access request is also written with nobody signed in, and its label opens
    with a name the applicant typed, which was once enough to add a lockout
    for "Login locked for ..." as a first name. The name shown is the typed one
    only if it is an email address (`readable_sign_in_name`, used when the line
    is written AND when it is read, for older lines); anything else, a password
    included, reads "an unrecognised sign-in name". "Is an address" means
    letters, digits and the few marks an address uses, an at sign and a dotted
    host of letters, digits and hyphens: the first version accepted anything
    around an @ and a dot, which kept "p@ss.word!" and "me@x.ph;DROP". A
    password that is shaped exactly like an address ("Summer2024@home.net")
    cannot be told from one and is still kept; that residual is accepted.
    Activity 0009 rewrote the lines written before this to the same rule (its
    own frozen copy of the pattern), so the audit feed, which shows the stored
    label, stops carrying them as well; it cannot be undone. **A lock on a whole
    address is its own line** (`lockout.ADDRESS_LOCK_LABEL`, written when
    `register_failure` reports "address" among the locks it set): after 20
    failures from one address, everyone behind it is refused for 15 minutes,
    the ISA included, and it used to be recorded as the lock of whichever name
    was typed twentieth. It names no address and no name, is picked out by its
    whole sentence, and counts here ("Every sign-in from one address"). The
    tile's hint names the latest lockout instead of repeating the count.
  - *Temp passwords unchanged*: active accounts still on `must_change_password`.
    The issue date is the newest create, reset or reactivation in the log, the
    last two found by `ActivityLog.kind`; one issued before they were
    told apart from an ordinary edit (`UserViewSet._log`) reads "issue date not
    recorded".
  - *Psychologist requests stuck*: a PENDING `AssignmentRequest` asked
    `ASSIGNMENT_ANSWER_DAYS` (7) or more days ago on an active child, plus
    active children with no psychologist whose latest request was declined.
  - *No social worker yet*: active children with no `social_worker`, which only
    the ISA can open.
  - *Held by an archived account*: active children whose social worker OR
    assigned psychologist has an account that is not active (archiving never
    moves records, and a psychologist's caseload strands exactly as a social
    worker's does: nine children once sat with an archived psychologist, in no
    queue, and the Workload total came out nine short). The row names which
    holder is gone; a child with both gone is one row.
  - *Names to fix*: a U+FFFD in an account's name, the Latin-1 form body
    problem under "One child, one record"; the ISA retypes it in Users. Child
    records are NOT listed: a record's name cannot be changed once it exists
    (`ChildSerializer.validate`, for everyone), so a row would open a form that
    cannot fix it and the queue could never empty.
  - *Possible duplicates*: groups of records with the same first name, last name
    and birth date, by the rule `children/duplicates.py` already refuses on. The
    row's Open searches Records for the LAST NAME: Records matches a plain
    substring of the full name, and two records of one child can differ in the
    middle name. The rule counts closed records, and Records opens on its Active
    tab, so the link also carries `status=all` (Children.jsx reads `status` as
    the page opens and whenever the address changes to one) and the row marks a
    closed record "(closed)". The last name is sent composed (NFC) with single
    spaces, and Records folds both sides the same way, so a name stored
    decomposed or with a doubled space is found as well.
  The rows are laid out here by queue key, four to a row, not by the server's
  `group`: that puts "stuck requests" with the records, and the approved picture
  has it with the people.
- **Sign-ins come from the activity log**, which has a line for every door;
  `last_login` was stamped by the Google door alone, so Users said "Never" for
  a colleague the console listed as signed in today. Since 15 Oct 2026 the
  password door stamps it too (`SIMPLE_JWT UPDATE_LAST_LOGIN`) and accounts
  0015 brought earlier sign-ins up to date from the log. "Signed in today" is
  today in `TIME_ZONE`; "dormant" is an account more than 30 days old with no
  sign-in line in the last 30 (never having signed in counts).
- **How the version is found.** The API's commit is `RENDER_GIT_COMMIT`, else
  the checkout's `.git`, else unknown; "up since" and the checkout's commit
  (`BOOT_COMMIT`) are taken when the module is imported
  (`AccountsConfig.ready()`), not at the first request. Read per request, the
  commit followed every `git commit` made while a development server kept
  running the old code, and the row said the two halves differed. The web build's
  is baked in by `vite.config.js` (`VITE_WEB_COMMIT`: `RENDER_GIT_COMMIT`, else
  `git rev-parse --short=7 HEAD`, else "unknown"). When both are known and
  differ the row turns amber, "The two halves differ - one is still deploying":
  the two services deploy independently off one push (see "A push is not a
  deploy"). An unknown commit proves nothing and never raises it.
- **Health rows**: server (a timed `SELECT 1`), version, text messages, email,
  assistant, file storage; each status is also named in words beside the title.
  SMS is "off" when `SMS_PROVIDER` is `console` (it is never unset). The row
  says "unconfirmed", not "refused": a refused reminder deletes its row so the
  next run retries, so no refusal count exists to show. **The Test buttons**:
  text messages asks the gateway to check the key and balance (spends nothing);
  email sends a REAL message, so it asks first, "Send a test email to <your
  address>?"; assistant asks `/api/assistant/model-health/`. The answer sits under
  the row, and "not set up" reads as a plain statement, not as a failure. A
  test that ran and FAILED turns its row to "Needs a look" (and the card's chip)
  until it is run again; one that could not run, a timeout or a 403, proves
  nothing and leaves the row alone. A Test button is `aria-disabled` while it
  runs, never `disabled`, or the keyboard focus falls to the top of the page.
- **Workload** lists a deactivated psychologist or social worker for as long as
  active children are still assigned to them, marked "(inactive)" in amber, so
  the "children in care" total counts everyone the records say is in care. Both
  lists run heaviest first (a psychologist's open requests count with the load,
  as the bar draws it), then by name.
- **A role change is one line in the log**, not two (`UserViewSet.perform_update`):
  the serializer writes the line that names both roles, and the ordinary
  "edited the account" line is written only when something besides the role
  changed in the same save. The edit form resends every field, so this
  compares what the save changed rather than what it sent.
- **Times**: queue rows and health lines ("Locked today at 3:37 PM", "newest file
  Sep 30 at 3:37 AM") are worded by the server in `TIME_ZONE`, as all server
  prose is (see "Times on screen"); the security log, "up since" and "Signed in
  today" are formatted by the browser. In the Philippines they agree; a browser
  in another zone shows the two sets in its own and the agency's clock. Left as
  it is, because the agency works in one zone.
- **The screen**: a queue's rows open in place under the ROW of tiles the tapped
  tile is in (two to a row on a narrow pane, so not always under the whole
  group), right after it in the tab order; Escape closes the list and returns
  the focus to its tile. Refresh and Try again are `aria-disabled` while they
  load, never `disabled`, for the Test buttons' reason. While the first answer
  is awaited the four cards below the queues are placeholders of about their
  height, so the figures and the care gaps do not start at the top and jump a
  screenful when it arrives. Tiles go four to a row from a 680 px pane and the
  card pairs side by side from 740 (a 1280 window has a 692 px pane, a 1366 one
  778; the owner's pictures are 1440 and 1100, which are unchanged). The
  "Review access" button shows its count only when somebody is waiting.
- **The footer holds every admin action** (owner, 15 Oct 2026: it carried only
  three, and he named the rest; `pages/dashboard/ConsoleActions.jsx`). Accounts:
  Review access (with its count only while somebody waits), Add user, Reset a
  password, Transfer records. System: Send test text, Assistant: On/Off, Agency
  details, Settings. Outlined small buttons that wrap in whatever pane there is
  (checked at 1440, 1280, 1100, 820, 390 and 320; the group labels sit level
  with the FIRST row of their buttons, not centred on the group, so at 1280,
  where each group wraps onto a second line, "Accounts" and "System" stay with
  the line they name), **all eight outlined** as in
  the approved picture - Review access too, whose count is what says somebody
  is waiting (it used to turn solid blue with one). Drawn once the overview has
  answered, and also when it FAILED - a console that failed to load is when Add
  user and Settings are still wanted - but not while the first answer is
  awaited: the placeholder tiles are shorter than the real ones (a real tile is
  96px with one-line text and 129px with the two-line label and hint that an
  all-clear agency or a phone gives), so a footer drawn at once slid tens of
  pixels down under the pointer as the tiles arrived, and a press aimed at one
  button landed on another thing. Five go to another page (Review access, Add
  user, Agency details, Settings, and Reset a password once it has been told
  whose); Transfer records, Send test text and Assistant finish here. One that
  is working is `aria-disabled`, never `disabled`, for the Test buttons' reason.
  - *Reset a password* opens a picker (the shared `Modal`) of ACTIVE accounts
    other than the ISA's own - the new password would end their own session -
    from `GET /users/`; the search box has the focus, the arrow keys move
    between rows. Under 420px the role drops under the email
    (`.racco-pick-role`, `!important` because the element's inline style says
    flex none and right-aligned, which a plain rule cannot beat): beside the
    name, at 320, it left the name a few letters wide and a row 180px tall.
    Picking one goes to `/users?reset=<id>`, and that is all the
    console does: Users asks the same question the drawer's Temporary password
    button asks and shows the one-time password in its own dialog. **The
    password is never on the console.**
  - *Transfer records* is a `Drawer` over `POST /api/children/transfer/` (see
    "Each social worker's own records"). From lists the social workers holding
    an active record, a deactivated account first and marked "(inactive)", with
    their count; To lists ACTIVE social workers other than From; the checklist
    of From's active records is all ticked until the ISA unticks ("Select all /
    none"). Both lists are read when the drawer opens (`GET /users/` and
    `GET /children/`, which the ISA already may), not taken from the overview:
    the overview's workload is as of the last check, and the checklist needs
    the rows anyway, so the counts beside the names are counted from the very
    list that is ticked and cannot disagree with it. Every count the drawer
    prints has its thousands separator (`num()`: "1,200", not "1200" in one
    place and "1,200" in the next). The closed From select holds about 26
    characters at 320px, so a long name still clips its "records"; the count
    is repeated in the checklist header below it and the open list is whole,
    which is why the line was shortened ("active" dropped) and not given a
    second row, which a native select cannot do. "Move N records" asks
    first, then an end dialog reads back how many moved and to whom; the
    console is read again (the archived-holder tile and Workload follow) and so
    is the activity feed. A 409 shows the server's sentence, reads the list again
    and keeps ticked what is still the holder's to move; nothing moved. A 400
    reads it again too: the receiver may have been deactivated since the drawer
    opened, and without it that worker stayed picked and offered, and every
    retry said the same thing. The refusal is scrolled into view
    (`scrollIntoView`): it is drawn at the top of the same scrolling box as the
    checklist, and with the box scrolled it was off screen while the list
    quietly lost a row. **One move takes at most 1,000 records** (the server's
    rule): for a holder with more, the first 1,000 are ticked, Select all means
    those, the rest cannot be ticked, and a line says to open it again for the
    rest. The fields lock only once the request is out, not while the question
    is up.
  - *Send test text* asks first (it costs a credit), then calls the same
    `POST /sms-test/` as Settings; the answer is the end dialog. With no gateway
    (`SMS_PROVIDER` console, the local copy) the server logs the text and answers
    ok, so the dialog says "written to the log", not "sent". An ISA with no
    verified number is told so and offered "Open my profile" (`/profile`).
  - *Assistant: On/Off* reads the overview's `assistant` health row (status
    "off" is off; unknown until the overview answers - which, since the footer
    is drawn only once there are tiles or an error, means the load FAILED - and
    the button then says "on/off", is `aria-disabled` and, when pressed, says in
    a toast that the console has not loaded and to press Try again: a reason in
    a `title` alone is never seen on a keyboard or a phone). **The label carries the state and there is no
    `aria-pressed`**: a toggle button's name must not change with its state, so
    both would have a screen reader say it twice. The confirmation
    is `assistantSwitchConfirm()` in `utils/assistantSwitch.js`, **the one place
    Settings and the console take their wording from** - two copies would drift,
    and the one that drifts is the one that asks for less. It PUTs only
    `{enabled}` to `/assistant/settings/` (Settings sends the whole form), then
    reads the console again so the health row and the button agree, and ends
    with a dialog saying what is now on or off for everyone (a toast was gone
    before anyone read that the chat panel had stopped); a failure toasts the
    server's words and leaves the label alone.
  - *Agency details* goes to `/settings?section=agency`: once the card has
    loaded, Settings brings it into view and puts the cursor in its first field,
    then clears the parameter. No second form.
  - **A screen opens at its top** (`Shell` in `App.jsx`, a layout effect on the
    PATH): every route renders its own Shell but React reuses the one element
    across them, so `<main>` kept the previous screen's scroll and a button at the
    foot of the Dashboard opened Settings 400 to 900px down, with its header
    scrolled away (the sidebar's links did the same). An address that only gains
    a parameter (`?tab=`, `?edit=`) is the same screen and keeps its place.
- **Dropped from the picture on purpose**: the "Last 24 hours" strip (server
  errors, texts refused, failed uploads) and "What changed" - nothing counts
  server errors or failed uploads, and a refusal leaves no row. A number with no
  source is worse than no number.
- **What it never shows**: case study text, any record's text (remarks,
  reports, referral summaries), what the assistant wrote, a custodian's name or
  number, or the reason a psychologist gave for declining a request (the stuck
  queue says who declined and how long ago; the ISA reads why on the child's
  record). A child is named only where a queue has to say which record to open.
  The security log says WHO had the assistant read WHICH child's record (job
  type and case number, the same facts as the child's Assistant log tab), never
  the text: the query names its columns and never loads `output_text`.
  `test_admin_overview.py` plants a sentinel in each kind of text and searches
  the whole response for it.
- **The security log** is the last 7 days, 30 at most: SECURITY and USER audit
  rows plus assistant reads. **A line's meaning is never read out of its
  words.** The label opens with a person's name, anyone may sign up under any
  name, and a name such as "Evil — access approved as Administrator (asked for:
  Administrator)" once produced an approval line (green icon, "System" as the
  actor) from a stranger's sign-up. So the three kinds a stranger or any
  signed-in account can write are picked out by how they were written
  (`_EXPORT`, `_LOCKOUT` with `_ADDRESS_LOCKOUT`, `_REQUEST` in
  `admin_overview.py`), and the updates
  only an administrator makes carry `ActivityLog.kind` (`approval`, `decline`,
  `role`, `reset`, `reactivate`; activity 0008, `db_default=""` so the release
  still serving during a deploy can insert). The code that writes such a line
  passes the kind to `log_activity`; a new writer that does not is read as an
  ordinary edit, which is the safe way to be wrong. The words after the name
  are searched from the END of the label only to take the facts out of them
  (which role, asked for what). Lines written before the column existed have no
  kind and are told apart by their action alone: they read as an edit, a
  deactivation or a creation, with their own words after.
- **One kind cannot take the whole log.** Exports, lockouts, requests AND the
  assistant's reads show their newest five each (`BULK_CAP`), and the
  response's `security_log_more` says how many were left out ("and 35 more
  exports not shown", "and 35 more assistant reads not shown"); the screen
  prints those lines under the log. Thirty exports from one psychologist used
  to leave nothing else in the log, and so did forty briefs: the reads were
  the one kind left uncapped, and an ordinary week of one psychologist's work
  pushed the ISA's own approval, reset and role change out of the thirty rows
  with nothing to say so. A survey checked answer by answer is one line, and is
  counted as one. When the thirty-line cut itself drops anything, the log says
  "and N older events not shown" (kind `older`), so a quiet log is never
  mistaken for a complete one. The export note is also throttled per account
  (`exports`, `EXPORT_LOG_RATE`, 20 an hour; off under test, patched in by the
  test that needs it). A job type added to `ACCESS_LOGGED_JOB_TYPES` needs a
  sentence in `_READ_WORDS`; one without still shows as "read a record", and
  `test_every_logged_job_type_has_its_words` fails until it has its own.
- **Exports are logged by the screen, and that is a log of honest use, not a
  control.** The Records list and the result entries are built in the browser
  from the rows already on screen, so the server cannot know a file was made
  unless the browser says so: after the file is saved it fires
  `POST /api/children/exported/` (`{rows}`, and `what: "results"` from Results
  & Reports), fire and forget, never blocking the export. Anyone can skip the call
  or copy the list off the screen. `what` is a closed list, so a caller cannot
  write its own sentence into the trail, and `rows` is a whole number up to a
  million (as digits, at most seven of them: a longer string made Python raise
  and the endpoint answer 500). The Agency Summary's CSV is built by the
  server, counts only, and is not logged. The new activity action `exported`
  (activity 0007) is in `utils/activity.js` and the Users drawer; without it a
  feed would read "Archived child Exported the Records list".
- **Deep links it needs** (Users.jsx, the `useOpenFromLink` pattern):
  `/users?edit=<id>` opens that account's drawer once the directory has
  loaded, `/users?new=1` opens the add-user form. Both clear the parameter;
  an id that is not in the directory says so in a toast, once. A blank `edit=`
  is cleared, and a link that asks for both gets the add form with `edit`
  dropped in the same change to the address (left behind it opened that
  account's drawer when the form was closed, or on a reload).
  `/children?edit=<id>`, `/children?q=` and `/users?tab=requests` already
  existed. Two more for the footer: **`/users?reset=<id>`** asks the existing
  reset confirmation (`setConfirm({kind: 'reset'})`, the drawer's own path) once
  the directory has loaded, clears the parameter, and says so in a toast, once,
  for an id that is not there or an account that is not active (the server
  refuses any but an active one; the signed-in ISA's OWN id is accepted, as the
  Users drawer's Temporary password button and the row menu accept it - the
  confirmation says the current password stops working, and only the console's
  picker leaves the ISA out); `new` and `edit` outrank it and drop it from
  the address with them, because two effects each rewriting the address from
  what they read would put back what the other removed. **`/settings?section=agency`**
  scrolls to the agency card and focuses its first field when it has loaded
  (`useOpenFromLink`, so it is used up and does not fire again on a reload).

## Email notifications

Owner's request, 21 Oct 2026: each user gets their own emails, starting with
the psychologist asked to take a child. Design and what was settled in review:
`docs/superpowers/specs/2026-10-21-email-notifications-design.md`.

- **The "Google notification" the owner meant is the existing Brevo sender**
  (`children/notifications.py _post`, `BREVO_API_KEY`, from
  `racco1nacc@gmail.com`). It was what mailed temporary passwords; nothing new
  was added to send. Sending from each user's own Gmail would need Google's
  restricted `gmail.send` scope and the live OAuth app changed.
- **One catalogue, `KINDS` in `accounts/email_notifications.py`**: psychologist
  `case_request`, `request_withdrawn`, `my_calendar`; SW `case_accepted`,
  `case_declined`, `records_transferred` (the console's Transfer records only,
  not a single Social Worker pick), `case_taken_over`, `child_sessions`. The
  ISA gets none yet; the design note lists the suggested ones. **A key is never
  renamed**: opt-outs are stored by key, and the test pins the list.
- **Fired from the domain functions, never from `log_activity`.** The bell
  addresses lines to the psychologist even for their own actions and every
  line names the child.
- **No child's name, ever**, in a subject, body, text part or invite: the case
  reference (`case_ref`), session times, staff names and counts only. People
  are named through `accounts.display.entered_name`, never `display_name`:
  `username` is the email address, so a nameless colleague would have read as
  their address in someone else's subject line. `entered_name` also makes the
  name one plain line of at most 60 characters (no control, bidi or zero-width
  characters, no newlines): a colleague's typed name lands in genuine agency
  mail. The privacy tests plant a token in every free-text Child column
  (`children/tests/payloads.planted_text`).
- **Links come from `SITE_URL`** (23 Oct 2026), the address people actually
  open NACC SYS at, checked by `config/guards.checked_site_url` (http/https,
  a host, no user/password, query, fragment or path; https unless DEBUG; a bad
  value is ignored with a warning). Unset, `sign_in_url()` falls back to the
  first non-localhost CORS origin, as before - on the local copy that is
  `localhost`, a link that opens only on the host PC. **Never from the
  request's Host header**: that is password-reset poisoning. Forced to '' under
  `manage.py test` so a developer's .env cannot change pinned links. A button
  path must start with `/` or `render()` refuses it.
- **Every notification email shows the site address in plain text under the
  button, and a safety line**: "NACC SYS never asks for your password or a code
  by email, and nothing in this email signs you in. Not sure it is from us? Do
  not use the button: open NACC SYS from your bookmark ...". It deliberately
  does NOT say "check the address above": a clone can print the same address.
  **Never add a link that signs someone in** (forwarded mail, link scanners,
  tokens in history). `case_request` has its own fuller layout (an "Action
  needed" chip, greeting, fact card, three steps naming "Waiting for your
  answer").
- **Never for your own action**, except the psychologist's "Calendar update":
  moving or cancelling a session they hold an invite for re-sends the invite,
  or Google Calendar keeps the old time.
- **Session emails carry an `.ics` invite** (`scheduling/calendar_invite.py`,
  `scheduling/session_mail.py`), the psychologist's only. `CalendarInvite`
  counts SEQUENCE per (appointment, attendee), under a row lock, and only when
  the email is really queued. **The event UID is stored** (`CalendarInvite.uid`,
  scheduling 0007): drawn at random for an appointment's first invite, under
  the appointment's row lock, and reused for every later invite to anyone, so
  changing `SITE_URL` or CORS never gives a session a second event. A row from
  before the column keeps the old CORS-derived value (`legacy_uid`). **Not yet seen in a real Gmail inbox** - this was
  built with no Brevo key. Book a session as a SW with the key set and check the
  psychologist's Gmail shows an invitation before relying on it. A 400 from
  Brevo for a message with an attachment is sent again without it.
- **"Accepted" and the first session are ONE email** (owner, 24 Oct 2026).
  Accepting no longer mails the social worker; the acceptance is owed
  (`AssignmentRequest.answer_mailed_at` NULL, children 0032) until the first
  of: the psychologist books (one `case_accepted` email, "... and booked the
  first session" with its time, and no "booked" email for that booking - only
  when it is their first non-cancelled session with the child); the
  "When will you see them first?" dialog ends without a booking (Book later,
  Finish, X, Escape, or `pagehide` with `fetch keepalive`) and calls
  `POST /api/assignment-requests/<id>/mail-answer/`; or the sweep
  (`mail_overdue_answers`), 10 minutes after the acceptance
  (`ANSWER_MAIL_GRACE`), run by `GET /api/activity/` at most once a minute per
  process and ten at a time, and by `send_session_reminders` (fifty, then it
  waits for the send threads). One conditional UPDATE claims it, so the three
  can race and the SW still gets one email. A superseded acceptance (the child
  moved on, or a later one accepted) is dropped, a closed case says so, and a
  send that fails gives the claim back. The dialog ignores its close buttons
  while a booking is in flight; leaving the page by Back leaves it to the
  sweep (StrictMode runs unmount cleanups, so sending there would mail at
  once). A request accepted by the release still serving during the deploy
  that adds 0032 can be mailed twice; accepted.
- **Each person's switches** are `EmailOptOut` rows (a row means OFF, so a new
  kind starts on), at `GET/PUT /api/auth/me/email-notifications/`, shown on My
  Profile under the mobile number. Only the caller's role's keys are accepted.
- **One account can cause only so many of them** (25 Oct 2026, design note
  "Round four"): counted per ACTOR (the `by` of the domain function, passed to
  `notify(..., by=)` by every caller - a test reads the source for a call that
  leaves it out) in `EmailSendRecord` rows: 20 an hour to one colleague, 60 an
  hour in all, 150 in 24 hours (`EMAIL_LIMIT_PER_RECIPIENT_HOUR`,
  `EMAIL_LIMIT_PER_HOUR`, `EMAIL_LIMIT_PER_DAY`; 0 = off; all off under
  `manage.py test`, the tests of the limit switch them on). A table and not the
  cache: each gunicorn worker has its own cache. Past a limit the email is HELD
  - not built, `attachment` not called, so no invite SEQUENCE moves, and never
  tried again later, an acceptance included (`claim_answer_mail` stands; the
  social worker has the notice) - and the colleague gets a "More updates are
  waiting" notice (no case, no kind of change; not counted by the limits): at
  most one an hour and `OVERFLOW_PER_DAY` (3) in 24 hours per actor and
  colleague, because the daily limit can hold an account for most of a day and
  a notice every hour was 428 emails a day from one account measured, not 150.
  So what one account can cause in 24 hours is 150 plus three notices to each
  colleague it reaches. The ISA's security log gets one `email_limit` line an
  hour per account (a bulk kind there, newest five shown; picked out by
  `ActivityLog.kind`, not by its words); the ISA's bell leaves it out
  (`/api/activity/`), or a held account would fill its 50 lines. Hourly holds
  lift within the hour, the daily one can take up to a day, and neither the
  notice nor the line promises the hour. The "once" is checked, not locked: two
  held emails in the same instant can each send a notice, as two can each pass
  the limit. Rows are pruned at 25 hours by the once-a-minute sweep and
  `send_session_reminders`.
- **With no `BREVO_API_KEY`** (the local copy) each email is one WARNING line,
  `EMAIL (console) <kind> to <address>: <subject>` (`EMAIL (held: limit) ...`
  when the limit holds it, so the limit can be watched working locally; console
  lines are counted for it), and no CalendarInvite is recorded - so the
  Calendar update cannot happen locally.
- **A link in an email opens a signed-out tab** (tokens are per tab), so
  `ProtectedRoute` passes `state.from` and `Login` returns there after any
  sign-in (`returnPath`: a single leading `/`, never `/login`).
- **Left open for the owner**: a session PATCHed onto another child (API only)
  sends nothing; the footer's "No case details are included" is stronger than
  a session's date and time; dark mode is unchecked on a real phone. **The sender is the bigger risk**: gmail.com sent
  through Brevo cannot pass DMARC, so it is spoofable and spam-prone - send
  from an agency-controlled domain with SPF/DKIM/DMARC. Brevo also rewrites
  links for click tracking (no API switch; support only).

## Text messages (Semaphore)

Audited 26 Sep 2026 when the owner chose Semaphore. `SMS_PROVIDER` picks the
gateway in `accounts/sms.py`; unset writes every message, verification codes
included, to the API log. Local setup: docs/LOCAL-SETUP.md "Text messages".
Since 29 Sep 2026 a child's custodian is texted too - the only recipient who
is not a system user; see "The custodian and their texts".

- **Only a `message_id` counts as sent.** Semaphore puts refusals in 200
  bodies in several shapes (`{"field": ["reason"]}`, a list of sentences,
  `[]`), and the old reader called every one of them sent.
  `test_sms_semaphore.py` pins each; it is the only gateway that had none.
- **Semaphore silently discards a message that begins with TEST** - accepted,
  never sent, nothing said. The sender refuses one, and a test reads every
  message the system sends for it.
- **One segment means GSM-7.** One character outside it (an em dash, a curly
  quote) makes a segment 70 characters, not 160, and the text costs three
  credits. The trim used to append "…", which did exactly that. Measure with
  `fits_one_segment()`, never `len()`.
- **Verification codes go by Semaphore's OTP route**: `send_sms(...,
  otp_code=)` with `{otp}` in the text, 2 credits, never trimmed, once a
  minute and five an hour per account. Skipped while `SMS_ENDPOINT` is set.
- **Nothing SMS keeps state in the cache.** The cache is per-process LocMem.
  The session reminder records who was told in `SessionReminder`: the
  management command is a fresh process each run, so a cache record was
  always empty, and a `queue_sms` daemon thread dies when the command exits
  (`queue_sms` is for request threads in a long-lived server only). The
  phone code and its resend limits are a `PhoneVerification` row: under
  gunicorn each worker had its own cache, so a code stored by one worker was
  "expired" to the next and the limits multiplied by the worker count.
- `send_session_reminders` exits non-zero when any reminder was refused.
  The sign-up email code moved to a database row for the same reason on
  4 Oct 2026 (see "Getting an account").
- **"No active sender name found" does not mean the account has none.** The
  owner's live account, sender name approved, answered with it under HTTP 500
  on 1 Oct 2026. It means the name SENT is not an Active name on the key's
  account: unset, misspelt (exact match, capitals included), still pending,
  or another account's key. Check the key reads `account/sendernames` and
  says which; a sender error at any HTTP status gets that hint.
- **The ISA has a profile page too** (1 Oct 2026). The Settings test text
  goes to the caller's own verified number, and `/profile` used to admit
  only Staff and Psychologists, so an administrator could never verify one
  and the button could never work. The ISA sees the personal column only:
  the right-hand column reads `/children/`, `/appointments/` and
  `/activity/`, which for an administrator are the whole agency's.

## Role names on screen

Administrator shows as **"ISA (Administrator)"** and Staff as **"SW (Staff)"**
(24 Sep 2026), through `roleLabel()` in `ui/index.jsx`. Display only: the
stored `role_name`, every permission check and every API answer still say
"Administrator" and "Staff". Never compare anything against the label.

## Times on screen: the 12-hour clock

Owner's request, 29 Sep 2026: no military time anywhere. Every time a person
reads says "9:30 AM", never "09:30".

- **Screens go through `clock()` / `clockRange()`** in `utils/time.js`, built
  by hand. `toLocaleTimeString` lets the browser's region pick the clock, so
  an en-GB machine printed 14:05 - and with `hour12: true`, "2:05 pm".
- **A time is TYPED: a box that says HH:MM, and AM/PM in a dropdown beside
  it** (`TimeInput` in `ui/index.jsx`; availability From/To and "Schedule
  now"). The owner's design, 30 Sep 2026, after a 15-minute list, the
  browser's time box and two pickers were each tried and turned down. The
  typing rules are `typeTime()` in `utils/time.js`: the colon is put in, never
  typed; the hour stops at 12 (a 13th hour's digit is not taken); after 10,
  11 or 12 the next digit starts the minutes; a space moves on after a
  one-digit hour, so "3 00" is 03:00 and "12 30" is 12:30; minutes stop at
  59. Leaving the box or pressing Enter finishes it ("3" -> 03:00). No
  `<input type="time">` anywhere. It still reads and writes "HH:MM", and
  sends '' while the box or AM/PM is unfinished.
- **The AM/PM arrow carries `zIndex: 2`.** index.css lifts every
  `:focus-visible` control to `position: relative; z-index: 1`, which paints
  a focused select over an arrow drawn beside it - the shared `Select`'s own
  arrow vanishes the same way.
- **The calendar's formats are pinned** (`CAL_FORMATS` in `Schedule.jsx`); the
  localizer's defaults ask the locale.
- **Server prose goes through `config/clock.py`**: booking refusals, the
  availability overlap, the assistant's schedule answers, Monitoring's next
  session, custodian texts. `scheduling/tests/test_twelve_hour_clock.py`.
- **Data stays "HH:MM".** A slot's `start`, `availability_today`, the Today
  strip's `time`: the screen sends them back when booking and compares them as
  strings, so it formats them, and the API does not.

## The demo deployment

Built 27 Aug 2026. Public, free, fictional children, real accounts. Runbook in
`docs/CLOUD-DEPLOYMENT.md` §11-12; design in
`docs/superpowers/specs/2026-08-27-free-secure-web-deployment-design.md`.

- **It builds from `NACC-SYS-V3.1-LOCAL-VER`, branch `cloud-setup`.** Services
  are `nacc-v3-demo-*`; the live ones are `nacc-v3-api`/`nacc-v3-web`, built
  from `NACC-SYS-V3`. Read off the API service's own header in the Render
  dashboard on 23 Sep 2026 — `cloud-setup` → `79466d7`, Live — which is the
  only authority for this. The web service was not checked; do not assume it
  matches. `main` is not what the API watches: a push there built nothing.
  (This bullet used to name REMOTES — "from `local-ver`, never `origin`" —
  the framing the 20 Sep correction above disowned.) Whether a push actually
  reaches the demo is still a question with an answer rather than an
  assumption — see "A push is not a deploy" above.
- **GitHub’s deployment records lie about the branch and the commit.** Render
  writes a row per build to `api.github.com/repos/<owner>/<repo>/deployments`,
  but with `ref=main` and an environment named `main - nacc-v3-demo-*`, stale
  from whenever that name was set — and GitHub fills in `sha` by resolving
  that ref, so every row shows `main`’s head at that moment, not what Render
  built. On 23 Sep a session read 100 of these as proof the demo builds from
  `main`, wrote it into this file, pushed to `main`, then spent half an hour
  diagnosing a "missing" deploy. Lined up against commit times, each row had
  landed about five seconds after a `cloud-setup` commit. The timestamp and the
  success status are real; the branch and the sha are not. Same lesson as the
  axe-core note under Accessibility: an instrument less reliable than what it
  measures is worse than none.
- **The database is a Neon BRANCH** named `demo`, off a default branch called
  **`production`** (not `main`). It exists so the demo inherits the real
  accounts while its writes — and this repo's newer migrations — never reach
  production.
- **Three settings broke it, all silently.** `DATABASE_URL` unset fell back to
  SQLite on the container disk while `/healthz/` still said "ok";
  `VITE_API_BASE_URL` unset baked `localhost` into the bundle; and
  `CORS_ALLOWED_ORIGINS` unset blocked every browser request while `curl`
  worked. The first is now fatal at boot, `/healthz/` names the engine and
  host, and the third warns.
- **The Dockerfile needs those values too.** `collectstatic` runs with
  `DJANGO_DEBUG=False`, so the boot guard fires during the image build; the
  build step passes throwaways, and a test asserts it still does.
- **A hosted model needs `ASSISTANT_ALLOW_HOSTED_MODEL=true` as well as
  credentials.** Credentials alone are not consent, and the live blueprint sets
  none of these.
- **The model must return structured `tool_calls`.**
  `@cf/qwen/qwen3-30b-a3b-fp8` returns them as raw `<tool_call>` text and the
  chatbot then refuses everything while looking healthy.
  `@cf/meta/llama-4-scout-17b-16e-instruct` measures 39/39 at 611ms — about
  four times faster than the local 3B, same accuracy.
- **Only the chatbot is hosted.** Polish drifts 67% on Taglish and the
  self-report detector missed 28%; both belong to `qwen2.5:3b` and transfer to
  nothing. Enforced in code since 27 Sep 2026 (`allow_hosted`, above); until
  then every drafting feature used the hosted model once the flag was on.
- **The free API sleeps after ~15 minutes idle** and the first request then
  takes about a minute (entrypoint.sh re-runs migrate and the PSGC steps on
  every boot). A refresh in that minute used to show a bare "Loading…" that
  read as broken; `ProtectedRoute`'s `WaitingForServer` now says the server is
  waking after 3 s and offers "Try again" after 75 s. Not a fault to debug.
  Signing in was the same wait with less said (owner, 14 Oct 2026: "20 to 30
  seconds or more"), so three things changed, none of them touching
  `entrypoint.sh`:
  - **`seed_psgc` writes only what changed.** It asked `update_or_create`
    about all 3,394 places on every wake-up: a lookup, an update and a
    savepoint pair apiece, 13,578 statements to Neon to learn that nothing
    had changed. It now reads each table once, compares in Python and bulk
    writes only the rows that are missing or differ, so a run with nothing to
    do is 3 reads (5 statements with BEGIN and COMMIT). Measured locally on
    SQLite: 13,578 -> 5 queries, 1.9 s -> 0.04 s; not measured against Neon,
    where the saving is the round trips. Rows still update in place on the
    PSGC code, so no primary key moves. `test_locations.py` pins the count and
    checks it does not grow with the number of rows.
  - **The sign-in and sign-up pages wake the server on open** (`wakeServer()`
    in `api/client.js`): one GET to `/healthz/`, which also touches Neon, once
    per page load, so the server is waking while the person types and their
    request finds it up. Fire and forget, never a toast. It overlaps the boot
    with the typing; it does not shorten the boot, which still runs migrate
    and both PSGC steps.
  - **The sign-in says what is happening after 3 s** (`SlowSignIn` in
    `Login.jsx`, tied to `busy`): "Waking the server (up to a minute)" and
    "Keep this page open" with the seconds so far, a small spinner beside the
    first line. It takes the place of the "Request access" links under the
    divider and their size - two centred lines of the same type, no box - not a
    spot under the button: the card is centred, so anything that changes its
    height moves the fields, and the button slid out from under the pointer of
    someone waiting on it. The links stay in the flow, hidden while signing
    in, and give the footer its height; the note lies over them in the same
    grid cell, so the card is exactly its idle size at the click and at 3 s
    (measured 0 px on the fields, the button and the card at 1280x800,
    375x667 and 320x568; the idle card is the size it was before the note
    existed). Keep it no taller than the links: on a 320-wide phone
    they are three lines and the note two. Gone the moment the request
    settles, the links back in the same space; a quick sign-in never sees it.
    The Google door shares `busy`, so it gets the line too.
- **A deploy that times out on the health check is usually Render, not the
  code.** It happened on 24 Sep and again on 5 Oct, both times on a deploy
  that carried new migrations; each time a manual redeploy of the same commit
  went through. The free instance runs migrate, `seed_psgc` and
  `backfill_psgc` before Gunicorn answers, and that is close to the limit.
  Redeploy once before debugging; if it fails again, the deploy log's last
  `==>` line says which step is too slow.
- **Cloudflare retires models** — `llama-3.1-8b` returns 410. Check
  `/api/assistant/model-health/` before assuming the code broke.

## Accessibility

Text tokens clear WCAG AA — measured against white, `--ink-50` AND `--ink-100`,
because most text sits on a card and the card is the stricter test.
`--ink-400`/`--ink-500` are untouched and still drive dots and borders, which
are decoration and not held to the text rule.

**`FormField` generates an id and injects it into its child**, so controls are
labelled without touching ~200 call sites. Pass your own `id` or `aria-label`
to opt out.

Audit with **axe-core**, never a hand-rolled probe. Mine gave three different
answers and each was its own bug: it missed a fixed header's background, read
`rgba(255,255,255,0.1)` as opaque white, then sampled a badge's coloured dot
and called it the text's background. An instrument less reliable than what it
measures is worse than none.

Every screen has now been audited with real axe-core, `/reports` and
`/report/child/:id` included — both clean on WCAG A/AA. Check the page
actually rendered before believing a clean result: an empty shell has no
violations either, so the audit records element and text counts beside the
verdict. One known violation remains on `/schedule`, and it is
react-big-calendar's own `role="rowgroup"` markup rather than ours.

**A `Button` shows where the keyboard is** (`.racco-btn:focus-visible` in
index.css). `Button` sets its own `box-shadow` inline (none for the plain and
outlined ones, a drop shadow for the filled ones), and an inline style beats the
global `button:focus-visible` ring, so no `Button` in the app - the console's
eight, the Dashboard's quick actions - had a visible focus until 18 Oct 2026. The
rule is `!important`, the one thing that outranks an inline style, and is kept
to the Button's own class so the inset rings of `.racco-queue-tile` and
`.racco-pick-row`, which are plain buttons, are not overruled. The Dashboard's
quick-action row is hand-built with the same inline shadow and carries the class
by hand. Other hand-built `<button>`s that set `boxShadow` inline have the same
fault and have not been swept: give them `racco-btn`, do not drop the shadow. A computed-style
check needs a wait after focusing: the shadow transitions in, and a read straight
away says `none`.

**Toasts are announced** (`ToastContext.jsx`): the stack is a polite live
region that is always in the page. It was a plain `div`, so the only feedback of
`/users?reset=` for an account that is not there, or of a button that cannot act
yet, was silent to a screen reader. A toast is still not a place for anything
that must be read: the end dialogs (`useNotice`) are.

## Before committing or bundling anything

Both of these, every time:

```
cd backend && .venv/Scripts/python.exe manage.py test   # 3,288 tests, ~20 s with --parallel 4
cd frontend && npm run lint && npm run build
```

**Tests hash passwords with MD5** (`settings.py`, only `if _TESTING`; 10 Oct
2026). PBKDF2 at Django's default costs ~0.22 s a hash and the suite hashes
thousands in setUp: the full run went from ~23 minutes to ~50 seconds.
Production hashing is unchanged - check `manage.py shell` still reports
PBKDF2 if this is ever touched. Nothing asserts a hash prefix; a test that
genuinely needs PBKDF2 sets it with `override_settings`.

**A test never reads the wall clock** (4 Oct 2026). Three failed only between
11 PM and midnight, Manila time (`TIME_ZONE`), and a run that straddles that
window fails them. A late-evening availability window, "now minus five
minutes" as today, and `next_weekday()` disagreeing with itself across one
morning. A test that needs a time pins `django.utils.timezone.now` with
`patch` (`localtime`/`localdate` follow it). To prove a test does not depend
on the clock, run it with the clock faked to Sat 23:58, Sun 00:02 and Wed
09:30. A faked-clock runner must keep "test" in `sys.argv`, or settings.py
turns throttling on and ~43 unrelated assistant tests fail.

**`npm run build` is not enough on its own.** Vite only reports syntax errors —
a reference to a deleted variable, or a hook left below an early return, builds
perfectly and then throws at runtime. Both have already happened here: removing
the AI layer left a `setPolishJob` call behind, and an inserted `useState`
landed under `if (!data) return …`, which crashed the child report for every
user until someone opened that page.

`npm run lint` catches both. It is already configured with
`plugin:react-hooks/recommended`, so `rules-of-hooks` is on and reports
*"Did you accidentally call a React Hook after an early return?"* by name.

After a change that spans several screens, load each one — a page that renders
nothing still exits `npm run build` with code 0.
