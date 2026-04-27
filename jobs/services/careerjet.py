"""
Careerjet API provider service.

Docs: https://www.careerjet.com/partners/api/
Endpoint: GET http://public.api.careerjet.net/search

Required env vars:
    CAREERJET_AFFID   — Your affiliate ID (assigned when you sign up)

Optional env vars:
    CAREERJET_LOCALE  — e.g. "en_GB", "en_US" (default: "en_GB")

Note: Careerjet's REST API is plain HTTP (not HTTPS). We pass user_ip and
user_agent as required by their terms of service; we use sensible defaults
when they are not available in the task context.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from jobs.models import JobSource
from .base import BaseJobProvider
from .normalizer import (
    employment_type_from_string,
    is_remote,
    parse_posted_at,
    parse_salary,
)

logger = logging.getLogger(__name__)

_BASE_URL = "http://public.api.careerjet.net/search"
_PAGESIZE = 20
_BOT_USER_AGENT = "PathfindBot/1.0 (+https://pathfind.app)"
_BOT_IP = "1.1.1.1"  # Careerjet requires a non-empty value; use Cloudflare's public DNS IP


class CareerjetService(BaseJobProvider):
    """Fetches and normalises job listings from the Careerjet REST API."""

    source_label = JobSource.CAREERJET

    def __init__(self) -> None:
        self._affid: str = getattr(settings, "CAREERJET_AFFID", "") or ""
        self._locale: str = getattr(settings, "CAREERJET_LOCALE", "en_GB")

    def _is_configured(self) -> bool:
        return bool(self._affid)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self, query: str, location: str, page: int = 1) -> list[dict]:
        """
        GET the Careerjet search endpoint and return raw job dicts.

        Careerjet returns a top-level ``jobs`` list and ``hits`` integer.
        Response docs: https://www.careerjet.com/partners/api/
        """
        params = {
            "affid": self._affid,
            "keywords": query,
            "location": location,
            "locale_code": self._locale,
            "pagesize": _PAGESIZE,
            "page": page,
            "user_ip": _BOT_IP,
            "user_agent": _BOT_USER_AGENT,
        }

        response = requests.get(_BASE_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        # Careerjet returns {"type": "JOBS", "hits": N, "jobs": [...]}
        return data.get("jobs", [])

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw_item: dict) -> dict:
        """
        Map a single Careerjet job dict to JobListing field values.

        Careerjet result shape (key fields):
        {
            "title": "...",
            "company": "...",
            "locations": "London, UK",
            "description": "...",
            "salary": "£30,000 - £45,000",
            "url": "https://...",
            "date": "30 minutes ago"   <- relative or absolute date string
        }
        """
        title = raw_item.get("title", "").strip()
        company = raw_item.get("company", "").strip()
        location_str = raw_item.get("locations", "").strip()
        description = raw_item.get("description", "").strip()
        source_url = raw_item.get("url", "").strip()
        salary_raw = raw_item.get("salary")

        salary_min, salary_max = parse_salary(salary_raw)

        # Careerjet does not expose employment_type directly; infer from title.
        emp_type = employment_type_from_string(title)

        return {
            "title": title,
            "company": company,
            "location": location_str,
            "description": description,
            "source": JobSource.CAREERJET,
            "source_url": source_url,
            "employment_type": emp_type,
            "is_remote": is_remote(title, description, location_str),
            "salary_min": salary_min,
            "salary_max": salary_max,
            # Careerjet dates are relative strings ("3 hours ago"); dateutil handles them.
            "posted_at": parse_posted_at(raw_item.get("date")),
        }
