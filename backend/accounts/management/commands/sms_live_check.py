"""Send every message in the catalogue once to one handset, then ask Semaphore
what became of each and compare it with what the system recorded.

    manage.py sms_live_check --to 09XXXXXXXXX [--only KIND ...] [--dry-run] [--wait 180]

Why it exists: on 27 Oct 2026 a hand-run check found that nine of eleven
delivered texts were recorded as failed (docs/superpowers/specs/
2026-10-27-sms-credit-safety-design.md). A row where Semaphore and the system
disagree is that bug again, so this prints MISMATCH beside it and exits
non-zero. Semaphore's "Sent" means the network took the message, not that it
arrived: the operator still has to look at the handset.

It spends real credit, so it is never part of `manage.py test` (the tests patch
the network), it refuses the console provider and any hosted database, and it
asks for the number to be typed back before anything is sent. Each message goes
through send_sms exactly as the features send it, with the limits switched off
(a check of eleven kinds to one number would otherwise meet its own per-number
limit); the spend record is written as usual, subject "live-check".
"""
import secrets
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts import sms, sms_catalogue
from accounts.phone import InvalidPhilippineMobile, normalise_ph_mobile
from config.demo_guard import refuse_if_not_local

POLL_SECONDS = 15
GAP_SECONDS = 2
# Semaphore statuses that mean it has not finished yet.
IN_FLIGHT = ("pending", "queued")
GONE_WRONG = ("failed", "refunded")


def _text_for(entry):
    """(text for send_sms, code or None, text as the handset will read it)."""
    samples = dict(entry.samples)
    if entry.route == sms_catalogue.OTP:
        code = f"{secrets.randbelow(900000) + 100000}"
        samples.pop("otp", None)
        text = sms_catalogue.render(entry.key, **samples)
        return text, code, text.replace(sms_catalogue.OTP_PLACEHOLDER, code)
    text = sms_catalogue.render(entry.key, **samples)
    return text, None, text


def is_mismatch(recorded, message_id, semaphore):
    """Whether the system's record and Semaphore's status disagree.

    `recorded` is the SmsResult outcome, `semaphore` Semaphore's status word
    ("" when it could not be had, which is not a verdict either way).
    """
    if not message_id:
        return True
    said = (semaphore or "").lower()
    if recorded != sms.SENT and (said in IN_FLIGHT or said == "sent"):
        return True
    if recorded == sms.SENT and said in GONE_WRONG:
        return True
    return False


class Command(BaseCommand):
    help = ("Send each catalogue message once to one number and compare "
            "Semaphore's status with what the system recorded. Spends credit.")

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="A Philippine mobile number.")
        parser.add_argument("--only", nargs="+", metavar="KIND",
                            help="Only these catalogue kinds.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would be sent and stop.")
        parser.add_argument("--wait", type=int, default=180, metavar="SECONDS",
                            help="How long to wait for Semaphore's final status.")

    # -- refusals, all before anything is sent ---------------------------------

    def _refuse(self, options):
        provider = settings.SMS_PROVIDER
        if provider == "console":
            raise CommandError("SMS_PROVIDER is console, so nothing would reach a "
                               "gateway. Nothing was sent.")
        if provider != "semaphore":
            raise CommandError(f"SMS_PROVIDER is '{provider}'. The status lookup "
                               "is Semaphore-only, so this check needs "
                               "SMS_PROVIDER=semaphore. Nothing was sent.")
        if settings.SMS_ENDPOINT:
            raise CommandError("SMS_ENDPOINT overrides the gateway's address, so "
                               "Semaphore could not confirm what was sent. Unset "
                               "it. Nothing was sent.")
        if not settings.SMS_API_KEY:
            raise CommandError("SMS_API_KEY is not set. Nothing was sent.")
        # Before anything opens a database connection, as seed_demo_data does.
        refuse_if_not_local()
        try:
            number = normalise_ph_mobile(options["to"])
        except InvalidPhilippineMobile as exc:
            raise CommandError(str(exc))
        if not number:
            raise CommandError("--to needs a mobile number.")
        kinds = options["only"] or list(sms_catalogue.ENTRIES)
        unknown = [k for k in kinds if k not in sms_catalogue.ENTRIES]
        if unknown:
            raise CommandError(f"Not in the catalogue: {', '.join(unknown)}. "
                               f"Known: {', '.join(sms_catalogue.ENTRIES)}.")
        return number, kinds

    # -- output ----------------------------------------------------------------

    def _table(self, header, rows):
        widths = [max(len(str(r[i])) for r in [header] + rows)
                  for i in range(len(header))]
        for row in [header] + rows:
            self.stdout.write("  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip())

    def handle(self, *args, **options):
        number, kinds = self._refuse(options)

        plan = []
        for kind in kinds:
            entry = sms_catalogue.ENTRIES[kind]
            text, code, shown = _text_for(entry)
            by_code_route = code is not None
            credits = 2 if by_code_route else sms.segments(shown)
            plan.append((entry, text, code, shown, credits))
        total = sum(p[4] for p in plan)

        self.stdout.write(f"{len(plan)} messages to {"*" * (len(number) - 4)}{number[-4:]}:")
        self._table(("kind", "route", "chars", "segments", "credits"),
                    [(e.key, e.route, len(shown), sms.segments(shown), credits)
                     for e, _t, _c, shown, credits in plan])
        self.stdout.write(f"Estimated total: about {total} credits.")

        if options["dry_run"]:
            self.stdout.write("Dry run: nothing was sent.")
            return

        typed = input("Type the number again to send these (anything else aborts): ")
        try:
            confirmed = normalise_ph_mobile(typed) == number
        except InvalidPhilippineMobile:
            confirmed = False
        if not confirmed:
            self.stdout.write("Not confirmed. Nothing was sent.")
            return

        sent = []
        for position, (entry, text, code, _shown, _credits) in enumerate(plan):
            if position:
                time.sleep(GAP_SECONDS)
            result = sms.send_sms(number, text, entry.key, otp_code=code,
                                  subject=sms.LIVE_CHECK_SUBJECT, enforce_limits=False)
            self.stdout.write(f"  sent {entry.key}: {result.outcome}"
                              + (f" ({result.message_id})" if result.message_id else ""))
            sent.append((entry, result))

        statuses = self._poll(sent, options["wait"])

        rows, bad = [], 0
        for entry, result in sent:
            status, note = statuses.get(result.message_id, ("", ""))
            mismatch = is_mismatch(result.outcome, result.message_id, status)
            unverified = not mismatch and result.message_id and not status
            flag = "MISMATCH" if mismatch else ("UNVERIFIED" if unverified else "")
            bad += bool(flag)
            rows.append((entry.key, entry.route, result.message_id or "-",
                         result.outcome, status or note or "-", flag))
        self.stdout.write("")
        self._table(("kind", "route", "message_id", "system recorded",
                     "Semaphore status", ""), rows)
        self.stdout.write("")
        self.stdout.write("Now check the handset: Semaphore's \"Sent\" means the "
                          "network took the message, not that it arrived.")
        if bad:
            raise CommandError(f"{bad} row(s) did not agree (MISMATCH or "
                               "UNVERIFIED above).")

    def _poll(self, sent, wait):
        """{message_id: (status, why not)} once each is past Pending/Queued or
        `wait` seconds have gone. Time is counted by the sleeps, not read from
        a clock."""
        statuses = {}
        waiting = [r.message_id for _e, r in sent if r.message_id]
        waited = 0
        while waiting:
            still = []
            for message_id in waiting:
                status, note = sms.semaphore_message_status(message_id)
                statuses[message_id] = (status, note)
                if not status or status.lower() in IN_FLIGHT:
                    still.append(message_id)
            waiting = still
            if not waiting or waited >= wait:
                break
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        return statuses
