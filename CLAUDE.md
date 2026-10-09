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
  number), the newest survey; the care gaps those rows already say are
  dropped. `/assistant/capabilities/` says `brief: "clinical" | "case"`. A
  model-written part for SWs is next step 6 and waits on `ai_eval` on the
  owner's PC; it may read no remarks, no self-report words, no case study
  text and no unconfirmed summary.
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
study tab on an Adoption child's page that prints the SCSR. No Word export.
App `case_study` (never `adoption`, see "Removed").

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
  file", each printable). A closed or non-Adoption case cannot be reopened -
  its finals still print. Finalized is addressed to the ASSIGNED psychologist
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
  to confirm, and the record must hold nothing but what Add Record makes - a
  pending request and its referral files go with it; any appointment,
  clinical record, case study, answered request or assistant job blocks it.
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
  intake aside, below) - the record form's Social
  Worker field, shown to the ISA only; a SW sending a different one gets 400,
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

## Before committing or bundling anything

Both of these, every time:

```
cd backend && .venv/Scripts/python.exe manage.py test   # 2,306 tests, ~23 min
cd frontend && npm run lint && npm run build
```

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
