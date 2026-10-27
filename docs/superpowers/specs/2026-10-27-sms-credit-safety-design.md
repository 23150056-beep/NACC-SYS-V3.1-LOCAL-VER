# SMS: credit safety, one catalogue, and a repeatable live check

Owner's request, 27 Oct 2026: test every SMS feature against the real
Semaphore account, find what can drain its credit, minimise it while keeping
every feature, and make the protections apply to SMS features added later.

## What the live test found (27 Oct 2026)

Every feature sent once to the owner's handset through its real function,
SMS_PROVIDER=semaphore, sender NACCRACCO1. Semaphore accepted all eleven
("Sent", Smart, one segment each). The system recorded **nine as failed**:

| Message | Chars | Reply chars | System said |
|---|---|---|---|
| Temporary password notice | 109 | ~426 | failed |
| Case waiting (psychologist) | 90 | ~407 | failed |
| Psychologist reminder | 78 | ~395 | ok |
| Custodian booked / moved / cancelled / missed | 141 / 111 / 132 / 88 | 405-458 | failed |
| Custodian day-before reminder | 118 | ~435 | failed |
| Own phone verification code (OTP) | 69 | ~399 | ok, by one char |
| Custodian number code (OTP) | 113 | ~443 | failed |
| Settings test text | 86 | ~401 | failed |

Cause: `_post` (`accounts/sms.py:417`) keeps only the first 400 characters of
the reply, and Semaphore echoes the whole message, the account email and the
sender name back. A cut reply is not JSON, so a paid, delivered text is read
as a failure. The tests did not see it because their fake replies are short.

Consequences today: a custodian's number can never be confirmed (the code is
wiped on "failure", the SW retries at 2 credits a time), so custodian texts
can never be turned on; the custodian reminder deletes its claim and a rerun
texts again; the reminder job exits non-zero every night.

## Principle: protections live in the sender, not in the features

Every text passes through `send_sms` (`queue_sms` calls it). Anything that
must hold for every text - reading the reply, telling refusal from unknown,
limits, the spend record - goes there, so a feature added later inherits it
by calling `send_sms` like the others. What it must declare up front goes in
one catalogue, and a test fails on a message nobody has declared.

---

## Phase 1 - the sender core (`accounts/sms.py`, `config/settings.py`)

1. **Read the whole reply.** `_post` reads up to 64 KB and parses all of it;
   only the log line and the on-screen detail are shortened. Tests use the
   real reply shapes captured on 27 Oct (account email and number replaced
   with same-length stand-ins), for every message, Pending and Sent, normal
   and OTP.
2. **Three outcomes, not two.** `SmsResult.outcome` is `sent`, `refused` or
   `unknown` (`ok` stays, true only for `sent`):
   - refused: missing key, invalid number, HTTP 4xx, a readable reply with no
     message_id (Semaphore's "No active sender name" under HTTP 500 included);
   - unknown: timeout or connection dropped after the request went, an
     unreadable 2xx, a 5xx with no readable refusal.
   Callers give back limits, or delete a reminder claim, **only on refused**.
   Unknown is "may have been sent" - kept, never automatically resent.
3. **`SMS_PROVIDER` is normalised once** in settings (`.strip().lower()`), so
   "Semaphore" no longer sends silently to the log.
4. **A record of every send**: new model `SmsSend` (accounts 0016): when,
   catalogue kind, route, estimated credits, outcome, Semaphore message_id,
   the requester (nullable), a subject reference (e.g. `child:42`,
   `appointment:7`, `user:5`), and the number as a keyed hash plus its last
   four digits - enough to count sends per number without keeping a list of
   custodians' phone numbers in a second table. Written by `send_sms` for every
   attempt that reaches a gateway (and for the console provider with 0
   credits, so tests and the local copy exercise it).
5. **Limits that apply to every text, checked in `send_sms` before the
   network, counted from `SmsSend` (shared by every worker, survives
   restarts):**
   - one number: at most **10 texts a day** of any kind;
   - the whole agency: at most **300 credits a day** (`SMS_DAILY_CREDIT_LIMIT`);
     at 80% the console's text-message row turns amber, at 100% texts are
     refused with a sentence that says so, and the row turns red.
   A refused-by-limit text costs nothing and is recorded as refused.

## Phase 2 - the catalogue and its guard test

New `accounts/sms_catalogue.py`: one entry per message the system can send -
key, template, route (normal/otp), recipient type (staff / custodian / caller),
its own limits, and worst-case sample values (longest case number, two-digit
day, "12:30 PM", etc.). The existing wording moves here unchanged; the code
sites keep their names (`notify_custodian`, `send_code`, ...).

`send_sms(..., kind=)` takes a catalogue key instead of a free description and
refuses an unknown one. `test_sms_catalogue.py` fails when:
- a message rendered with its worst-case values is over one segment, is not
  GSM-7, or starts with TEST;
- an entry has not declared its route, recipient and limits;
- a call to `send_sms` / `queue_sms` anywhere in the code names a kind that is
  not in the catalogue (read from the source, the way the chatbot's tool
  count is pinned).

So a new SMS feature is: add an entry, call `send_sms(kind=...)`. It gets the
reply reading, the outcomes, the record, the per-number and daily limits, the
guard test and the live check (phase 5) without anything else.

## Phase 3 - each feature's own leaks

| Feature | Change | Saves |
|---|---|---|
| Own phone code | Already-verified same number: "already verified", nothing sent. Per number: 2 codes an hour, 4 a day, across all accounts; per account 10 a day. Limits kept on an `unknown` outcome. | blocks the 20-credits-an-hour loop to any number |
| Custodian number code | Same limits; a number this SW confirmed within the hour is "already confirmed". Stays on the OTP route (decision 1). "Send code" asks first, naming the number. | typo and repeat codes |
| Custodian booked / moved / cancelled / missed | "Moved" only when the date or time actually differs from what the custodian was last told; at most 4 event texts per appointment per day (after that the SW is told on screen to phone them). | drag-and-drop and book/cancel loops |
| Custodian reminder | Skipped when the custodian was texted about this appointment (booked/moved) in the last 12 hours. A moved appointment gets a fresh reminder for its new day (today it never does). Claim deleted only on refused. | the short-notice duplicate; fixes a missed reminder |
| Custodian reminder, siblings | **See decision 2.** | N-1 credits per family per day |
| Psychologist reminder | Claim deleted only on refused. | duplicate on rerun |
| Case waiting (assignment) | Not texted again when the same psychologist was texted about the same child in the last hour (a withdraw-and-re-ask). A re-ask later, or after a decline, still texts. | toggling the pick |
| Temporary password notice | At most one text per account per 10 minutes; the email always goes. | repeated reset clicks |
| Settings / console test text | Server-side: 1 a minute, 5 an hour (from `SmsSend`). Both confirmations say "uses 1 credit" and point to the free "Check the key". | a held-down button or script |

## Phase 4 - seeing the spend

The console's Text messages row (`admin_overview._sms_health`) reads `SmsSend`:
"N texts, about M credits in the last 24 hours", the daily limit, how many are
unconfirmed (outcome unknown), and amber/red at 80%/100%. No message text, no
numbers - kind, count, credits. Today the row counts reminders only.

## Phase 5 - the live check, kept in the repo

`manage.py sms_live_check --to 09XXXXXXXXX [--only KIND ...] [--dry-run]`:
- renders every catalogue message with its worst-case sample values and sends
  each once through `send_sms` - so a new message is included automatically;
- prints the estimated credits and asks for the number to be typed back
  before sending anything; refuses with the console provider, refuses on a
  hosted database, `--dry-run` sends nothing;
- then asks Semaphore for each message's status and prints one table: kind,
  route, chars, segments, message_id, Semaphore status, what the system
  recorded. Any row where the two disagree is a bug - that is exactly the one
  found today.
Never part of `manage.py test`. Costs about 13 credits for the current eleven.

## Not in this plan

- **`.github/workflows/session-reminders.yml` gives up on a Render 502.** It
  drives the hosted deployment, which is out of scope (CLAUDE.md "Scope");
  noted for when the owner asks.
- **Delaying and merging custodian texts** (send only the final time after a
  burst of moves). It needs a queue that survives a restart, which this
  system does not have; the "last told" check and the per-appointment cap
  cover the same waste without one.

## Decisions for the owner

1. **Custodian code route - settled 27 Oct 2026: both codes stay on the OTP
   route.** The handset showed the normal route delivers, but late: the
   18:08 normal-route check arrived after texts sent at 18:15, while the
   OTP code sent a second after it arrived at once. A code is read back by
   somebody waiting for it, so 1 credit saved once per custodian number is
   not worth a seven-minute wait. The per-number limits and "already
   verified" checks above are what stop codes being wasted.
2. **One reminder per custodian per day for siblings** ("the children in your
   care have appointments tomorrow: 9:00 AM, 10:30 AM at the RACCO I office")
   instead of one per child. New wording - yes or no.
3. **The numbers**: 300 credits a day for the agency, 10 texts a day per
   number, 2 codes an hour / 4 a day per number. Change any of them; they are
   settings, not code.

## How it is built and checked

- Workers on Sonnet do the code, phase by phase; each phase is read before it
  is committed. Commits as Reynold <jreynoldcanedo@gmail.com>, pushed to
  `claude/brave-cray-e0dgmx` on NACC-SYS-V3.1-LOCAL-VER only.
- Each phase: its own tests, then the full suite
  (`manage.py test --parallel 4`), `npm run lint && npm run build` when the
  frontend changes, and `makemigrations --check` (two new migrations:
  accounts 0016 `SmsSend`, scheduling 0006 for the reminder "last told"
  fields - both additive).
- At the end, `sms_live_check` against the owner's number, with the owner's
  go-ahead first (about 13 credits), and every row must read the same in
  Semaphore and in the system.
- CLAUDE.md "Text messages" updated with the catalogue rule: a new SMS
  feature is a catalogue entry plus a `send_sms(kind=)` call, nothing else.
