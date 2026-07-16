from django.apps import AppConfig


class AccountsConfig(AppConfig):
    # Preserve the primary-key type of Django's original auth_user table.
    default_auto_field = "django.db.models.AutoField"
    name = "accounts"

    def ready(self):
        from . import signals  # noqa: F401
