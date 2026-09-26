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
- **Declining archives rather than deletes.** An archived address cannot
  register again, so a refused applicant cannot loop until a distracted
  administrator approves them.
- **The form verifies nothing.** No confirmation mail is sent, so a typed
  address is only what someone typed — unlike a Google one, which Google
  verified. The queue says which door each request came through for exactly
  that reason.

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
  Custodian. An adoption shows neither until its type is picked. Changing the
  case type or the adoption type clears the date no longer asked, and the save
  sends it as `null` - Records used to leave an empty date OUT of the request,
  which kept the old value on the record.
- **Category first means the pairing filters both ways**: a category narrows
  the case types and a case type narrows the categories. The server refuses a
  pair the lists do not offer, but only when one of the two is being set.
- **Mandatory on create; on an edit, an answer cannot be taken away.** A record
  from before the rule keeps its blanks through an unrelated edit (the form
  names them as "Still blank"), except that changing the case type or adoption
  type asks that type's questions again. The fullname-only create path the
  older tests use stays exempt, as it always was.
- **Deliberately optional**: middle name (a foundling or a non-marital child
  may have none), legal status (none issued yet), landmark, date found. Asking
  for something that does not exist gets "N/A" typed into it.
- **`middle_initial` was renamed `middle_name`** (children 0020), not dropped:
  the initials already recorded are kept. The display name still uses an
  initial (`middle_initial_of`), and a value already written as one ("DC") is
  kept as written so no existing name changes shape on its next save.
- **Renamed values were migrated** (children 0021): Orphan -> Orphaned, N/A ->
  Unknown, and the demo seeder's misspelt "Stepparent" -> "Step-parent".
  **Retired values were not**: birth status "Child", SIBRA, ICA Relative, and
  the seeder's "Domestic"/"Relative" stay on the records that hold them, shown
  as "(no longer offered)". The serializer accepts them unchanged and refuses
  them as a new pick - the same change-only rule as the old categories.
  `import_demo_data` upgrades an older fixture the same way before loading it.
- "Street Number" is the owner's wording; its hint allows a purok or sitio,
  because most addresses in the region have no street.
- **Previous Custodian is typed, not picked** (staff's request, 24 Sep 2026):
  `surrendered_by` lost its three placeholder choices and widened to 150
  (children 0022). Still required where the case type asks it; old values
  such as "Relatives" are ordinary text now and were left alone.
- **Educational Placement moved to Child's Profile and is required** there
  ("Not in school" is an answer); **Referral Source is a pick** from RACCO /
  LGU / CCA / RCF (children 0023), with typed text on older records kept by
  the same change-only rule; **Current Whereabouts left the form and every
  screen** (owner, 24 Sep 2026) - the column and what it holds are kept.
- **A rename cannot ride along with a deploy the old release survives.** On
  24 Sep the demo API's first deploy of 0020 failed on Render's side after the
  web had gone live; had the migration run first, the old API - still serving
  - would have answered 500 on every child query, because it asks for
  `middle_initial`. Replayed on PostgreSQL 16 it does exactly that. A manual
  redeploy of the same commit went through in two minutes. Prefer additive
  migrations (add, copy, drop later); widening a column, as 0022 does, is safe.

## Each social worker's own records

Owner's decision, 24 Sep 2026: **each SW keeps their own records**, not one
shared list. `Child.social_worker` says whose a record is, and
`accounts/scoping.py` - the one rule nearly every endpoint already used - now
narrows Staff to it. Administrators (the ISA) see everything; psychologists
are unchanged (their assigned children).

- **A new record is its creator's** (`ChildViewSet.perform_create`), whatever
  the request says. **Only the ISA moves one** - the record form's Social
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
  the same child is the worse failure - but another SW's match says only that
  it exists and who holds it ("held by R. Santos - ask the ISA"): no id, no
  birth date, nothing from the record.
- **Dashboard is their own; Agency Summary stays agency-wide** (the owner's
  choice): the Summary holds counts with no names and mirrors the agency's own
  report form. The assistant answers a SW about their own records.
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
  The sign-up email code (`accounts/email_verification.py`) is still in the
  cache, with the same multi-worker weakness.

## Role names on screen

Administrator shows as **"ISA (Administrator)"** and Staff as **"SW (Staff)"**
(24 Sep 2026), through `roleLabel()` in `ui/index.jsx`. Display only: the
stored `role_name`, every permission check and every API answer still say
"Administrator" and "Staff". Never compare anything against the label.

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
  nothing.
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
cd backend && .venv/Scripts/python.exe manage.py test   # 855 tests, ~16 min
cd frontend && npm run lint && npm run build
```

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
