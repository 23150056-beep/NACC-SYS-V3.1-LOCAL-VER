"""Previous Custodian becomes the (present) Custodian, with a contact number
the system may text (owner's decision, 29 Sep 2026).

The rename is STATE ONLY: the Django field is `custodian_name` from now on,
and the column keeps its name, `surrendered_by`, through `db_column`. No SQL
runs for it, so a release still serving while this migrates - which reads
`surrendered_by` - keeps working. That is the lesson of children 0020, where a
real column rename would have made the old API answer 500 on every child query.

Everything else is additive: the contact number, the consent and when and by
whom it was recorded, when the number was confirmed, and the table holding the
one-time codes.

The three values the field held as a pick list until 24 Sep 2026 - Social
Worker, Police, Relatives - said who SURRENDERED the child. None is anybody a
child lives with, so they are cleared rather than shown as a custodian; the
record form names the field as still blank on the next edit. Names typed since
are kept: the owner's word is that they are mostly the present custodian.
Nothing here invents a custodian for a record - demo data gets its sample
names from the demo commands (children/demo_custodians.py), never from a
migration that also runs against real records.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

PLACEHOLDERS = ["Social Worker", "Police", "Relatives"]


def clear_placeholders(apps, schema_editor):
    Child = apps.get_model("children", "Child")
    Child.objects.filter(custodian_name__in=PLACEHOLDERS).update(custodian_name="")


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0027_clinical_closure_reasons"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameField("child", "surrendered_by", "custodian_name"),
                migrations.AlterField(
                    model_name="child", name="custodian_name",
                    field=models.CharField(blank=True, db_column="surrendered_by",
                                           max_length=150),
                ),
            ],
            database_operations=[],
        ),
        migrations.AddField(
            model_name="child", name="custodian_contact",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="child", name="custodian_sms_consent",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="child", name="custodian_sms_consent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="child", name="custodian_sms_consent_by",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="child", name="custodian_contact_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CustodianContactCheck",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("number", models.CharField(blank=True, default="", max_length=16)),
                ("code", models.CharField(blank=True, default="", max_length=12)),
                ("tries", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                ("window_started_at", models.DateTimeField(blank=True, null=True)),
                ("sent_in_window", models.PositiveSmallIntegerField(default=0)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("requested_by", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="custodian_contact_check",
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "tbl_custodian_contact_check"},
        ),
        migrations.RunPython(clear_placeholders, migrations.RunPython.noop),
    ]
