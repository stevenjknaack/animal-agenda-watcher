from datetime import date
from pathlib import Path

from agenda_watcher import config

ROOT = Path(__file__).resolve().parents[1]


def test_config_loads_two_clients_and_backtest_start():
    cfg = config.load(ROOT / "config.toml")
    assert [c.slug for c in cfg.clients] == ["madison", "dane"]
    assert cfg.backtest_start == date(2023, 11, 1)
    assert cfg.triage_model and cfg.review_model
    for c in cfg.clients:
        assert c.name and c.how_to_comment and c.comment_source_url.startswith("https://")


def test_client_lookup_by_slug():
    cfg = config.load(ROOT / "config.toml")
    assert cfg.client("dane").name == "Dane County"
