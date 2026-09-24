"""Previous Custodian becomes free text (24 Sep 2026, at staff's request).

Only the column's width changes in the database (50 -> 150); the choices were
never a database constraint. Widening is safe to run while the previous
release is still serving - unlike a rename, nothing it reads goes away.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0021_rename_orphan_and_na"),
    ]

    operations = [
        migrations.AlterField(
            model_name="child", name="surrendered_by",
            field=models.CharField(blank=True, max_length=150)),
    ]
