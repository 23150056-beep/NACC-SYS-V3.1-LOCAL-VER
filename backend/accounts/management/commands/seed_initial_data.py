from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.models import Role

User = get_user_model()


class Command(BaseCommand):
    help = "Seed default roles and an initial administrator account."

    def handle(self, *args, **options):
        for name in Role.NAMES:
            Role.objects.get_or_create(role_name=name)
        self.stdout.write(self.style.SUCCESS("Roles seeded."))

        admin_role = Role.objects.get(role_name=Role.ADMINISTRATOR)
        if not User.objects.filter(email="admin@racco1.gov.ph").exists():
            User.objects.create_superuser(
                email="admin@racco1.gov.ph",
                username="admin",
                password="admin1234",
                first_name="System",
                last_name="Administrator",
                role=admin_role,
            )
            self.stdout.write(self.style.SUCCESS(
                "Default admin created: admin@racco1.gov.ph / admin1234"))
        else:
            self.stdout.write("Admin already exists; skipping.")
