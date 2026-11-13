from pathlib import Path

import pytest

from agenda_watcher import cli, store
from agenda_watcher.legistar import LegistarError
from conftest import matter

CONFIG = Path(__file__).resolve().parents[1] / "config.toml"


class FakeLegistar:
    broken = set()

    def __init__(self, client):
        self.client = client

    def paged(self, path, filter=None, orderby=None, params=None):
        if self.client in FakeLegistar.broken:
            raise LegistarError(f"{self.client} is down")
        return iter([matter(1)] if path == "/matters" else [])

    def get(self, path, params=None):
        return []


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Legistar", FakeLegistar)
    FakeLegistar.broken = set()
    db = tmp_path / "t.db"

    def go(*argv):
        cli.main(["--db", str(db), "--config", str(CONFIG), *argv])
        return store.connect(db)

    return go


def test_sync_continues_with_other_clients_when_one_is_down(run, capsys):
    FakeLegistar.broken = {"madison"}
    with pytest.raises(SystemExit) as e:
        run("sync")
    assert e.value.code == 1
    assert "City of Madison" in capsys.readouterr().err


def test_sync_stores_data_for_the_client_that_worked(run, tmp_path):
    FakeLegistar.broken = {"madison"}
    with pytest.raises(SystemExit):
        run("sync")
    conn = store.connect(tmp_path / "t.db")
    assert [r[0] for r in conn.execute("select distinct client from matters")] == ["dane"]


def test_since_that_differs_from_the_first_sync_is_refused(run):
    run("sync", "--since", "2026-10-01")
    with pytest.raises(SystemExit, match="2026-10-01"):
        run("sync", "--since", "2023-11-01")


def test_plain_sync_after_a_short_first_sync_is_refused(run):
    run("sync", "--since", "2026-10-01")
    with pytest.raises(SystemExit, match="2026-10-01"):
        run("sync")


def test_repeat_sync_with_same_start_is_fine(run):
    run("sync")
    run("sync")


def test_weekly_refuses_to_rebuild_from_an_empty_database(run):
    with pytest.raises(SystemExit, match="empty"):
        run("weekly", "--require-existing")
