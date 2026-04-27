"""
Celery application entry-point for the Pathfind project.

Usage:
    # Start a worker (from the project root with venv active):
    celery -A pathfind worker -l info

    # Start the beat scheduler for periodic tasks:
    celery -A pathfind beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
"""

import os
from celery import Celery

# Tell Celery which Django settings module to use so it can read
# CELERY_BROKER_URL, CELERY_BEAT_SCHEDULE, etc.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pathfind.settings")

app = Celery("pathfind")

# Load all Celery config keys that are prefixed with CELERY_ from Django settings.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover @shared_task functions in tasks.py within each installed app.
app.autodiscover_tasks()
