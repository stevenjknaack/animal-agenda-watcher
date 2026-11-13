"""Minimal client for the public Legistar Web API."""
from __future__ import annotations

import time
from datetime import date
from typing import Callable, Iterator

import httpx

BASE = "https://webapi.legistar.com/v1"
PAGE = 1000
BACKOFF = (1, 2, 4)


class LegistarError(Exception):
    pass


def odata_date(d: date) -> str:
    return f"datetime'{d.isoformat()}'"


class Legistar:
    def __init__(self, client: str, http: httpx.Client | None = None, pause: float = 0.2,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.http = http or httpx.Client(timeout=60)
        self.pause = pause
        self.sleep = sleep

    def get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{BASE}/{self.client}{path}"
        last = ""
        for attempt in range(len(BACKOFF) + 1):
            if attempt:
                self.sleep(BACKOFF[attempt - 1])
            elif self.pause:
                self.sleep(self.pause)
            try:
                resp = self.http.get(url, params=params)
            except httpx.TransportError as e:
                last = repr(e)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
                continue
            try:
                data = resp.json()
            except ValueError:
                raise LegistarError(f"{url}: HTTP {resp.status_code}, not JSON")
            if resp.status_code >= 400:
                msg = data.get("Message") if isinstance(data, dict) else ""
                raise LegistarError(f"{url}: HTTP {resp.status_code} {msg}")
            return data
        raise LegistarError(f"{url}: gave up after {len(BACKOFF)} retries ({last})")

    def paged(self, path: str, filter: str | None = None, orderby: str | None = None,
              params: dict | None = None) -> Iterator[dict]:
        skip = 0
        while True:
            q = dict(params or {})
            q["$top"] = PAGE
            q["$skip"] = skip
            if filter:
                q["$filter"] = filter
            if orderby:
                q["$orderby"] = orderby
            page = self.get(path, q)
            if not isinstance(page, list):
                msg = page.get("Message") if isinstance(page, dict) else page
                raise LegistarError(f"{BASE}/{self.client}{path}: expected a list, got: {msg}")
            yield from page
            if len(page) < PAGE:
                return
            skip += PAGE

    def matter_text(self, matter_id: int) -> str:
        versions = self.get(f"/matters/{matter_id}/versions")
        if not isinstance(versions, list) or not versions:
            return ""
        text = self.get(f"/matters/{matter_id}/texts/{versions[-1]['Key']}")
        if not isinstance(text, dict):
            return ""
        return text.get("MatterTextPlain") or ""
