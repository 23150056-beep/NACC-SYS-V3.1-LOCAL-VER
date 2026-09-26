"""Who gets told about tomorrow's sessions, and the record of having told them.

One implementation, called from two places: `manage.py send_session_reminders`
for a person at a keyboard, and a token-guarded endpoint for whatever runs on a
schedule. Two copies of "who should be reminded" would drift, and the one that
drifted would be the one nobody watches.
"""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.sms_notifications import notify_session_reminder
from scheduling.models import Appointment, SessionReminder


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
    deleted if it refuses so the next run tries again. Each message is sent
    before this returns, so "sent" in the report means the gateway accepted
    it.
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
              "sent": 0, "skipped": 0, "failed": 0, "lines": []}

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
        if result.ok:
            claim.sent_at = timezone.now()
            claim.save(update_fields=["sent_at"])
            report["lines"].append(f"sent  {label}: {count} session(s) {word}")
            report["sent"] += 1
        else:
            claim.delete()
            report["lines"].append(f"FAIL  {label}: {result.detail}")
            report["failed"] += 1

    if not counts:
        report["lines"].append(f"no scheduled sessions {word} ({target})")
    return report
