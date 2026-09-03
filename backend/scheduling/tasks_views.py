"""The door a scheduler knocks on, since this deployment has no cron.

Render's free plan does not run scheduled jobs, and the paid add-on is not
worth buying for one text a day. So the job is exposed as an endpoint and
something free calls it — a GitHub Actions schedule, or any of the free cron
pingers. See docs/CLOUD-DEPLOYMENT.md §9c.

Guarded by a shared token rather than a signed-in account, because the caller
is a machine with no session. Three things make that safe enough for what it
does:

* **Unset means off.** With no token configured the endpoint 404s, so an
  unconfigured deployment has no extra surface at all.
* **Constant-time comparison**, so the token cannot be recovered a byte at a
  time from response timings.
* **What it can do is bounded.** Triggering it repeatedly cannot send repeated
  messages — the reminder records who it has told, so the second call today is
  a no-op. The worst a leaked token buys is knowing whether anyone has sessions
  tomorrow, which is why the response counts people rather than naming them.
"""
import hmac
import logging

from django.conf import settings
from django.http import Http404
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from scheduling.reminders import send_session_reminders

logger = logging.getLogger(__name__)


class SessionReminderTaskView(generics.GenericAPIView):
    """POST here once a day and the reminders go out."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = None

    def _check_token(self, request):
        expected = settings.SESSION_REMINDER_TOKEN
        if not expected:
            # Not configured: behave as though the route does not exist rather
            # than advertising a disabled feature.
            raise Http404

        supplied = request.headers.get("X-Task-Token", "")
        if not supplied:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:]
        return hmac.compare_digest(supplied, expected)

    def post(self, request):
        if not self._check_token(request):
            logger.warning("Session-reminder task called with a bad token")
            return Response({"detail": "Not authorised."},
                            status=status.HTTP_403_FORBIDDEN)

        dry_run = str(request.query_params.get("dry_run", "")).lower() in ("1", "true", "yes")
        report = send_session_reminders(today=False, dry_run=dry_run)
        logger.info("Session reminders for %s: %s sent, %s skipped",
                    report["date"], report["sent"], report["skipped"])
        # Counts, not names. A scheduler's logs are not a place for a caseload.
        return Response({
            "date": report["date"],
            "sent": report["sent"],
            "skipped": report["skipped"],
            "dry_run": report["dry_run"],
        }, status=status.HTTP_200_OK)
