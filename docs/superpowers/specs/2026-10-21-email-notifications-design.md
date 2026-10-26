# Email Notifications for Psychologists and Social Workers

**Date:** 2026-10-21
**Status:** Built on `claude/busy-faraday-jl44nl`. The calendar invite is not
yet checked in a real Gmail inbox.
**Owner's request:** "I need to have my users each of them have their own
google notifications for example the psychologist: when a child as been assign
to them it will notify them from their google email account, Im open for your
other suggestions ... the current notifications are only from password reset
and temp password"

Owner's answers, 21 Oct 2026:

- **Sender:** "I already have a google notification set up on this system, can
  you not utilize that existing google notification?" The emails the owner
  receives today come from `racco1nacc@gmail.com` through the existing sender
  in `children/notifications.py` (`_post`, Brevo's API, `BREVO_API_KEY`).
  Every new email goes through that same path. No new provider, no new
  setting.
- **Which emails:** the psychologist's (cases and sessions) and the social
  worker's (answers and handovers). Not the ISA's, and not the
  account-and-security set; both stay as suggestions.
- **Calendar invites:** yes, on session emails.
- **Opt-out:** yes, one switch per email on My Profile.

## What happens today

Five emails exist, all in `children/notifications.py`: the temporary password,
the sign-up verification code, the Settings test, and "A case is waiting for
your answer" to a psychologist asked to take a case (`request_assignment`).
That last one shows the case's raw id ("Case number: 42") where every screen
and the SMS say `C-0042`. Nothing tells a social worker anything by email, and
nobody can turn an email off.

The bell (`ActivityLog.recipient`) already addresses events to people, but it
is not a model for email: most bell lines are addressed to the psychologist
even when the psychologist did it themselves, and every line names the child.
So emails are their own curated catalogue, fired from the same domain
functions, never from `log_activity`.

## The rules every email keeps

1. **No child's name, ever.** Not in the subject, the HTML, the text part or
   the calendar invite. Also never: birth date, case type, address, custodian,
   the session's purpose or notes, a decline reason, any clinical or case
   study text. Brevo is a processor outside the agency's agreements
   (docs/CLOUD-DEPLOYMENT.md), and Google keeps the invite. What an email MAY
   carry: the case reference (`scheduling.visibility.case_ref`, "C-0042"),
   session dates and times, staff names (`accounts.display.display_name`) and
   counts. A test plants a sentinel child name and searches every email for it.
2. **A nudge to sign in, never the thing itself.** Same idea as the texts in
   `accounts/sms_notifications.py`.
3. **Never for your own action.** If you did it, you are not emailed about it.
   The one exception is the calendar update below, and it says so.
4. **Only to someone who can act on it:** an active account
   (`is_active`), with an email address, in the email's role, who has not
   switched it off.
5. **After the commit, off the request thread.** `transaction.on_commit` plus
   a daemon thread, the pattern `send_assignment_notification` uses. A
   rolled-back save mails nothing, and a slow Brevo never holds a save open.
   Never raises: a failure is logged and the save stands.
6. **With no `BREVO_API_KEY`** (the local copy), nothing is sent. One line is
   logged instead, `EMAIL (console) <kind> to <address>: <subject>`, the way the
   sign-up code and the SMS console provider do it, and nothing is recorded as
   sent.

## The catalogue

One table, `KINDS` in `accounts/email_notifications.py`, ordered as the
profile card shows it. The key is stored in opt-out rows, so **a key is never
renamed or reused**.

### Psychologist

| Key | Switch label | Fires when | To |
|---|---|---|---|
| `case_request` | Asked to take a case | `assignment.request_assignment` writes a new request | the psychologist asked |
| `request_withdrawn` | A request is withdrawn | `assignment.withdraw` settles one as withdrawn (any caller), and a pending request is taken out with a duplicate record (`children/duplicates.py`) | the psychologist who was asked |
| `my_calendar` | Sessions on my calendar | someone else books, moves or cancels a session on their calendar | the appointment's psychologist |

### Social worker (Staff)

| Key | Switch label | Fires when | To |
|---|---|---|---|
| `case_accepted` | A psychologist accepts | `assignment.accept` | the record holder (`_record_holder`), only if Staff |
| `case_declined` | A psychologist declines | `assignment.decline` | the record holder, only if Staff |
| `records_transferred` | Records moved to me | the ISA's transfer (`children/transfer.py` `move`) commits | the receiving worker |
| `case_taken_over` | My closed case is taken over | another SW reopens their closed case at intake (`ChildViewSet.reopen`, `taking_over`) | the previous holder, if Staff |
| `child_sessions` | Sessions for my children | someone else books, moves or cancels a session for one of their children | `child.social_worker`, if Staff |

Each kind also carries a one-sentence `description` for the profile card.

### The wording

`{by}` is `display_name(actor)`, `{ref}` the case reference, `{psych}` the
psychologist's display name, `{when}` like "Tue 21 Oct 2026, 9:30 AM",
built with `config.clock.clock` in local time (no `%-d`, which raises on
Windows).

| Kind | Subject | Body (first paragraph) | Button |
|---|---|---|---|
| `case_request` | A case is waiting for your answer | *Replaced on 23 Oct 2026: see "The case request email" in Round two.* | Review the request: `/` |
| `request_withdrawn` | Case {ref} is no longer waiting for you | {by} withdrew the request for you to take case {ref}. There is nothing for you to do. | Open NACC SYS: `/` |
| `case_accepted` | {psych} accepted case {ref} | {psych} accepted case {ref}. It is now in their records, and a session can be booked. | Open the record: `/report/child/<id>` |
| `case_declined` | {psych} declined case {ref} | {psych} declined case {ref}. Sign in to read their reason and ask another psychologist. | Open the record: `/report/child/<id>` |
| `records_transferred` | {n} record(s) moved to you | {by} moved {n} record(s) to you from {from}. | Open Records: `/children` |
| `case_taken_over` | Case {ref} was reopened by {by} | {by} reopened case {ref}, a closed case in your records, at intake. It is now in their records. | Open NACC SYS: `/` |
| sessions | see below | | Open the schedule: `/schedule` |

A session email reads "{by} booked a session on your calendar" / "for case
{ref}", "...moved...", "...cancelled...", with fact rows **Case** {ref},
**When** {when} to {end time}, and, on a move, **Was** {old when}.

Every email is the temporary password email's branded layout (same palette and
header, a table layout with inline styles because Gmail strips `<style>`), with
a plain-text part, and this footer: "You get this email because of your
notification settings in NACC SYS (My Profile, Email notifications). No case
details are included. Automated message; replies are not monitored." The
button's address is `sign_in_url()` plus the path; with no address known, no
button.

The existing `case_request` email moves into this catalogue: same subject and
question, now with `C-0042`, the asker's name, the layout and the switch. The
old `build_payload` and `send_assignment_notification` go, with their tests
rewritten for the new function.

## Sessions and the calendar invite

One function decides everything for a change to an appointment
(`scheduling/session_mail.py`), called from `AppointmentViewSet`:

- **booked:** `perform_create`.
- **moved:** `perform_update` when `start`, `duration_minutes` or the
  psychologist changed, and the appointment is scheduled.
- **cancelled:** the status action moves a scheduled appointment to cancelled
  and it had not started yet. Completed and no-show are outcomes, not changes
  to the plan, and send nothing. Appointments cannot be deleted
  (`http_method_names`).
- **The psychologist changed** on an update: the previous psychologist is
  sent a cancellation, the new one a booking.

**The psychologist** (`my_calendar`) gets the email with a calendar invite
attached (`invite.ics`, Brevo's `attachment` field, base64). Gmail shows it as
an invitation and Google Calendar can add it. A move sends the same event
again with a higher `SEQUENCE`, which updates it. A cancellation sends
`METHOD:CANCEL`, which removes it.

**The calendar update.** If the psychologist moves or cancels a session they
were already sent an invite for, the event in their Google Calendar would be
left at the old time. So they get a short "Calendar update: session {ref}"
email carrying only the updated invite, which says it is about their own
change. That is the only email anyone gets about their own action. Nothing is
sent for a session they booked themselves.

**The social worker** (`child_sessions`) gets the same email without an
invite, because the invite is for the person who has to be in the room.

**The invite itself** (`scheduling/calendar_invite.py`, RFC 5545, CRLF line
endings, lines folded at 75 octets, text escaped):

- `METHOD:REQUEST` or `METHOD:CANCEL`. `STATUS:CONFIRMED` or `CANCELLED`.
- `UID`: `appointment-<id>-<tag>@nacc-sys`, with `<tag>` the first 10 hex
  characters of the SHA-256 of `sign_in_url()` (or "local"), so the demo and a
  local copy never share an id for one person's calendar. **Replaced on
  23 Oct 2026:** the UID is stored and the tag is random (round two, below).
- `DTSTART` and `DTEND` in UTC, from `start` and `duration_minutes`. `DTSTAMP`
  is now.
- `SUMMARY:NACC session · C-0042`. `DESCRIPTION`: "Case C-0042. Sign in to
  NACC SYS for the details: <url>". `LOCATION`: the first line of
  `AgencyProfile.office_address`, else "RACCO I office".
- `ORGANIZER;CN=<BREVO_SENDER_NAME>:mailto:<BREVO_SENDER_EMAIL>`;
  `ATTENDEE;CN=<psychologist>;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:<their address>`.
  `RSVP=FALSE` because a reply would land in the agency's inbox.
- `SEQUENCE` from `CalendarInvite`, one row per (appointment, attendee):
  `sequence`, `cancelled`, `sent_at`. It is bumped only when an invite is
  actually queued, and its existence is what "already sent an invite"
  means.

**Not verified against real Gmail from the build environment**, which has no
Brevo key. Brevo sends the `.ics` as an attachment, not as the inline
`text/calendar` part a calendar server would use. Gmail recognises such
attachments, but whether it adds the event without a click depends on the
person's Google Calendar setting ("Add invitations to my calendar"). Check
with a real booking once the key is set.

**Turning `my_calendar` off stops updates too,** so sessions already added
stay as they were. The switch's description says so.

## Each person's switches

`EmailOptOut` (accounts, `tbl_email_opt_out`): `user`, `kind`, `created_at`,
unique on (user, kind). **A row means off.** No row means on, so a kind added
later starts on for everyone, and an account that never opened the card gets
everything. A new table rather than a column, so a release still serving
during a deploy is untouched.

`GET /api/auth/me/email-notifications/` (signed in):

```json
{"address": "p@gmail.com", "sending": true,
 "kinds": [{"key": "case_request", "label": "Asked to take a case",
            "description": "...", "enabled": true}]}
```

Only the caller's role's kinds are listed; an administrator gets `[]`.
`sending` is whether a mail service is set up (`BREVO_API_KEY`).

`PUT` with `{"enabled": {"case_request": false}}` changes only the keys sent and
answers with the same shape. A key that is not the caller's role's, or a value
that is not a boolean, is a 400 naming the key, and nothing is written.

**My Profile** gets an "Email notifications" card under the mobile number card
(eyebrow "Notifications"): "Sent to <address>", one `Switch` per kind with its
description, and Save, enabled once something changed and asking first through
`useConfirm()`, as every save does. With `sending` false, a note says this copy
has no mail service set up, so nothing is sent yet and the choices are kept for
when it is. Hidden for an administrator, who has no kinds.

## Signing in from an email

Tokens live in per-tab `sessionStorage`, so a link from an email opens a tab
with no session of its own. Since 26 Oct 2026 that tab borrows the session of
a tab already signed in as the email's recipient, whom the button names with
`for=<user id>` (`2026-10-26-shared-tab-session-design.md`). With no such tab
it is signed out. `ProtectedRoute` sends people to `/login` and
`Login` goes to `/` afterwards, so every email button would land on the
Dashboard. Now the sign-in screen gets the address it was sent from
(`<Navigate to="/login" state={{ from }}>`) and goes back there after a
password, Google, or forced-password-change sign-in. Only a path starting with
a single `/` that is not `/login` is followed; anything else goes to `/`.

## Not done, suggested

- The ISA: an email when an access request becomes approvable (a Google
  request, or a typed one once its code is confirmed), without the applicant's
  typed name.
- Everyone: "your access was approved" (nobody is told today), "your password
  was changed", "your role was changed". These are security emails and would
  not have a switch.
- An invite for a session a psychologist books themselves, so their whole
  calendar is in Google Calendar.
- An emailed day-before agenda alongside the SMS reminder
  (`send_session_reminders`).
- The previous psychologist told when a child moves to someone else on a
  transfer.
- Sending from each user's own Gmail: not practical. It needs Google's
  restricted `gmail.send` scope, a security assessment and changes to the live
  OAuth app.

## Settled in review

Found by reading the built change against the rules above, 21 Oct 2026. Each is
a gap in the table or the wording, not a change of what is emailed to whom.

- **Names, never addresses.** `username` is the email address on every way an
  account is made and a name is optional, so `display_name` would have put a
  colleague's address in a subject line. Emails and invites name people through
  `accounts.display.entered_name`: the name typed, else "A colleague" / "A
  psychologist" / "The ISA" / "Attendee". A recipient with no name is mailed by
  address alone.
- **Closing a case** withdraws its open request (`terminate`), and the email to
  the psychologist who was asked now says the case was closed and names nobody,
  where it read "{by} withdrew the request".
- **A session already in the past** (never marked completed, still
  "scheduled") is not mailed about when its length or psychologist is edited,
  the same rule the custodian's text has.
- **Handing a session to another psychologist**: the social worker's email
  carries a Psychologist row ("Jose Rizal (was Marivic Bulan)"), and the one it
  left is told the session goes ahead with the other.
- **The calendar invite is numbered under a row lock**, so two changes handled
  together are numbered one after the other; a file that cannot be built
  rolls the count back. `sent_at` is stamped on every send.
- **A 400 from Brevo for a message with an attachment** is sent once more
  without it: the message is the point, the invite the extra.
- **Invisible formatting characters** (right-to-left overrides, zero-width
  spaces) are dropped from names in the invite.
- **The `records_transferred` switch** says "Transfer records": only the ISA's
  bulk move sends it, not a single Social Worker pick on the record form.
- `request_withdrawn` accepts a string only if it is a case reference; the
  queue's `on_commit` callback is `robust`.

Left open, with the owner:

- **A session pointed at a different child** (API only; the calendar never
  sends it) changes the invite's case but sends nothing. It needs a decision:
  tell both social workers and update the invite, or refuse the change.
- **The event UID** is derived from `CORS_ALLOWED_ORIGINS` as specified above, so
  editing that list while sessions are booked would give each a second event.
  Storing the UID on the first `CalendarInvite` row would fix it. *Done in
  round two.*
- **"No case details are included"** (the footer, the My Profile line) is
  stronger than a session email's date and time; the deployment document now
  says what they carry. The wording is the owner's to settle.

## Round two: the case request email and the site link (23 Oct 2026)

**Owner's request:** "improve the case is awaiting for your answer ui, and also
add the direct site link for them to easily access the site, if you think
something here on my suggestion can lead to vulnerability or security measures
that can be breach suggest me a better approach to improve this notification"

### What was wrong with the link

The email the owner has been receiving is the OLD one on the hosted copies:
"Sign in to the RACCO I Child Care Management System to accept or decline it."
No link at all. The new one has a button, but its address is `sign_in_url()`,
the first non-localhost entry of `CORS_ALLOWED_ORIGINS`, and on the local copy
that list is only `http://localhost:5173` - a link that opens nowhere but the
PC running the app. A psychologist reading the email on their phone would get
a dead link.

### The site address: `SITE_URL`

A new setting, `SITE_URL`: the address people actually open NACC SYS at. It is
used first by `sign_in_url()`, which every email shares (the temporary
password email included); unset, the old rule stands, so the hosted copies
behave exactly as before until someone sets it.

It is **read from configuration only, never from the request** (`Host`
header). A link built from the request is the classic password-reset
poisoning hole: anyone who can send a request with a forged `Host` gets the
system to email a colleague a link to their own site.

It is checked when settings load (`config/guards.py`, beside the CORS guard):
an `http` or `https` scheme, a host, no user name or password in it, no query
or fragment; a trailing slash is dropped; a path is refused (it is a site, not
a page). `http` is refused when `DJANGO_DEBUG` is False - a deployed copy is
https. A value that fails is ignored with a warning naming why, and the old
rule is used, rather than failing the boot over an email link.

### The calendar invite's UID no longer follows the address

The UID's tag was the hash of `sign_in_url()`, so setting `SITE_URL` would have
given every booked session a second event in people's calendars (the "Left
open" item above). The UID is now **stored**: `CalendarInvite.uid` (scheduling
0007, additive, `db_default=""`). The first invite for an appointment draws a
random one (`appointment-<id>-<10 random hex>@nacc-sys`); every later invite
for that appointment, to anyone, reuses it. A row from before the column (empty
`uid`) gets the old derived value written into it on its next send, so an
invite already in someone's calendar keeps its identity.

### The case request email

Every notification email gets, through the shared `render()`:

- **Preview text** - the hidden first line Gmail shows beside the subject in
  the inbox list ("Case C-0042 is waiting for you to accept or decline.").
- **The address in plain words under the button**: "Or open NACC SYS at
  https://...". The bare site address, never the deep path, so a person can
  check it is the address they know. It is text, not a link, so a mail
  service's click tracking cannot rewrite it into a stranger-looking one.
- **A safety line**: "NACC SYS never asks for your password or a code by email,
  and this button only opens the sign-in page. If the address above is not the
  one you know, do not sign in - tell the ISA." *Reworded after review, the
  same day: see "After review" at the end of this section.*
- **A footer link to My Profile** where the switch is ("Choose which emails you
  get"), as text plus the address.

`case_request` gets its own fuller layout:

- an amber **"Action needed"** chip over the heading;
- "Hello <their name>," (the name they typed, `entered_name`; no greeting
  without one);
- the lead: "<asker>, <social worker | the ISA>, asked you to take case C-0042.
  It joins your records only once you accept it.";
- a fact card: **Case** C-0042, **Asked by** <asker>, **Asked on** <when>;
- three steps: 1. Open NACC SYS and sign in. 2. Find C-0042 under "Waiting for
  your answer" at the top of your Dashboard. 3. Accept it, or decline it with a
  reason for the social worker.
- the button **Review the request** -> `/`, the plain address, the safety line.

Still no child's name, case type, age, purpose or anything else about the
child, and no greeting by a colleague's address.

### Not done: what the owner should decide (security)

- **Never a link that signs someone in.** A "magic link" would hand access to
  whoever the email is forwarded to, mail scanners open links automatically,
  and a token in an address leaks through history and logs. The button opens
  the sign-in page; signing in is the password or Google, as always.
- **The sender is a gmail.com address sent through Brevo.** Brevo cannot sign
  mail for gmail.com, so it fails Gmail's sender checks (DMARC): it is likelier
  to land in spam, may be shown "via" Brevo or with a Brevo address, and
  anyone can open a look-alike Gmail account and send a convincing fake. The
  fix is to send from a domain the agency controls (e.g. a `notifications@`
  address on the agency's own domain), authenticated in Brevo with SPF, DKIM
  and DMARC. That is a Brevo and DNS change, not code.
- **Brevo rewrites links for click tracking** on transactional mail, so the
  button's real target is a Brevo tracking address that forwards to the site,
  and Brevo learns who clicked. There is no API switch to turn it off; Brevo
  support can, case by case. The plain-text address above is the workaround.
- **Plain `http` beyond one PC sends passwords readable on the network.** A
  local copy reached from other devices should be served over https; the
  hosted copies already are.

### As built (23 Oct 2026)

Where the build differs from, or settles, the plan above.

- **The old UID is rebuilt from the CORS rule, not from `SITE_URL`**
  (`children/notifications.py origin_from_cors`, used by
  `scheduling/calendar_invite.py legacy_uid`). Invites already out were built
  from the CORS list. A deployment that sets `SITE_URL` in the same release
  would otherwise work out a different tag than its calendars hold, and the
  first move would add an event beside each old one.
- **The appointment row is locked before the invite rows** in `_invite`. The
  rows are per attendee, so two first invites to two people (a session handed
  over while another change is handled) would each lock only their own row and
  each draw a UID. `scheduling/tests/test_event_uid_race.py` shows it on
  PostgreSQL: with the lock the second send waits and reuses the first's UID;
  without it each draws its own. Skipped on SQLite, which cannot show it.
- **The address line and the safety line need a button.** Both talk about "the
  button" and "the address above"; with no button asked for or no address
  known, neither is printed. The footer's way to My Profile needs only the
  address.
- **The host is written in lower case** and limited to letters, digits, dots
  and hyphens (or an IPv6 address): a space or a letter outside ASCII is a
  typo, and it is going into a link.
- **Under `manage.py test` `SITE_URL` is empty** whatever the environment
  says, so a value set in `backend/.env` for a copy others reach cannot change
  the links every email test pins.
- **`case_request`'s lead keeps the role when the asker has no name**: "A
  colleague, a social worker, asked you to take case C-0042." A role that is
  neither (only these two can ask) is not named.
- **Contrast of the chip**: `#92400e` on `#fef3c7` is 6.37:1 (AA asks 4.5:1);
  the brand amber `#b45309` is 4.51:1 and was not used.
- **The lead names the role with an article**: "Editha Pascua, a social worker,
  asked you..." or "Ada Admin, the ISA, asked you..." (`_ASKER_ROLE`), where the
  plan above wrote `<social worker | the ISA>`.

### After review (23 Oct 2026)

What the first round-two build got wrong or left rough, found by rendering every
email at 640 and 375 px and by a security read of the change. Each change has
a test; the rest is left for the owner, below.

- **The safety line was reworded.** It had asked the reader to compare the
  address printed under the button with the one they know. That address is
  only text, independent of where the button goes: a forged email can print the
  genuine address, carry the same sentence and point its button elsewhere, so
  the email taught the one check a look-alike passes. It also said the button
  "only opens the sign-in page", which is not so in a tab that is already signed
  in, and sat under buttons called "Open the schedule". It now reads: "NACC SYS
  never asks for your password or a code by email, and nothing in this email
  signs you in. Not sure it is from us? Do not use the button: open NACC SYS
  from your bookmark or the address you already know, and tell the ISA." The
  check left is the one that does not use the email. The printed address stays,
  for the person who has no bookmark yet, and no longer claims to prove
  anything.
- **A name is printed as one plain line, at most 60 characters**
  (`accounts.display.entered_name`, `one_line`). Anyone can ask for an account
  under any name and the name is then printed in a subject, a lead sentence, a
  fact row and a log line of an email that really does come from the agency.
  Newlines, right-to-left overrides and zero-width characters are dropped or
  made one space, and a longer name is cut with an ellipsis. A name made only of
  such characters reads as the fallback ("A colleague"). The invite already
  dropped them; the emails did not. The sign-up and user forms still accept
  such a name (see below).
- **The address under the button is on its own line and breaks only if it
  must** (`overflow-wrap:anywhere`), and so does the address in the footer. It
  had been cut in the middle of a word on a phone ("exam" / "ple.ph"), and the
  footer's did not wrap at all.
- **A time stays whole in the page**: "10:00&nbsp;AM", so a narrow screen never
  leaves "AM" alone on a line. The text part keeps the ordinary space.
- **The hidden preview line** also collapses to a point and takes the page's own
  background as its text colour, for a client that drops `display:none`.
- **A button's path must begin with `/`** (`render` raises, `notify` logs it
  and sends nothing). The path is appended to the site's address, and one that
  began with `@elsewhere.example` would have made the host somewhere else. No
  caller does this.
- **`SITE_URL` refuses an IPv6 address with a zone** (`[::1%eth0]`). Python
  accepts almost any text after the `%`, which no browser can open and the
  emails would print.

Left open, with the owner:

- **Nothing limits how many of these a person can cause.** A social worker can
  alternate the psychologist on a record, and each change asks one and
  withdraws the other: 60 requests made 119 emails. They share Brevo's daily
  allowance with the temporary-password and sign-up-code emails. The cap has
  to be counted in the database (the cache is per process) and the numbers are
  the owner's to choose, for example one email per psychologist and record per
  ten minutes and a ceiling per recipient per hour. Not done: a repeat ask
  inside the window would then be mailed to nobody, which can hide a live
  question from the psychologist.
- **Names are still accepted with invisible characters** at sign-up and in the
  user form (`accounts/names.py` refuses only U+FFFD), so the ISA approving a
  request does not see them. The emails no longer print them; refusing them
  where they are typed would also show the ISA what will be printed.
- **The fallback site address is not checked.** With `SITE_URL` unset the link
  is still the first deployed `CORS_ALLOWED_ORIGINS` entry, as before, and a
  malformed entry would reach the button and the printed address. Setting
  `SITE_URL` on each hosted copy puts the checked value in its place.
- **The calendar identity can still split in one rare case**: the previous
  release inserts an invite row during a deploy, after this release has already
  invited someone else of the same session, so that one person's next change
  adds an event beside the old one.
- **Dark mode was not checked** on a real phone. The design is light with every
  colour set, and nothing in a desktop emulation changed. If iPhone Mail or the
  Gmail app inverts it badly, add `<meta name="color-scheme" content="light">`
  and `supported-color-schemes` (Apple Mail honours them; Gmail ignores them).
- **The session and handover emails repeat the heading as their first
  sentence** ("Rosa moved a session on your calendar" over "Rosa moved a
  session on your calendar for case C-0042"). The case request avoids it with a
  short heading and a longer lead; the rest can be tidied the same way.
- **The main recommendation is still the sender.** A gmail.com address sent
  through Brevo fails Gmail's sender checks, and no wording in the email
  can make up for that: a look-alike from a free account looks the same. Sending
  from a domain the agency controls, with SPF, DKIM and DMARC set in Brevo,
  is what lets a mail app tell the real one from a copy.

## Round three: one email for "accepted" and the first session (24 Oct 2026)

**Owner's request:** "combine them into one email" - when a psychologist
accepts a child and books the first session in the same dialog, the social
worker got two emails seconds apart ("accepted", then "booked a session").

### Why it cannot simply be one send

Accepting and booking are two saves: `POST /assignment-requests/<id>/accept/`,
then the "When will you see them first?" dialog (`ScheduleFirstSession` in
`components/AssignmentRequests.jsx`) books through `POST /appointments/` -
or the psychologist picks "Book later", or closes the dialog, or closes the
tab. The acceptance email therefore waits until the dialog is over, and there
is no background scheduler to hold it (daemon threads die with the process;
see "Text messages"). So the acceptance is recorded as **not yet mailed**,
and whichever of these comes first sends it, once:

1. **The psychologist books** (the dialog's Schedule now or From my
   availability, or the Calendar): ONE email to the social worker -
   "Maria Santos accepted case C-0042 and booked the first session" - with the
   session's time, and the usual "booked a session" email to the social worker
   is not sent for that booking.
2. **The dialog ends without a booking** (Book later, Finish, the close
   button, Escape): the screen calls
   `POST /assignment-requests/<id>/mail-answer/`, and the email goes as an
   acceptance only: "... No session is booked yet; you will get an email when
   one is." Closing the tab with the dialog open sends the same call with
   `fetch(..., {keepalive: true})` on `pagehide`.
3. **Nothing came at all** (the browser crashed, the network dropped): a
   sweep sends every acceptance still unmailed **10 minutes** after it was
   made (`ANSWER_MAIL_GRACE`). It runs when anybody's screen reads the
   activity feed (every screen does after every save, and at sign-in), at most
   once a minute per process, and from `send_session_reminders`. If a session
   was booked by then, by anyone, the email names the next one.

"Sent once" is a conditional UPDATE on `answer_mailed_at IS NULL`: whoever
sets it sends, everyone else does nothing, so a booking, the dialog's call and
the sweep can race and the social worker still gets one email.

### The data

`AssignmentRequest.answer_mailed_at` (children 0032, nullable, additive). The
same migration stamps every request already answered (accepted, declined,
withdrawn) with its `decided_at`, so the sweep never mails an old acceptance.
A request accepted by the release still serving during a deploy has it NULL
and is mailed by the sweep - after that release mailed it on accepting, as it
always did, so for that window the social worker can get the acceptance twice.
Rare, bounded by the length of one deploy, and not worth a boot-time check.

A **decline** is mailed at once, as before: nothing follows it. It stamps
`answer_mailed_at` too, so the column means the same for every answer.

### Who gets what

- The combined email is kind `case_accepted`. A social worker who switched
  "A psychologist accepts" off but keeps "Sessions for my children" on gets the
  ordinary "booked a session" email for that booking instead.
- Only a booking BY the psychologist who accepted combines. The ISA or the
  social worker booking in the meantime sends what it always sent, and the
  acceptance still goes by 2 or 3, naming the next session.
- After the acceptance is mailed, every later booking is the ordinary email.
- The psychologist still gets nothing about their own booking.

### Wording

- With the psychologist's booking: subject "{psych} accepted case {ref} and
  booked the first session"; body "{psych} accepted case {ref}. It is now in
  their records, and the first session is booked."; facts Case, First session
  (the time span); button Open the schedule -> /schedule.
- Without a session: subject "{psych} accepted case {ref}"; body "{psych}
  accepted case {ref}. It is now in their records. No session is booked yet;
  you will get an email when one is." (the last clause only when the social
  worker has "Sessions for my children" on); button Open the record ->
  /report/child/<id>.
- Without the psychologist's booking but with one found: subject "{psych}
  accepted case {ref}"; facts Case, Next session.

### `POST /assignment-requests/<id>/mail-answer/`

Only the psychologist who accepted (403 for anyone else, 404 outside their
scope as the viewset already does), only for an ACCEPTED request (409
otherwise). Idempotent: `{"mailed": true}` the first time, `{"mailed": false}`
after. Never sends anything but the acceptance email of that request.

### After review (24 Oct 2026)

What a read of the built change found, and what was done. Each change has a
test; the rest is left, below.

- **"Booked the first session" is said only of a first session.** The combined
  email used to fire for any booking by the psychologist who accepted, so after
  the ISA had booked 9:30 their own 10:30 went out as "the first session", and
  so did a booking for a child who had sessions with them before. It now
  combines only when the psychologist has no other session with the child that
  is not cancelled (`accepted_with_first_session`); otherwise the booking is
  the ordinary email and the acceptance stays owed, going by the dialog's call
  or the sweep as "Next session" naming the earliest. In that case the
  acceptance can wait for the sweep (up to ten minutes): the dialog sends no
  call after a booking.
- **A superseded acceptance is dealt with and not mailed** (`_superseded`): the
  child is no longer with that psychologist, or a later request for the child
  has been accepted. Marivic accepted, Jose accepted, Marivic accepted again
  used to mean three acceptance emails, the first two untrue by the time they
  went. A question still open to somebody else does not supersede: until they
  accept, the child is where it was.
- **A failure in the combined email gives the claim back.** The claim was taken
  first and the email prepared after, so a failure left the social worker with
  neither email and the sweep with nothing to find. Now the booking's own email
  goes and the acceptance stays owed. A social worker who switched the
  acceptance off is not a failure: the claim stays taken.
- **A case closed before the mail goes** reads "{psych} accepted case {ref},
  which has since been closed." and promises no email. The recipient is still
  whoever holds the record when it is sent, as for every session email: a
  record the ISA has moved since is the new holder's to act on, and the bell
  and the "Records moved to me" email follow it the same way.
- **The feed's own sweep mails ten at a time** (`FEED_SWEEP_BATCH`). It rides on
  the one request every screen waits for, and a full batch of 50 cost about 250
  queries. The reminder run, which nobody waits for, still takes `SWEEP_BATCH`.
- **The dialog cannot be left while the booking is in flight.** Escape, the
  close button and "Book later" sent the acceptance on its own, and the booking
  then landing sent a second email. They are off while "Booking..." shows
  (`dismissible={!busy}`, as in the upload and transfer drawers). A request that
  never answers would hold the dialog; reloading still sends the call
  (`pagehide`).
- **The two switches say what they cover.** "A psychologist accepts" also gives
  the first session's time when the psychologist books it as they accept, and
  "Sessions for my children" says that booking comes in the acceptance email
  instead. The combined email is governed by `case_accepted` alone.

Left as it is, with the reason:

- **Withdrawn requests keep `answer_mailed_at` empty.** Nothing is owed on a
  withdrawal (an accepted request cannot be withdrawn), and every query that
  looks for owed emails filters on `status`, which a test now pins.
- **The "booked a session" email can arrive before "accepted"** when a booking
  and the dialog's call race. Both are true and each is sent once.
- **Browser Back, or a killed browser, leaves the acceptance to the sweep.** A
  route change unmounts the dialog without sending, on purpose (development's
  StrictMode would send it on open); the sweep sends it after ten minutes.


## Round four: a limit on the emails one account can cause (25 Oct 2026)

**Owner's request:** "add a limit on how many emails one account can trigger"

### Why

Every email in the catalogue is caused by somebody's action, and nothing
bounded how many one account's actions could cause. Picking one psychologist
and then another on a record, back and forth, mailed "asked" and "withdrawn"
to both on every save; rescheduling a session over and over mailed an invite
each time. A careless account annoys colleagues and gets the sender marked as
spam; a stolen one can mail every colleague all day and use up the Brevo
quota the temporary-password emails also depend on.

### The limits

Counted per **actor** (the `by` of the domain function: the person whose
action caused the email), over rolling windows, for the notification
catalogue only (`notify()` in `accounts/email_notifications.py`):

| Limit | Default | Setting |
|---|---|---|
| One account to one colleague | 20 an hour | `EMAIL_LIMIT_PER_RECIPIENT_HOUR` |
| One account to everyone | 60 an hour | `EMAIL_LIMIT_PER_HOUR` |
| One account to everyone | 150 a day (24 hours) | `EMAIL_LIMIT_PER_DAY` |

Ordinary work stays far under them: a social worker booking a whole week for
fifteen children is fifteen emails, a psychologist accepting ten cases is ten.
An email with no actor (none today) is not limited. A person's own "Calendar
update" counts against their own budget.

Not covered, on purpose: the temporary password (only the ISA can cause it,
and the screen already shows the password when the email does not go), the
sign-up code (it has its own limits per request and per address) and the
Settings test email (it goes to the ISA's own address).

### What happens at the limit

- The email is **held**, not queued: nothing is sent, and nothing is counted
  as sent (a calendar invite's SEQUENCE is not bumped, the attachment
  callable is not called). With no `BREVO_API_KEY` the log line says
  `EMAIL (held: limit) <kind> to <address>: <subject>` instead of
  `EMAIL (console) ...`, so the limit can be seen working on the local copy.
- **The recipient is told once**: the first held email from that actor to
  that recipient in a rolling hour sends one notice instead, "More updates are
  waiting for you in NACC SYS" - "<actor> has made more changes that affect you
  lately than NACC SYS sends emails for. Sign in to see them: your Dashboard
  and the notifications list show everything. Emails about their changes start
  again once they have slowed down: within the hour, or within a day at the
  longest." (As planned it said "in the last hour" and "within the hour"; see
  "After review".) No case reference, no kind of change.
  It goes only if the recipient has not switched off the held email's kind,
  and it does not count toward the limits (it is bounded by itself: one per
  actor and recipient per hour, three in 24 hours). This matters because a held email can be the
  one that would have corrected an earlier one ("asked" after "withdrawn");
  the notice says to look rather than letting the last email stand as the
  truth.
- **The ISA is told once**: the first time an account reaches any limit in a
  rolling hour, one SECURITY line goes into the activity log (`ActivityLog`
  kind `email_limit`, actor = that account, entity the account), and the
  ISA console's security log shows it as "<name> reached the email limit;
  further emails are held for up to a day" ("an hour" as planned; see "After
  review"). An account doing this may be
  careless or may be stolen. The line names no recipient and no case.
- **The bell, the Dashboard and every screen are unaffected.** Emails are
  nudges; what the system holds is the record. (The ISA's bell did show the
  line as built; review took it out again, see "After review".)

### The data

`EmailSendRecord` (accounts, `tbl_email_send_record`): `actor` (FK user,
SET_NULL, nullable), `recipient` (FK user, CASCADE), `kind` (the catalogue key,
or `overflow` for the notice), `created_at` (indexed). One row per email
QUEUED (or logged in console mode), written in `notify()` just before
`_queue`. It holds who emailed whom and when, never a case or any text, and
rows older than 25 hours are deleted by the existing once-a-minute sweep
(`maybe_sweep`) and by `send_session_reminders`, so nothing older than a day
plus an hour is kept. A database table and not the cache, for the reason the
SMS limits are rows: each gunicorn worker has its own cache, and a limit kept
there multiplies by the worker count.

Counting is two COUNT queries per email. Two emails caused at the same instant
can both pass the check, so the limit is soft by the number of requests in
flight at once; that is accepted.

### Tests and settings

Under `manage.py test` the limits are off (`None`), as the export throttle
is, because many existing tests send more than twenty emails from one account;
the tests of this feature turn them on with `override_settings`. A value of
`None` or `0` in a setting means no limit of that kind.

### What was built (25 Oct 2026)

As planned, with these choices made while building:

- **One aggregate query, not two COUNTs.** The three windows are counted in a
  single `Count(..., filter=...)` over the actor's rows of the last 24 hours.
- **Windows are strictly "after now minus the window"**: an email exactly an
  hour old is out of the hour, so "an hour later" flows again without a second
  to spare.
- **The ISA's line is a bulk kind in the console's security log**
  (`_EMAIL_LIMIT` in `admin_overview.py`, newest five shown and "and N more
  email limits not shown" under them). One account can write one a hour for as
  long as it goes on, which is the flood the cap exists for. It is picked out
  by `ActivityLog.kind` and its SECURITY category, and the sentence is built
  from the line's actor, never read back out of its label.
- **A hold lasts "up to an hour" for the two hourly limits only.** The daily
  limit can hold longer. As built the notice and the console line said an
  hour and a colleague still held an hour on was told again then; review
  replaced that (see "After review"): the wording no longer promises the hour,
  and the repeat is capped at three notices a day.
- **Notices are not counted by the three limits.** As built they were bounded
  only by one per actor and colleague an hour; review found that was not a
  bound over a day (see "After review", the first item).
- **Console sends are counted**, so the limit can be watched on the local copy,
  and `EmailSendRecord` rows are written even when every limit is off, so one
  switched on later counts from a true picture.
- **The feed's bell showed the line** as built (`utils/activity.js`); review
  took it out of the bell again, as the plan said (see "After review"). The
  Users drawer reads it as "Updated · <name> reached the email limit" under
  that account.


### After review (25 Oct 2026)

Changes made after the review of the build, to what the plan above says:

- **The notice has a budget of its own: at most one an hour and
  `OVERFLOW_PER_DAY` (3) in 24 hours per actor and colleague.** As built it
  was once an hour only, and an account held by the DAILY limit stays held for
  most of a day, so a stolen account reaching twelve colleagues cost 150 emails
  plus a notice to each of them every hour: 428 in a day, measured. What one
  account can now cause in 24 hours is the daily limit plus three notices a
  colleague. The notice no longer says the emails "start again within the
  hour" (false under a daily hold) or that the changes were "in the last
  hour": it says they start again once the account has slowed down, within the
  hour or within a day at the longest. The console's line says "held for up to
  a day" for the same reason. The first hold's notice still comes at once; the
  later ones are for a colleague who is mailed again after it and held again.
- **The line is out of the ISA's bell** (`/api/activity/` leaves
  `email_limit` out, as it does a move's per-record lines), which is what the
  plan said and the build did not do: it is written as often as once an hour
  for as long as the account goes on, and a few such accounts fill the feed's
  50 lines. The console shows it (newest five and a count) and the account's own
  drawer lists it. The rule in `utils/activity.js` that made the bell read it
  was taken out with it.
- **A held email is not tried again, an acceptance included, and the code now
  says so.** The claim on an acceptance stands (`claim_answer_mail`), and
  `POST /assignment-requests/<id>/mail-answer/` answers `mailed: true` as it
  already did for a social worker who switched the email off: `mailed` is "dealt
  with", not "an email left". A retry was left out on purpose: it changes the
  plan (a held email is held, whatever it was), a decline has no claim to give
  back, and a held acceptance retried by the sweep is held again every minute
  and crowds the newer ones out of the ten it takes. The social worker has the
  notice (when that hour's was not already sent, which is the usual case once
  the psychologist has been held, they already have it). A held calendar invite
  is lost the same way: the psychologist's external calendar is stale until the
  next change that is not held.
- **Soft, not exact, and said so.** The "once" of the notice and of the ISA's
  line is a check followed by a write with no lock, like the limits, so a burst
  of held emails in the same instant can each send a notice; the number is
  bounded by the requests in flight. A lock on the actor's row would serialise
  every email-causing request of one account, and inside a request's own
  transaction it would hold the lock to the end of it, in an order other
  locks (the transfer's record locks) do not share. Not worth it for a nudge.
- **Three tests were added that the mutation run showed missing**: a row 23
  hours old still counts toward the day, a second account held toward the same
  colleague is told about too, and a notice that fails does not stop the ISA's
  line (nor the line the notice).
- **Left as built:** a psychologist held on their own "Calendar update" is
  mailed a notice that names themselves. Skipping it would leave their
  external calendar stale with nothing said, which is worse than an odd
  sentence; wording it for the one person who is both can come with the owner's
  revision of the email texts.
