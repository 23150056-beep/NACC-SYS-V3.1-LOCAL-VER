"""Give the records that already exist a social worker (24 Sep 2026).

Nothing recorded whose record a child was until 0024, so this is the owner's
chosen best guess, in this order:

1. whoever filed the child's latest case referral, if a staff account did -
   the rule the calendar used for names until now, so nobody's calendar
   changes when this runs;
2. otherwise whoever the activity log says created the record, if a staff
   account did.

Anything left has no social worker, and only an administrator sees it until
one is assigned. An administrator's upload or creation is never read as
ownership: administrators are not social workers.

Only rows still without a social worker are touched, so it can never undo an
assignment, and `.update()` leaves `updated_at` alone - the record form
compares it to catch a colleague's edit, and a migration is not one.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Child = apps.get_model("children", "Child")
    CaseReferral = apps.get_model("clinical", "CaseReferral")
    ActivityLog = apps.get_model("activity", "ActivityLog")
    User = apps.get_model("accounts", "User")

    staff = set(User.objects.filter(role__role_name="Staff").values_list("id", flat=True))
    if not staff:
        return
    owner = {}
    # Second choice first, so the first choice overwrites it.
    created = (ActivityLog.objects
               .filter(entity_type="Child", action="created",
                       entity_id__isnull=False, actor_id__in=staff)
               .order_by("created_at", "id").values_list("entity_id", "actor_id"))
    for child_id, actor_id in created:
        owner.setdefault(child_id, actor_id)
    referred = (CaseReferral.objects.filter(uploaded_by_id__in=staff)
                .order_by("created_at", "id").values_list("child_id", "uploaded_by_id"))
    for child_id, uploader_id in referred:
        owner[child_id] = uploader_id  # oldest first, so the latest wins

    by_owner = {}
    for child_id, user_id in owner.items():
        by_owner.setdefault(user_id, []).append(child_id)
    for user_id, child_ids in by_owner.items():
        Child.objects.filter(pk__in=child_ids, social_worker__isnull=True).update(
            social_worker_id=user_id)


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0024_child_social_worker"),
        ("clinical", "0011_report_check"),
        ("activity", "0003_drop_ai_tables"),
        ("accounts", "0010_google_requests_are_already_verified"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
