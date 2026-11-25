"""Changing an account's role must not strand the records it holds.

A social worker's active records are theirs (Child.social_worker). Change that
account to any other role and they sit with somebody who cannot hold them:
Transfer's usual refusal would no longer apply to the account, and a queue that
looked at roles alone would not list it. So a change AWAY from Staff is refused
while the account holds active records - Staff to Officer, and Staff to
Psychologist too (owner's plan, section 4) - and the way out is Transfer
records. Closed records do not count; a closed one reopened in place comes
back to its holder, which is why the console lists, and Transfer accepts, a
holder who cannot hold records.

The account's row is locked (select_for_update) before the count, and the
caller keeps the transaction open through the save, so a transfer that locks
the same row (children/transfer.py) cannot slip a record past the check.
"""
from accounts.models import Role


def records_still_held(user):
    """The number of active records this account holds as social worker. Locks
    the account's row first; the caller is inside a transaction.
    Child is imported here (accounts/scoping.py says why)."""
    from accounts.models import User
    from children.models import Child
    locked = User.objects.select_for_update().get(pk=user.pk)
    return Child.objects.filter(social_worker=locked, status=Child.ACTIVE).count()


def refusal_for(user, new_role):
    """The sentence that refuses changing `user` to `new_role`, or None."""
    old = getattr(getattr(user, "role", None), "role_name", None)
    if old != Role.STAFF or new_role is None or new_role.role_name == Role.STAFF:
        return None
    held = records_still_held(user)
    if not held:
        return None
    return (f"This account still holds {held} active record{'' if held == 1 else 's'}. "
            "Move them first (Transfer records).")
