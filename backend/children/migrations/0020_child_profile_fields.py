"""The Add Record changes of 24 Sep 2026.

Middle initial becomes the whole middle name (renamed, not dropped and added:
the initials already recorded are kept), a street address and landmark join
the three PSGC levels, and a foundling's date found sits beside the birth date.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0019_drop_adoption_samd"),
    ]

    operations = [
        migrations.RenameField(
            model_name="child", old_name="middle_initial", new_name="middle_name"),
        migrations.AlterField(
            model_name="child", name="middle_name",
            field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(
            model_name="child", name="date_found",
            field=models.DateField(blank=True, null=True)),
        migrations.AddField(
            model_name="child", name="house_number",
            field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(
            model_name="child", name="street",
            field=models.CharField(blank=True, max_length=150)),
        migrations.AddField(
            model_name="child", name="landmark",
            field=models.CharField(blank=True, max_length=200)),
        migrations.AlterField(
            model_name="child", name="case_category",
            field=models.CharField(blank=True, max_length=50, choices=[
                ("Surrendered", "Surrendered"), ("Abandoned", "Abandoned"),
                ("Dependent", "Dependent"), ("Neglected", "Neglected"),
                ("Without Known Parents", "Without Known Parents"),
                ("Orphaned", "Orphaned")])),
        migrations.AlterField(
            model_name="child", name="birth_status",
            field=models.CharField(blank=True, max_length=20, choices=[
                ("Marital", "Marital"), ("Non-Marital", "Non-Marital"),
                ("Unknown", "Unknown")])),
        migrations.AlterField(
            model_name="child", name="type_of_adoption",
            field=models.CharField(blank=True, max_length=50, choices=[
                ("Regular", "Regular"), ("Domestic Relative", "Domestic Relative"),
                ("Relative (Without 2-yr custody)", "Relative (Without 2-yr custody)"),
                ("Step-parent", "Step-parent"), ("Adult", "Adult"), ("IP", "IP"),
                ("Foster-Adopt", "Foster-Adopt")])),
    ]
