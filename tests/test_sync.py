from datetime import date

from agenda_watcher import store, sync
from agenda_watcher.legistar import LegistarError
from conftest import event, item, matter


class FakeLegistar:
    def __init__(self, matters=(), events=(), items=None, fail_items_for=(), text="body text"):
        self.matters, self.events, self.items = list(matters), list(events), items or {}
        self.fail_items_for = set(fail_items_for)
        self.text = text
        self.paged_calls, self.text_calls = [], 0

    def paged(self, path, filter=None, orderby=None, params=None):
        self.paged_calls.append((path, filter))
        return iter(self.matters if path == "/matters" else self.events)

    def get(self, path, params=None):
        event_id = int(path.split("/")[2])
        if event_id in self.fail_items_for:
            raise LegistarError("boom")
        return self.items.get(event_id, [])

    def matter_text(self, matter_id):
        self.text_calls += 1
        return self.text


def test_first_sync_filters_by_intro_date_and_sets_watermark(conn):
    lg = FakeLegistar(matters=[matter(1, MatterLastModifiedUtc="2025-03-05T10:00:00.1"),
                               matter(2, MatterLastModifiedUtc="2025-04-01T09:00:00")])
    assert sync.sync_matters(conn, lg, "madison", date(2023, 11, 1)) == 2
    assert lg.paged_calls == [("/matters", "MatterIntroDate ge datetime'2023-11-01'")]
    assert store.get_meta(conn, "matters_watermark:madison") == "2025-04-01T09:00:00"


def test_second_sync_filters_by_last_modified_watermark(conn):
    lg = FakeLegistar(matters=[matter(1, MatterLastModifiedUtc="2025-04-01T09:00:00")])
    sync.sync_matters(conn, lg, "madison", date(2023, 11, 1))
    sync.sync_matters(conn, lg, "madison", date(2023, 11, 1))
    assert lg.paged_calls[1] == ("/matters", "MatterLastModifiedUtc gt datetime'2025-04-01T09:00:00'")


def test_sync_with_no_new_matters_keeps_watermark(conn):
    lg = FakeLegistar(matters=[matter(1, MatterLastModifiedUtc="2025-04-01T09:00:00")])
    sync.sync_matters(conn, lg, "madison", date(2023, 11, 1))
    lg.matters = []
    assert sync.sync_matters(conn, lg, "madison", date(2023, 11, 1)) == 0
    assert store.get_meta(conn, "matters_watermark:madison") == "2025-04-01T09:00:00"


def test_watermarks_are_per_client(conn):
    lg = FakeLegistar(matters=[matter(1)])
    sync.sync_matters(conn, lg, "madison", date(2023, 11, 1))
    sync.sync_matters(conn, lg, "dane", date(2023, 11, 1))
    assert lg.paged_calls[1][1].startswith("MatterIntroDate ge")


def test_sync_events_stores_items_and_their_attachments(conn):
    att = {"MatterAttachmentId": 5, "MatterAttachmentName": "Staff report", "MatterAttachmentHyperlink": "https://x/a.pdf"}
    lg = FakeLegistar(events=[event(1)], items={1: [item(10, None, "Call to order"),
                                                    {**item(11, 77, "Rezoning"), "EventItemMatterAttachments": [att]}]})
    assert sync.sync_events(conn, lg, "dane", date(2025, 1, 1), date(2025, 12, 31)) == 1
    assert lg.paged_calls == [("/events", "EventDate ge datetime'2025-01-01' and EventDate le datetime'2025-12-31'")]
    assert conn.execute("select count(*) from event_items").fetchone()[0] == 2
    assert [tuple(r) for r in conn.execute("select matter_id, name from attachments")] == [(77, "Staff report")]


def test_event_items_failure_for_one_event_does_not_stop_sync(conn):
    lg = FakeLegistar(events=[event(1), event(2), event(3)],
                      items={1: [item(10)], 2: [item(20)], 3: [item(30)]}, fail_items_for=[2])
    assert sync.sync_events(conn, lg, "dane", date(2025, 1, 1), date(2025, 12, 31)) == 3
    assert [r[0] for r in conn.execute("select event_id from event_items order by 1")] == [1, 3]


def test_event_without_published_agenda_is_stored_without_fetching_items(conn):
    lg = FakeLegistar(events=[event(1, EventAgendaFile=None)], fail_items_for=[1])
    assert sync.sync_events(conn, lg, "dane", date(2025, 1, 1), date(2025, 12, 31)) == 1
    assert conn.execute("select count(*) from events").fetchone()[0] == 1


def test_matter_with_null_title_is_stored(conn):
    lg = FakeLegistar(matters=[matter(1, MatterTitle=None)])
    sync.sync_matters(conn, lg, "madison", date(2023, 11, 1))
    assert conn.execute("select title from matters").fetchone()[0] is None


def test_fetch_text_calls_api_once(conn):
    store.upsert_matter(conn, "madison", matter(1))
    lg = FakeLegistar()
    assert sync.fetch_text(conn, lg, "madison", 1) == "body text"
    assert sync.fetch_text(conn, lg, "madison", 1) == "body text"
    assert lg.text_calls == 1


def test_fetch_text_failure_returns_empty_and_is_retried_later(conn):
    store.upsert_matter(conn, "madison", matter(1))

    class Broken(FakeLegistar):
        def matter_text(self, matter_id):
            self.text_calls += 1
            raise LegistarError("boom")

    lg = Broken()
    assert sync.fetch_text(conn, lg, "madison", 1) == ""
    assert conn.execute("select text_fetched from matters").fetchone()[0] == 0
