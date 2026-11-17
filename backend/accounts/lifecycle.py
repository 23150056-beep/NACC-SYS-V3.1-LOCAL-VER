"""The account status lifecycle: the one table every status move is checked against.

"Declined" and "deactivated" are both ARCHIVED; which one it is comes from
whether the account ever had a role, as before.
"""
from accounts.models import User
from config.lifecycle import Lifecycle

USER_LIFECYCLE = Lifecycle(
    User, "status",
    {
        User.PENDING: {User.ACTIVE, User.ARCHIVED},   # approve, decline
        User.ACTIVE: {User.ARCHIVED},                 # deactivate
        User.ARCHIVED: {User.ACTIVE},                 # reactivate
    },
    name="User.status",
)

# What the edit endpoint says when somebody tries to change the status through it.
EDIT_DOORS = {
    (User.PENDING, User.ACTIVE): "Approve it from Access Requests.",
    (User.ACTIVE, User.ARCHIVED): "Deactivate the account from its menu.",
    (User.ARCHIVED, User.ACTIVE): "Reactivate the account from its menu.",
    "create": "An account is created active. Its status changes only through "
              "Approve, Decline, Deactivate and Reactivate.",
    "*": "The status changes only through Approve, Decline, Deactivate and "
         "Reactivate. It may have changed since this form was opened - open it "
         "again.",
}

LOST_RACE = "This account changed while you were looking at it. Reload and try again."


def is_active_for(status):
    """What User.save derives; queryset updates skip save, so move() callers pass it."""
    return status == User.ACTIVE
