from django.db import migrations, models

RENAMES = {
    "Counseling completed": "Sessions completed",
    "Favorable pre-assessment, no counseling needed": "Favorable pre-assessment, no sessions needed",
    "Counseling discontinued": "Sessions discontinued",
}


def _apply(mapping):
    def run(apps, schema_editor):
        Record = apps.get_model("children", "TerminationRecord")
        for old, new in mapping.items():
            Record.objects.filter(reason_category=old).update(reason_category=new)
    return run


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0032_retire_counseling_stage"),
    ]

    operations = [
        migrations.RunPython(_apply(RENAMES), _apply({v: k for k, v in RENAMES.items()})),
        migrations.AlterField(
            model_name="terminationrecord",
            name="reason_category",
            field=models.CharField(choices=[
                ("Reunified with family", "Reunified with family"),
                ("Adoption finalized", "Adoption finalized"),
                ("Transferred to another agency", "Transferred to another agency"),
                ("Aged out of program", "Aged out of program"),
                ("Services completed", "Services completed"),
                ("Other", "Other"),
                ("Sessions completed", "Sessions completed"),
                ("Favorable pre-assessment, no sessions needed",
                 "Favorable pre-assessment, no sessions needed"),
                ("Pre-assessment only, evaluation completed",
                 "Pre-assessment only, evaluation completed"),
                ("Sessions discontinued", "Sessions discontinued"),
                ("Referred to another specialist or service",
                 "Referred to another specialist or service"),
            ], max_length=50),
        ),
    ]
