"""Referral Source becomes a pick from RACCO / LGU / CCA / RCF (24 Sep 2026).

Choices are not a database constraint, so nothing in the table changes; the
text a record already holds stays, and the serializer keeps it valid until
somebody changes it (the same change-only rule as the retired values).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0022_previous_custodian_free_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="child", name="referral_source",
            field=models.CharField(blank=True, max_length=150, choices=[
                ("RACCO", "RACCO"), ("LGU", "LGU"), ("CCA", "CCA"), ("RCF", "RCF")])),
    ]
