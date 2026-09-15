import logging

from accounts.display import display_name
from activity.models import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(actor, action, category, *, entity_type="", entity_label="", entity_id=None, recipient=None):
    """Record an activity event. Never raises — logging must not break the request.

    `recipient` directs the event at a specific user (used for psychologist
    notifications, e.g. a newly assigned child).
    """
    try:
        is_user = bool(getattr(actor, "is_authenticated", False))
        label = display_name(actor, "System") if is_user else "System"
        ActivityLog.objects.create(
            actor=actor if is_user else None,
            recipient=recipient if getattr(recipient, "is_authenticated", False) else None,
            actor_label=label,
            action=action,
            category=category,
            entity_type=entity_type,
            entity_label=entity_label or "",
            entity_id=entity_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to write activity log")
