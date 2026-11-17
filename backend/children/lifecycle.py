"""The case's open/closed lifecycle (Child.status).

Closing goes through Terminate, which records why; opening again goes through
Reopen. Child.case_status mirrors the same fact, so both doors set it with the
move. The record edit never changes either.
"""
from config.lifecycle import Lifecycle
from children.models import Child

CHILD_LIFECYCLE = Lifecycle(
    Child, "status",
    {Child.ACTIVE: {Child.INACTIVE}, Child.INACTIVE: {Child.ACTIVE}},
    name="Child.status")

TERMINATE = "Close a case with Terminate, which records why."
REOPEN = "Reopen the case from its record."

DOORS = {
    (Child.ACTIVE, Child.INACTIVE): TERMINATE,
    (Child.INACTIVE, Child.ACTIVE): REOPEN,
    "*": f"{TERMINATE} {REOPEN}",
    "create": "A new record is always open.",
}

LOST_RACE = "This case was closed or reopened a moment ago. Reload to see it."
