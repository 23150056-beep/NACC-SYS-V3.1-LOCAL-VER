"""Who gets told about tomorrow's sessions, and the record of having told them.

One implementation, called from two places: `manage.py send_session_reminders`
for a person at a keyboard, and a token-guarded endpoint for whatever runs on a
schedule. Two copies of "who should be reminded" would drift, and the one that
drifted would be the one nobody watches.
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from accounts.sms_notifications import notify_session_reminder
from scheduling.models import Appointment

# Long enough that a second run the same day is refused, and that a run just
# after midnight still remembers yesterday evening's.
_REMEMBER_SECONDS = 36 * 3600


def _told_key(user_id, day):
    return f"session-reminder:{user_id}:{day.isoformat()}"


def send_session_reminders(*, today=False, dry_run=False):
    """Text each psychologist a count of their sessions. Returns a report.

    Safe to call repeatedly: whoever has already been told about a given day
    is skipped, so an overlapping schedule or a retried request cannot
    double-text. That matters more than usual here — the schedule this runs on
    is a free external pinger, and those retry.
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
              "sent": 0, "skipped": 0, "lines": []}

    for psychologist, count in sorted(counts.items(), key=lambda kv: kv[0].pk):
        label = psychologist.fullname or psychologist.email

        if not psychologist.phone or not psychologist.phone_verified:
            report["lines"].append(f"skip  {label}: {count} session(s), no verified number")
            report["skipped"] += 1
            continue

        key = _told_key(psychologist.pk, target)
        if cache.get(key):
            report["lines"].append(f"skip  {label}: already told about {target}")
            report["skipped"] += 1
            continue

        if dry_run:
            report["lines"].append(f"would text {label}: {count} session(s) {word}")
            continue

        if notify_session_reminder(psychologist, count, when=word):
            cache.set(key, True, _REMEMBER_SECONDS)
            report["lines"].append(f"sent  {label}: {count} session(s) {word}")
            report["sent"] += 1
        else:
            report["lines"].append(f"skip  {label}: sending was refused")
            report["skipped"] += 1

    if not counts:
        report["lines"].append(f"no scheduled sessions {word} ({target})")
    return report
