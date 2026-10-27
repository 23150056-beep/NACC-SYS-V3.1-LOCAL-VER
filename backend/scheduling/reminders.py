"""Who gets told about tomorrow's sessions, and the record of having told them.

One implementation, called from two places: `manage.py send_session_reminders`
for a person at a keyboard, and a token-guarded endpoint for whatever runs on a
schedule. Two copies of "who should be reminded" would drift, and the one that
drifted would be the one nobody watches.
"""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.phone import InvalidPhilippineMobile, normalise_ph_mobile
from accounts.sms_notifications import notify_session_reminder, remind_custodian_group
from children.custodian import texts_allowed
from scheduling.models import Appointment, CustodianReminder, SessionReminder
from scheduling.visibility import case_ref


def _claim(rows, psychologist, day, count):
    """The day's row for this psychologist, now ours to send - or None.

    None when it was already sent, or when another run claimed it within the
    lease and may still be sending. A claim older than the lease that was
    never stamped sent belongs to a run that died mid-send; it is taken over
    by one conditional UPDATE, so two runs cannot both take it.
    """
    try:
        with transaction.atomic():
            return SessionReminder.objects.create(
                psychologist=psychologist, day=day, session_count=count)
    except IntegrityError:
        pass
    now = timezone.now()
    stale = rows.filter(sent_at__isnull=True,
                        claimed_at__lt=now - SessionReminder.CLAIM_LEASE)
    if not stale.update(claimed_at=now, session_count=count):
        return None
    return rows.get()


def send_session_reminders(*, today=False, dry_run=False):
    """Text each psychologist a count of their sessions. Returns a report.

    Safe to call repeatedly: whoever has already been told about a given day
    is skipped, so an overlapping schedule or a retried request cannot
    double-text. That matters more than usual here — the schedule this runs on
    is a free external pinger, and those retry.

    Who was told is a SessionReminder row, claimed before the send so two
    overlapping runs cannot both text, stamped once the gateway accepts, and
    deleted only when the gateway definitely refused, so the next run tries
    again. An answer that was lost (outcome "unknown") may have been
    delivered and billed, so it is stamped like a sent one and reported as
    UNCONFIRMED: texting it again automatically would be paying twice for
    what may already be on the handset. Each message is sent before this
    returns, so "sent" in the report means the gateway accepted it.
    """
    target = timezone.localdate() + timedelta(days=0 if today else 1)
    word = "today" if today else "tomorrow"

    counts = {}
    for appointment in (Appointment.objects
                        .filter(start__date=target, status=Appointment.SCHEDULED)
                        .select_related("psychologist")):
        psychologist = appointment.psychologist
        if psychologist is not None:
            counts[psychologist] = counts.get(psychologist, 0) + 1

    report = {"date": target.isoformat(), "when": word, "dry_run": dry_run,
              "sent": 0, "skipped": 0, "failed": 0, "unconfirmed": 0,
              "lines": []}

    for psychologist, count in sorted(counts.items(), key=lambda kv: kv[0].pk):
        label = psychologist.fullname or psychologist.email

        if not psychologist.phone or not psychologist.phone_verified:
            report["lines"].append(f"skip  {label}: {count} session(s), no verified number")
            report["skipped"] += 1
            continue

        rows = SessionReminder.objects.filter(psychologist=psychologist, day=target)
        if dry_run:
            # Looks without claiming: a dry run that took the day's row would
            # leave the real run that follows nothing to send.
            if rows.filter(sent_at__isnull=False).exists():
                report["lines"].append(f"skip  {label}: already told about {target}")
                report["skipped"] += 1
            else:
                report["lines"].append(f"would text {label}: {count} session(s) {word}")
            continue

        claim = _claim(rows, psychologist, target, count)
        if claim is None:
            told = rows.filter(sent_at__isnull=False).exists()
            report["lines"].append(
                f"skip  {label}: " + (f"already told about {target}" if told
                                      else "another run is sending it now"))
            report["skipped"] += 1
            continue

        result = notify_session_reminder(psychologist, count, when=word)
        if result.refused:
            claim.delete()
            report["lines"].append(f"FAIL  {label}: {result.detail}")
            report["failed"] += 1
            continue
        claim.sent_at = timezone.now()
        claim.save(update_fields=["sent_at"])
        if result.ok:
            report["lines"].append(f"sent  {label}: {count} session(s) {word}")
            report["sent"] += 1
        else:
            report["lines"].append(f"UNCONFIRMED {label}: {result.detail}")
            report["unconfirmed"] += 1

    if not counts:
        report["lines"].append(f"no scheduled sessions {word} ({target})")
    _remind_custodians(target, word, dry_run, report)
    return report


def _claim_custodian(appointment):
    """This appointment's reminder row, now ours to send - or None. The same
    claim, lease and takeover as _claim above, one row per appointment."""
    try:
        with transaction.atomic():
            return CustodianReminder.objects.create(appointment=appointment)
    except IntegrityError:
        pass
    rows = CustodianReminder.objects.filter(appointment=appointment)
    now = timezone.now()
    stale = rows.filter(sent_at__isnull=True,
                        claimed_at__lt=now - SessionReminder.CLAIM_LEASE)
    if not stale.update(claimed_at=now):
        return None
    return rows.get()


# A custodian told about an appointment this recently (by the booked or moved
# text) does not also get the day-before reminder: the same news twice, a
# credit each, within hours of each other.
TOLD_RECENTLY = timedelta(hours=12)


def _custodian_key(appointment):
    """The number an appointment's custodian is texted on, the grouping key
    for siblings. Normalised, so the same handset typed two ways is one."""
    number = appointment.child.custodian_contact
    try:
        return normalise_ph_mobile(number) or number
    except InvalidPhilippineMobile:
        return number


def _remind_custodians(target, word, dry_run, report):
    """Text each custodian who agreed about their children's appointments on
    `target` (children/custodian.py). Lines name the case references, never
    the children: this report is printed to a terminal and a log.

    One text per custodian number and day: a custodian with several children
    coming in gets the sibling wording once rather than a text per child. Every
    appointment's row is claimed before the one send; the send's outcome
    decides them all together - sent or unknown stamps them (an unknown one is
    never resent), refused deletes them so the next run tries again.
    """
    appointments = (Appointment.objects
                    .filter(start__date=target, status=Appointment.SCHEDULED)
                    .select_related("child").order_by("start", "pk"))
    now = timezone.now()
    groups = {}
    for appointment in appointments:
        if not texts_allowed(appointment.child):
            continue
        told = appointment.custodian_told_at
        if told is not None and now - told < TOLD_RECENTLY:
            ref = f"custodian of {case_ref(appointment.child_id)}"
            report["lines"].append(f"skip  {ref}: told when booked")
            report["skipped"] += 1
            continue
        groups.setdefault(_custodian_key(appointment), []).append(appointment)

    for members in groups.values():
        refs = ", ".join(case_ref(a.child_id) for a in members)
        ref = f"custodian of {refs}"
        if dry_run:
            # Looks without claiming, as above.
            pending = [a for a in members
                       if not CustodianReminder.objects.filter(
                           appointment=a, sent_at__isnull=False).exists()]
            if not pending:
                report["lines"].append(f"skip  {ref}: already told")
                report["skipped"] += 1
            else:
                report["lines"].append(
                    f"would text {ref}: {len(pending)} appointment(s) {word}")
            continue

        claims = []
        for appointment in members:
            claim = _claim_custodian(appointment)
            if claim is not None:
                claims.append((appointment, claim))
        if not claims:
            report["lines"].append(f"skip  {ref}: already told")
            report["skipped"] += 1
            continue
        claimed = [a for a, _ in claims]
        ref = "custodian of " + ", ".join(case_ref(a.child_id) for a in claimed)

        result = remind_custodian_group(claimed, day=word)
        if result.refused:
            for _, claim in claims:
                claim.delete()
            report["lines"].append(f"FAIL  {ref}: {result.detail}")
            report["failed"] += 1
            continue
        stamped = timezone.now()
        for _, claim in claims:
            claim.sent_at = stamped
            claim.save(update_fields=["sent_at"])
        if result.ok:
            report["lines"].append(
                f"sent  {ref}: {len(claimed)} appointment(s) {word}")
            report["sent"] += 1
        else:
            report["lines"].append(f"UNCONFIRMED {ref}: {result.detail}")
            report["unconfirmed"] += 1
