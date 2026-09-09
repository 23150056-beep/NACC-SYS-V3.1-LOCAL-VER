from django.apps import AppConfig


class AdoptionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "adoption"

    def ready(self):
        # The handoff trigger. Imported here so it is connected however the
        # process starts - runserver, gunicorn, a management command.
        from adoption import signals  # noqa: F401
