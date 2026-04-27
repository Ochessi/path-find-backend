"""
Jooble API provider service.

Docs: https://jooble.org/api/about
Endpoint: POST https://jooble.org/api/{key}

Request body (JSON):
    {"keywords": "...", "location": "...", "page": 1, "resultonpage": 20}

Required env vars:
    JOOBLE_API_KEY
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

_BASE_URL = "https://jooble.org/api"
_RESULTS_PER_PAGE = 20


class JoobleService(BaseJobProvider):
    """Fetches and normalises job listings from the Jooble API."""

    source_label = JobSource.JOOBLE

    def __init__(self) -> None:
        self._api_key: str = getattr(settings, "JOOBLE_API_KEY", "") or ""

    def _is_configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self, query: str, location: str, page: int = 1) -> list[dict]:
        """
        POST to the Jooble API and return raw job dicts.

        Jooble paginates with a ``page`` integer in the request body.
        """
        url = f"{_BASE_URL}/{self._api_key}"
        payload = {
            "keywords": query,
            "location": location,
            "page": page,
            "resultonpage": _RESULTS_PER_PAGE,
        }

        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("jobs", [])

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw_item: dict) -> dict:
        """
        Map a single Jooble job dict to JobListing field values.

        Jooble result shape (key fields):
        {
            "title": "...",
            "location": "...",
            "snippet": "...",          <- description
            "salary": "£40,000",
            "source": "monster.com",   <- origin site, not our source field
            "type": "Full-time",
            "link": "https://...",
            "company": "...",
            "updated": "2024-01-15T10:00:00.0000000"
        }
        """
        title = raw_item.get("title", "").strip()
        company = raw_item.get("company", "").strip()
        location_str = raw_item.get("location", "").strip()
        description = raw_item.get("snippet", "").strip()
        source_url = raw_item.get("link", "").strip()
        salary_raw = raw_item.get("salary")
        emp_type_raw = raw_item.get("type", "")

        salary_min, salary_max = parse_salary(salary_raw)

        return {
            "title": title,
            "company": company,
            "location": location_str,
            "description": description,
            "source": JobSource.JOOBLE,
            "source_url": source_url,
            "employment_type": employment_type_from_string(emp_type_raw),
            "is_remote": is_remote(title, description, location_str),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "posted_at": parse_posted_at(raw_item.get("updated")),
        }
