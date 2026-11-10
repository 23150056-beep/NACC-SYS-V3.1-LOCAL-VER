"""A one-off window now REPLACES its weekday's weekly window on its own date.

Until that rule, a dated window sat beside the weekly one and ADDED hours to
the date. Left alone, every such pair would silently lose its weekly hours the
day the rule shipped. So each upcoming dated window whose weekday also has an
active weekly window is widened to cover both (earliest start, latest end) and
carries the capacities added up - the same fold the "combine" endpoint does -
so the date offers what it offered before. Weekly windows are untouched, and
no appointment is.

Past dates are left as they were. Not reversible (the pair is one window).
"""
from django.db import migrations
from django.utils import timezone


def fold_one_offs(apps, schema_editor):
    Block = apps.get_model("scheduling", "AvailabilityBlock")
    today = timezone.localdate()
    dated = (Block.objects.filter(active=True, date__isnull=False, date__gte=today)
             .order_by("psychologist_id", "date", "id"))
    groups = {}
    for b in dated:
        groups.setdefault((b.psychologist_id, b.date), []).append(b)
    for (psych_id, day), ones in groups.items():
        weekly = list(Block.objects.filter(
            psychologist_id=psych_id, active=True, date__isnull=True,
            weekday=day.weekday()))
        if not weekly:
            continue
        every = ones + weekly
        keep, rest = ones[0], ones[1:]
        keep.start_time = min(b.start_time for b in every)
        keep.end_time = max(b.end_time for b in every)
        keep.capacity = sum(b.capacity for b in every)
        keep.save()
        Block.objects.filter(pk__in=[b.pk for b in rest]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0008_custodian_told"),
    ]

    operations = [
        migrations.RunPython(fold_one_offs, migrations.RunPython.noop),
    ]
