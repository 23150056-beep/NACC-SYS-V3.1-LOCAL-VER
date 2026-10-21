# Email Notifications for Psychologists and Social Workers

**Date:** 2026-10-21
**Status:** Designed; built on `claude/busy-faraday-jl44nl`
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
| `case_request` | A case is waiting for your answer | {by} asked you to take case {ref}. It joins your records once you accept it. | Answer the request: `/` |
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
  local copy never share an id for one person's calendar.
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
