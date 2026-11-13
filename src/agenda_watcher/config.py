"""Load config.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Client:
    slug: str
    name: str
    how_to_comment: str
    comment_source_url: str


@dataclass(frozen=True)
class Config:
    clients: list[Client]
    triage_model: str
    review_model: str
    backtest_start: date

    def client(self, slug: str) -> Client:
        for c in self.clients:
            if c.slug == slug:
                return c
        raise KeyError(slug)


def load(path: Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return Config(
        clients=[Client(**c) for c in raw["clients"]],
        triage_model=raw["triage_model"],
        review_model=raw["review_model"],
        backtest_start=raw["backtest_start"],
    )
