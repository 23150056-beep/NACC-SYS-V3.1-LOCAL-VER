# Running NACC SYS V3 on your own machine

A local copy that behaves like the live system but touches none of it. Useful
for trying changes, working offline, and demonstrating without depending on a
free-tier service being awake.

## What is different from the cloud

| | Cloud | Local |
|---|---|---|
| Database | Neon (PostgreSQL) | SQLite, a file in `backend/` |
| Uploaded files | Cloudflare R2 | `backend/media/` on disk |
| Google Sign-In | on | off — the button hides itself ([turn it on](#google-sign-in-on-your-own-machine)) |
| Assignment emails | Brevo | not sent; skipped with a log line |
| Addresses (PSGC) | seeded on deploy | seeded by the setup script |

Nothing local can reach the live database or the live bucket. Records you make
here exist only here.

## First time

Double-click **`setup-local.bat`** in the repository root.

It checks that Python and Node are installed, then creates the environment,
installs packages, builds the database, and loads the roles, the default
administrator and the 3,265 Region I barangays. Ten minutes or so, mostly
downloads.

If it stops, the message says which step failed. Re-running is safe — it reuses
what already worked.

### Prerequisites

- **Python 3.11+** — <https://www.python.org/downloads/>
  Tick **"Add python.exe to PATH"** during install. This is the step people
  miss, and everything afterwards fails with *"python is not recognized"*.
- **Node.js LTS** — <https://nodejs.org/>

## Every time after that

Double-click **`run-local.bat`**.

Two windows open (API and frontend) and your browser opens on
<http://localhost:5173>. Close the windows to stop.

Sign in as `admin@racco1.gov.ph` / `admin1234`.

## Filling it with something to look at

A fresh database has no children, which makes every cross-caseload screen look
broken rather than empty:

```
cd backend
.venv\Scripts\python manage.py seed_demo_data
```

Invents 40 children across the four Region I provinces, with roughly six months
of remarks, self-reports, appointments and problems behind them. Everyone in it
is fictional — names, notes and answers were all written for that file.

It refuses to run against a hosted database or with `DJANGO_DEBUG=False`, so it
cannot reach the live system. `--purge` starts clean, `--seed N` changes the
caseload, and the same seed always produces the same people — so a demo can be
reproduced exactly.

The notes deliberately mix English, Tagalog and Ilocano, because notes written
in Region I do.

## The writing assistant

On by default, and entirely local — nothing about it reaches the network. The
system is still fully usable without Ollama running: every AI feature degrades
to a message the screen absorbs rather than an error, so a machine without it
is missing those features and nothing else.

1. Install [Ollama](https://ollama.com) and pull the model this was built and
   measured against:

   ```
   ollama pull qwen2.5:3b-instruct
   ```

2. Nothing. `run-local.bat` starts the model server itself, with these
   already applied — `start-ollama.bat` does it, and can be run on its own:

   ```
   OLLAMA_HOST=127.0.0.1
   OLLAMA_KEEP_ALIVE=-1
   OLLAMA_NUM_PARALLEL=1
   OLLAMA_MAX_LOADED_MODELS=1
   ```

   The first is the one with teeth: Ollama listens on every network interface
   unless told otherwise, which puts an unauthenticated model server on
   whatever network this machine has joined. The other three keep one model
   loaded indefinitely and one generation running at a time — reloading
   between calls and running generations concurrently were both measured
   slower on this hardware, not faster.

   Letting the Ollama tray app start at login instead applies none of them.
   `start-ollama.bat` stands aside when a server is already listening, and
   says so when that server is bound to every interface.

3. Confirm it is answering. Sign in as the administrator, open **Settings**,
   and use **Test connection** under "Local writing assistant" — the
   **Assistant enabled** switch there is already on, and exists so a
   misbehaving feature can be stopped without a deploy. Or from a terminal:

   ```
   cd backend
   .venv\Scripts\python manage.py ai_check
   ```

A short draft — polishing a remark — takes about 5 seconds. A pre-session
brief takes about 40 seconds, because it reasons over more text; briefs for a
day's schedule are generated in the background before anyone opens one, so in
normal use the brief button is instant rather than a 40-second wait.

## Google Sign-In on your own machine

Off by default. The login page asks the API whether the feature is configured,
gets `{"enabled": false}`, and renders no button at all — deliberately, so you
see the password form rather than a button that fails when clicked.

You do not need it: `admin@racco1.gov.ph / admin1234` and the demo accounts
cover everything. Turn it on only if you are changing the sign-in flow itself.

### Make a separate client — never reuse the live one

**Do not add `localhost` to the production OAuth client.** That client belongs
to the deployed app, and editing it puts a live sign-in path at risk to test a
local one. Make a second client instead. It costs nothing.

1. Google Cloud Console → **APIs & Services → Credentials**
2. **Create Credentials → OAuth client ID → Web application**
3. Name it something obviously local, e.g. `NACC SYS V3 (local dev)`
4. Under **Authorised JavaScript origins**, add exactly:
   ```
   http://localhost:5173
   ```
   Not `127.0.0.1`, not a trailing slash, not `https`. Google matches the
   origin string literally, and a mismatch fails as a silent, buttonless page.

   **Check the port the frontend window actually printed.** Vite takes 5174 if
   5173 is already in use — from an earlier window still running — and says so
   only in its own startup line. The origin then no longer matches and Google
   refuses, with nothing on screen to explain why. Add whichever port it
   reports, or close the stray window and restart.
5. Leave **Authorised redirect URIs** empty — Google Identity Services returns
   the credential to the page, so no redirect is used.
6. Copy the **Client ID**. The client *secret* is not used and is not needed.

### Point the local API at it

In `backend/.env`:

```
GOOGLE_OAUTH_CLIENT_ID=<the id you just copied>
```

Optionally restrict which addresses may sign in:

```
GOOGLE_ALLOWED_DOMAINS=racco1.gov.ph
```

Leave it unset while testing with a personal Gmail address, or that address is
refused before Google is ever consulted.

**Restart the API window.** `run-local.bat` does not reload on settings
changes, so a new client ID is not picked up until you restart it. Confirm:

```
curl http://localhost:8000/api/auth/google/config/
```

`{"enabled": true, ...}` means the button will render. `false` means the API
did not see the variable — check the file and the restart before touching
anything in Google.

### Three things that look like bugs and are not

- **Administrators are always refused**, with *"Administrator accounts sign in
  with email and password, not Google."* This is deliberate: the admin account
  is the recovery path, and tying it to a third-party identity provider means
  an outage or a lost Google account locks the agency out of its own records.
  Test with a **staff or psychologist** account.
- **An unknown Google address does not sign in.** It creates an *access
  request* for an administrator to approve, and the caller is told the request
  is pending. That is the intended flow, not a failure. To sign in directly,
  first create a user whose email is exactly your Google address, with role
  Staff or Psychologist.
- **A Google app left in Testing mode refuses every account** that is not on
  its test-user list, and does so in a way that looks exactly like a broken
  button. Either add your address under **OAuth consent screen → Test users**,
  or publish the app.

### Turning it back off

Comment the variable out of `backend/.env` and restart the API. The button
disappears again; nothing else changes.

## Starting over

Delete `backend/db.sqlite3` and run `setup-local.bat` again for an empty
database with just the roles, the administrator and the addresses.

## Running the tests

```
cd backend
.venv\Scripts\python manage.py test
```

666 tests. This is the local copy's real purpose: change something, prove it
still works, and only then send it to the live system.

## Manual setup, if you prefer

```
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py seed_initial_data
.venv\Scripts\python manage.py seed_psgc
.venv\Scripts\python manage.py runserver

cd ..\frontend
copy .env.example .env
npm install
npm run dev
```
