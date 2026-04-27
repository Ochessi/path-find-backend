"""
Adzuna API provider service.

Docs: https://developer.adzuna.com/activedocs#!/adzuna/search
Endpoint: GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}

Required env vars:
    ADZUNA_APP_ID
    ADZUNA_APP_KEY
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

_BASE_URL = "https://api.adzuna.com/v1/api/jobs"
_RESULTS_PER_PAGE = 50
_DEFAULT_COUNTRY = "gb"  # ISO 3166-1 alpha-2; override via ADZUNA_COUNTRY env var


class AdzunaService(BaseJobProvider):
    """Fetches and normalises job listings from the Adzuna API."""

    source_label = JobSource.ADZUNA

    def __init__(self) -> None:
        self._app_id: str = getattr(settings, "ADZUNA_APP_ID", "") or ""
        self._app_key: str = getattr(settings, "ADZUNA_APP_KEY", "") or ""
        self._country: str = getattr(settings, "ADZUNA_COUNTRY", _DEFAULT_COUNTRY)

    def _is_configured(self) -> bool:
        return bool(self._app_id and self._app_key)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self, query: str, location: str, page: int = 1) -> list[dict]:
        """
        Call the Adzuna search endpoint and return raw result dicts.

        https://api.adzuna.com/v1/api/jobs/gb/search/1?
            app_id=...&app_key=...&results_per_page=50&what=python&where=london
        """
        url = f"{_BASE_URL}/{self._country}/search/{page}"
        params = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "results_per_page": _RESULTS_PER_PAGE,
            "what": query,
            "where": location,
            "content-type": "application/json",
        }

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, raw_item: dict) -> dict:
        """
        Map a single Adzuna result dict to JobListing field values.

        Adzuna result shape (key fields):
        {
            "title": "...",
            "company": {"display_name": "..."},
            "location": {"display_name": "..."},
            "description": "...",
            "salary_min": 30000.0,
            "salary_max": 50000.0,
            "redirect_url": "https://...",
            "created": "2024-01-15T10:00:00Z",
            "contract_type": "permanent" | "contract" | null
        }
        """
        title = raw_item.get("title", "").strip()
        company = (raw_item.get("company") or {}).get("display_name", "").strip()
        location_str = (raw_item.get("location") or {}).get("display_name", "").strip()
        description = raw_item.get("description", "").strip()
        source_url = raw_item.get("redirect_url", "").strip()

        # Salary
        salary_min_raw = raw_item.get("salary_min")
        salary_max_raw = raw_item.get("salary_max")
        salary_min = int(salary_min_raw) if salary_min_raw else None
        salary_max = int(salary_max_raw) if salary_max_raw else None

        # Employment type
        contract_type = raw_item.get("contract_type") or raw_item.get("contract_time") or ""
        emp_type = employment_type_from_string(contract_type)

        return {
            "title": title,
            "company": company,
            "location": location_str,
            "description": description,
            "source": JobSource.ADZUNA,
            "source_url": source_url,
            "employment_type": emp_type,
            "is_remote": is_remote(title, description, location_str),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "posted_at": parse_posted_at(raw_item.get("created")),
        }
