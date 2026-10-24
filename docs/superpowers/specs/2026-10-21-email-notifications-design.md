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

Tokens live in per-tab `sessionStorage`, so a link from an email always opens
a tab that is signed out. `ProtectedRoute` sends people to `/login` and
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
