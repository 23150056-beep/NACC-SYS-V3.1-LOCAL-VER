# NACC SYS V3 — Office PC (On-Premises) Deployment & Backup Guide

> **V3's primary deployment target is the cloud** — see
> [`CLOUD-DEPLOYMENT.md`](CLOUD-DEPLOYMENT.md). This document covers the
> on-premises single-PC path, which remains supported. Choose it when the
> agency wants case data to stay physically on its own hardware.
>
> **Not re-tested since the cloud path landed.** It is kept because the
> on-premises option is a real one, not because anyone has walked it through
> recently — so treat the commands as a starting point rather than a
> verified script. Two things are known to be off: it says `python -m venv
> venv` where the rest of the repo uses `.venv`, and it assumes `psql` is on
> PATH, which it is not on the development machine. Walk it once on real
> hardware before promising it to an agency.

Target: a single office PC/server at RACCO I (Windows, 8–16 GB RAM).
Stack: Django 5.1 + PostgreSQL + React (built static) + optional Ollama.

## 1. One-time setup

### Prerequisites
- Python 3.12+ · Node 20+ (build only) · PostgreSQL 16+ · (optional) Ollama

### Database
```powershell
# in psql as the postgres superuser:
CREATE DATABASE nacc_v3;
```

### Backend
```powershell
cd backend
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env:
#   DJANGO_SECRET_KEY  -> long random string
#   DJANGO_DEBUG=False
#   DB_ENGINE=postgres, DB_PASSWORD=<the password chosen at PostgreSQL install>
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py seed_initial_data   # roles + default admin
```

Default admin: `admin@racco1.gov.ph` / `admin1234` — **change this password
immediately** via User Management.

### Frontend
```powershell
cd frontend
npm install
npm run build        # outputs dist/ — serve with any static server
```
For LAN use, the simplest run mode is `npm run dev` (port 5173) next to
`manage.py runserver` (port 8000); set `VITE_API_BASE_URL` in `frontend/.env`
to `http://<server-ip>:8000/api` for other machines on the network.

### Optional: local AI (Ollama)
```powershell
winget install ollama.ollama
ollama pull qwen2.5:3b-instruct     # ~2 GB; the model this branch was built and measured against
```
The target office PC measured for this feature had **1.16 GB free RAM**, and
a 2B model at Q8 quantization failed to allocate on it. `qwen2.5:3b-instruct`
is the model this branch was actually built and measured against, and it ran
within that budget.

Then in the app: **Settings → Local writing assistant** → enable the master
switch, then confirm it can reach the runtime with **Test connection** (or,
from a terminal, `manage.py ai_check`). There is no runtime provider to
choose — Ollama on loopback is the only provider this deployment has, by
design, so case data never leaves this machine.
If the machine is tight on RAM, keep the assistant off during heavy use —
every other feature works without it (care-gap alerts are deterministic and
always on).

## 2. Backup (do this weekly, before any update)

Two things hold all data: the PostgreSQL database and the media folder.

```powershell
# database
pg_dump -U postgres -d nacc_v3 -F c -f "D:\backups\nacc_v3_$(Get-Date -Format yyyyMMdd).dump"
# uploaded files (reports, consent scans, photos)
Copy-Item backend\media "D:\backups\media_$(Get-Date -Format yyyyMMdd)" -Recurse
```
Keep at least 4 rotations on an external drive stored separately (child data —
RA 10173 duty of care).

## 3. Restore

```powershell
psql -U postgres -c "DROP DATABASE IF EXISTS nacc_v3; CREATE DATABASE nacc_v3;"
pg_restore -U postgres -d nacc_v3 "D:\backups\nacc_v3_YYYYMMDD.dump"
Copy-Item "D:\backups\media_YYYYMMDD\*" backend\media -Recurse -Force
```

## 4. Security checklist (per release)

- `DJANGO_DEBUG=False`, unique `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`
  restricted to the server name/IP.
- Uploaded files are served **only** through authenticated endpoints
  (`/api/report-files/<id>/download/`, `/api/consents/<id>/download/`);
  the media folder must never be exposed by a web server directly.
- No instrument content in the database — spot-check `tbl_instrument_catalog`
  (titles/metadata only) and `tbl_agency_form_template` (attestation set).
- Access matrix (enforced server-side, verify after permission changes):

| Capability | Admin | Psychologist | Staff |
|---|---|---|---|
| Users / roles | full | — | — |
| Child & guardian records | full | read (assigned only) | create/edit |
| Terminate case (reason required) | yes | own cases | — |
| Instrument catalog / agency forms | all | own | — |
| Pre-assessment flow, remarks, plans, results, report uploads | yes | own children | read-only |
| Consent / interview / problem records | write | own children | read-only |
| Availability | all | own | read |
| Book appointments | yes | own calendar | inside availability |
| Appointment status | yes | own | cancel only |
| Agency summary / census | yes | — (dashboard is scoped) | yes |
| AI feature flags | yes | — | — |
| AI drafts (brief/summary/polish) | yes | own children | narrative only |
