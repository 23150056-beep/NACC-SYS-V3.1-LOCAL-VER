"""Social workers for demo children (24 Sep 2026).

Each social worker sees only the records they hold (accounts/scoping.py), so a
demo caseload with no `social_worker` is one no staff account can see at all -
the same fault, in a new place, as the seeded calendar nobody could book.

Round-robin across the active staff accounts actually on this database, like
the psychologists in import_demo_data: a fixture's user ids mean nothing here.
Only children without a social worker are touched, so a record somebody has
already been given is never moved.
"""
from django.contrib.auth import get_user_model

from accounts.models import Role
from children.models import Child

User = get_user_model()


def assign_social_workers(children):
    """Give each child without a social worker one. Returns how many were set."""
    workers = list(User.objects.filter(role__role_name=Role.STAFF, status=User.ACTIVE)
                   .order_by("pk"))
    if not workers:
        return 0
    todo = [c for c in children if c.social_worker_id is None]
    for index, child in enumerate(todo):
        child.social_worker = workers[index % len(workers)]
    Child.objects.bulk_update(todo, ["social_worker"])
    return len(todo)
