from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # Imported only so the console stamps "up since" as this process
        # starts. Left to the first request that reaches its URL, a worker that
        # had run for days would report the minute somebody opened the console.
        from accounts import admin_overview  # noqa: F401
