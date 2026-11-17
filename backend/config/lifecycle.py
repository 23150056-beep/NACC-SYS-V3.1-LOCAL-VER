"""One door for every status lifecycle.

A lifecycle declares its states and the legal moves between them as a table.
Every status change goes through `Lifecycle.move()`, which is a conditional
UPDATE on the status the caller saw: a double click or a second tab loses the
race and gets False (the caller answers 409), so a side effect never runs twice.
The generic edit endpoint (PUT/PATCH) never changes status: `refuse_edit()`
accepts the same value being resent, because edit forms send everything, and
refuses a different one with a sentence naming the endpoint that does the move.
Its companion is `edit_fields()`: the edit saves with update_fields built from
it, so an edit never writes the status column (or anything derived from it)
back from a stale copy and undoes a move that won while the form was open.
A guard test calls `check_table()` so a new choice cannot be added to a model
without deciding where it may go.
"""
from django.core.exceptions import FieldDoesNotExist
from django.utils import timezone
from rest_framework import serializers


class Lifecycle:
    def __init__(self, model, field, transitions, *, name):
        self.model = model
        self.field = field
        self.name = name
        self.transitions = {k: frozenset(v) for k, v in transitions.items()}

    @property
    def states(self):
        return frozenset(self.transitions)

    def allows(self, frm, to):
        return to in self.transitions.get(frm, frozenset())

    def move(self, instance, to, *, expected=None, extra=None):
        """Move `instance` from `expected` (default: what it holds now) to `to`.

        Returns False when somebody else moved it first. Raises ValueError for
        a move the table does not list: callers check allows() and answer 400.

        The trap: QuerySet.update skips Model.save(), so anything save()
        derives from the status is not derived here. accounts.User.save sets
        is_active from status, so its callers must pass extra={"is_active": ...}.
        """
        if expected is None:
            expected = getattr(instance, self.field)
        if not self.allows(expected, to):
            raise ValueError(f"{self.name}: {expected!r} -> {to!r} is not a legal move")
        values = {self.field: to, **(extra or {})}
        if any(f.name == "updated_at" for f in self.model._meta.get_fields()):
            values["updated_at"] = timezone.now()
        matched = self.model._default_manager.filter(
            pk=instance.pk, **{self.field: expected}).update(**values)
        if not matched:
            return False
        for key, value in values.items():
            setattr(instance, key, value)
        return True

    def refuse_edit(self, instance, attrs, doors):
        """For a serializer's validate(): an edit may not change the status.

        `doors` maps (old, new) to the sentence naming the real door, with "*"
        as the fallback and "create" for a new record.
        """
        if self.field not in attrs:
            return
        new = attrs[self.field]
        if instance is None:
            default = self.model._meta.get_field(self.field).get_default()
            if new != default:
                raise serializers.ValidationError(
                    {self.field: doors.get("create") or doors["*"]})
        elif new != getattr(instance, self.field):
            old = getattr(instance, self.field)
            raise serializers.ValidationError(
                {self.field: doors.get((old, new)) or doors["*"]})
        attrs.pop(self.field)

    def edit_fields(self, instance, also_skip=()):
        """update_fields for an edit's save: every concrete field of the model
        except the primary key, the status column and `also_skip`.

        Include derived fields the edit must keep (a child's fullname) simply
        by not skipping them; skip the ones derived from the status (a user's
        is_active) and anything else the edit must not rewrite from a stale copy.
        """
        skip = {self.field, *also_skip}
        return [f.name for f in instance._meta.concrete_fields
                if not f.primary_key and f.name not in skip]

    def check_table(self):
        """Problems between the table and the model's choices, [] when sound."""
        problems = []
        choices = {c[0] for c in self.model._meta.get_field(self.field).choices}
        for value in sorted(choices - self.states):
            problems.append(f"{self.name}: choice {value!r} is not in the table")
        for value in sorted(self.states - choices):
            problems.append(f"{self.name}: state {value!r} is not a model choice")
        for frm, targets in self.transitions.items():
            for to in sorted(targets - self.states):
                problems.append(f"{self.name}: {frm!r} -> {to!r}, but {to!r} is not a state")
        return problems


class EditKeepsStatus:
    """Mixin for a ModelSerializer: update() saves with `lifecycle.edit_fields`.

    Set `lifecycle` and, if the status drives other columns, `edit_skip`.
    """
    lifecycle = None
    edit_skip = ()

    def update(self, instance, validated_data):
        many = {}
        for attr, value in validated_data.items():
            try:
                is_many = instance._meta.get_field(attr).many_to_many
            except FieldDoesNotExist:
                is_many = False
            if is_many:
                many[attr] = value
            else:
                setattr(instance, attr, value)
        instance.save(update_fields=self.lifecycle.edit_fields(instance, self.edit_skip))
        for attr, value in many.items():
            getattr(instance, attr).set(value)
        return instance
