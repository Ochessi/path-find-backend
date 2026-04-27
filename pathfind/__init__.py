# Expose the Celery application instance so that Django's manage.py picks it
# up automatically. This must be imported here (not just in celery.py) so
# that shared_task decorators resolve correctly when workers start.
from .celery import app as celery_app  # noqa: F401

__all__ = ["celery_app"]
