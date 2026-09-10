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
is that `git push` is safe and `git push origin` is not.

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

**There are two remotes, and only one of them is safe.** Changed 27 Aug 2026.

| Remote | Repository | Deploys Render |
|---|---|---|
| `local-ver` | `NACC-SYS-V3.1-LOCAL-VER` | **no** |
| `origin` | `NACC-SYS-V3` | **yes, on `cloud-setup`** |

The branch `cloud-setup` tracks `local-ver/main`, so:

```
git push
```

is correct and goes to the local-version repo. It cannot touch the live
services, which are built from the other repository.

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
- **Do not pipe a long verification through `tail`.** A background
  `manage.py test | tail -8` reported `FAILED (failures=4, errors=1)` and threw
  away every failure name with it; the whole suite had to run again to find
  out what broke. The same goes for `grep` over a downloaded bundle — it can
  abort on a large minified file and print nothing, which reads exactly like
  "the string is absent".

**Do not `git push origin`** — the owner has said he does not want Render
touched, and that push is what deploys it. Pushing there needs asking first, in
so many words.

### When he does say to push live

**A plain `git push origin` is wrong, and will be refused.** The two lineages
have diverged permanently and on purpose: `render.yaml` here names the demo
services and points at a Neon branch and an empty R2 bucket, while the live
repo's names `nacc-v3-api`/`nacc-v3-web` and points at production. Forcing this
file onto live is how the live deployment lost its file storage once already —
the repair is commit `5d9342a`, "Restore the live Render blueprint that the
demo's file overwrote".

There is a local branch `live-push` tracking `origin/cloud-setup`. Merge into
it rather than pushing across:

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
- Verify a push landed with `git ls-remote local-ver` rather than asking.

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
  `OLLAMA_NUM_PARALLEL=1`.
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

## The demo deployment

Built 27 Aug 2026. Public, free, fictional children, real accounts. Runbook in
`docs/CLOUD-DEPLOYMENT.md` §11-12; design in
`docs/superpowers/specs/2026-08-27-free-secure-web-deployment-design.md`.

- **It deploys from `local-ver`, never `origin`.** Services are `nacc-v3-demo-*`;
  the live ones are `nacc-v3-api`/`nacc-v3-web`, built from the other repo.
  Whether a push actually reaches the demo is a question with an answer, not an
  assumption — see "A push is not a deploy" above.
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
