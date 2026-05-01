from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """
        Register cross-app signals once both apps are fully loaded.

        The Profile post_save signal lives in jobs/signals.py so all
        signal definitions stay co-located with the jobs domain. We import
        jobs.signals here (inside ready()) to avoid a circular import at
        module load time.
        """
        try:
            import jobs.signals  # noqa: F401
        except ImportError:
            # 'jobs' app not installed (e.g. test environments with minimal INSTALLED_APPS).
            pass
