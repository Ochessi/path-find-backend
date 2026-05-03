import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pathfind.settings")
django.setup()

from jobs.services.adzuna import AdzunaService
from jobs.services.jooble import JoobleService
from jobs.services.careerjet import CareerjetService

def test_sources():
    print("Testing Adzuna...")
    try:
        adzuna = AdzunaService()
        raw_jobs = adzuna.fetch("software engineer", "", page=1)
        print(f"Adzuna returned {len(raw_jobs)} raw jobs for empty location.")
        if raw_jobs:
            norm = adzuna.normalize(raw_jobs[0])
            print(f"  First job title: {norm.get('title')}")
            print(f"  First job company: {norm.get('company')}")
    except Exception as e:
        print(f"Adzuna Error: {e}")

if __name__ == "__main__":
    test_sources()
