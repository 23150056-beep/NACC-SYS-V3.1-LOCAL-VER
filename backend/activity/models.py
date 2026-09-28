from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    CREATED, UPDATED, ARCHIVED, LOGIN = "created", "updated", "archived", "login"
    # A psychologist asked to take a child, and their answer
    # (children/assignment.py). Entity type "Assignment"; the id is the child's.
    REQUESTED, ACCEPTED, DECLINED, WITHDRAWN = "requested", "accepted", "declined", "withdrawn"
    ACTION_CHOICES = [
        (CREATED, "Created"), (UPDATED, "Updated"),
        (ARCHIVED, "Archived"), (LOGIN, "Login"),
        (REQUESTED, "Requested"), (ACCEPTED, "Accepted"),
        (DECLINED, "Declined"), (WITHDRAWN, "Withdrawn"),
    ]

    RECORD, USER, SECURITY = "record", "user", "security"
    CATEGORY_CHOICES = [
        (RECORD, "Record"), (USER, "User"), (SECURITY, "Security"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="activities")
    # Directs a notification at a specific user (e.g. the newly assigned psychologist).
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="notifications")
    actor_label = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    entity_type = models.CharField(max_length=50, blank=True)
    entity_label = models.CharField(max_length=255, blank=True)
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_activity_log"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor_label} {self.action} {self.entity_type} {self.entity_label}".strip()
