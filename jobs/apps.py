from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = "jobs"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Import signals so the post-save handlers are registered."""
        import jobs.signals  # noqa: F401
