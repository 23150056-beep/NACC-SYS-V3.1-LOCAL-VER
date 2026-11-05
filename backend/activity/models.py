from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    CREATED, UPDATED, ARCHIVED, LOGIN = "created", "updated", "archived", "login"
    # A psychologist asked to take a child, and their answer
    # (children/assignment.py). Entity type "Assignment"; the id is the child's.
    REQUESTED, ACCEPTED, DECLINED, WITHDRAWN = "requested", "accepted", "declined", "withdrawn"
    # A case study made final, and taken back to a draft (case_study/views.py).
    # Entity type "CaseStudy"; the id is the child's.
    FINALIZED, REOPENED = "finalized", "reopened"
    # A duplicate record taken out by the ISA (children/duplicates.py). Entity
    # type "Child"; the label reads "C-0050 (duplicate of C-0049)", since the
    # record it names is gone.
    REMOVED = "removed"
    # A list of children saved to a file by the browser (children/views.py
    # `exported`). The file is built on the screen, so this row is the only
    # record that anyone took the list out of the system and how long it was.
    EXPORTED = "exported"
    # Records moved from one social worker to another by the ISA (children/
    # transfer.py). Entity type "Child"; the id is the child's on the line for
    # each record, and none on the lines that tell the workers. The label is
    # the whole sentence: "Moved C-0042 · Ana Cruz from R. Santos to E. Pascua"
    # for a record, "Moved 9 records from R. Santos to E. Pascua" for a worker.
    TRANSFERRED = "transferred"
    ACTION_CHOICES = [
        (CREATED, "Created"), (UPDATED, "Updated"),
        (ARCHIVED, "Archived"), (LOGIN, "Login"),
        (REQUESTED, "Requested"), (ACCEPTED, "Accepted"),
        (DECLINED, "Declined"), (WITHDRAWN, "Withdrawn"),
        (FINALIZED, "Finalized"), (REOPENED, "Reopened"),
        (REMOVED, "Removed"),
        (EXPORTED, "Exported"),
        (TRANSFERRED, "Transferred"),
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
    # What a USER line IS, for the ISA's console (accounts/admin_overview.py).
    # The label cannot say: it opens with a person's name, and anyone may sign
    # up under any name, including one that reads like the end of a sentence
    # ("... access approved as Administrator"). So the code that writes the
    # line says what it is, and nothing reads it back out of the words. Blank
    # on lines written before this column existed and on kinds nothing needs
    # told apart. `db_default` lets a release that predates the column keep
    # inserting rows during a deploy.
    KIND_APPROVAL, KIND_DECLINE, KIND_ROLE = "approval", "decline", "role"
    KIND_RESET, KIND_REACTIVATE = "reset", "reactivate"
    # An account reached the limit on the emails its actions can cause
    # (accounts/email_notifications.py). A SECURITY line the account itself
    # caused, so its words are not trusted for what it is: the ISA's console
    # reads this kind (accounts/admin_overview.py). No choices, no migration.
    KIND_EMAIL_LIMIT = "email_limit"
    # The two kinds of a RECORD line the feeds leave out (activity/views.py).
    # A move of records writes a line for each record, as its audit trail, and
    # a sentence for each worker told: the feeds hold the newest 50 lines, so
    # the per-record ones would fill everybody's bell, and the administrator
    # would read the second sentence as a repeat of the first.
    KIND_MOVE_DETAIL, KIND_MOVE_COPY = "move_detail", "move_copy"
    kind = models.CharField(max_length=20, blank=True, default="", db_default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tbl_activity_log"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor_label} {self.action} {self.entity_type} {self.entity_label}".strip()
