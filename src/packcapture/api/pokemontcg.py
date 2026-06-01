"""Thin client for the pokemontcg.io v2 API.

Used only by `build-set`. Works without an API key (lower rate limits); set
POKEMONTCG_API_KEY to raise them.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests

from .. import config


class PokemonTCGClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 4,
    ):
        self.session = requests.Session()
        key = api_key or os.environ.get(config.API_KEY_ENV)
        if key:
            self.session.headers["X-Api-Key"] = key
        self.timeout = timeout
        self.max_retries = max_retries

    def _get(self, url: str, params: Optional[dict] = None) -> requests.Response:
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429:  # rate limited; back off and retry
                    time.sleep(min(2 ** attempt, 30))
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as err:
                last_err = err
                time.sleep(min(1.5 ** attempt, 15))
        raise RuntimeError(
            f"Request failed after {self.max_retries} attempts: {url} ({last_err})"
        )

    def get_set(self, code: str) -> Optional[dict[str, Any]]:
        """Set metadata (name, total, etc.). Returns None if unavailable."""
        try:
            resp = self._get(f"{config.API_BASE}/sets/{code}")
            return resp.json().get("data")
        except Exception:
            return None

    def get_cards(self, code: str, page_size: int = 250) -> list[dict[str, Any]]:
        """All cards in a set, following pagination."""
        cards: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = self._get(
                f"{config.API_BASE}/cards",
                params={
                    "q": f"set.id:{code}",
                    "page": page,
                    "pageSize": page_size,
                    "orderBy": "number",
                },
            )
            payload = resp.json()
            batch = payload.get("data", [])
            cards.extend(batch)
            total = payload.get("totalCount", 0)
            if not batch or page * page_size >= total:
                break
            page += 1
        return cards

    def download(self, url: str) -> bytes:
        return self._get(url).content
