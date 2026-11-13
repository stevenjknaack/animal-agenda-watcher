from agenda_watcher import store
from conftest import event, item, matter


def test_upsert_matter_is_idempotent_and_updates(conn):
    m = matter(7, MatterTitle="A")
    store.upsert_matter(conn, "madison", m)
    store.upsert_matter(conn, "madison", {**m, "MatterTitle": "B"})
    rows = conn.execute("select title, intro_date, text_fetched from matters").fetchall()
    assert [tuple(r) for r in rows] == [("B", "2025-03-04", 0)]


def test_upsert_matter_keeps_fetched_text(conn):
    store.upsert_matter(conn, "madison", matter(7))
    store.set_matter_text(conn, "madison", 7, "full text")
    store.upsert_matter(conn, "madison", matter(7, MatterTitle="changed"))
    row = conn.execute("select title, text, text_fetched from matters").fetchone()
    assert tuple(row) == ("changed", "full text", 1)


def test_same_matter_id_in_two_clients_are_separate_rows(conn):
    store.upsert_matter(conn, "madison", matter(7))
    store.upsert_matter(conn, "dane", matter(7))
    assert conn.execute("select count(*) from matters").fetchone()[0] == 2


def test_event_item_without_matter_stores_null_matter_id(conn):
    store.upsert_event(conn, "dane", event(1))
    store.upsert_event_item(conn, "dane", 1, item(10, None, "Call to order"))
    row = conn.execute("select event_id, matter_id, title from event_items").fetchone()
    assert tuple(row) == (1, None, "Call to order")


def test_upsert_event_stores_date_and_status(conn):
    store.upsert_event(conn, "dane", event(1, date="2025-03-12", status="Cancelled"))
    row = conn.execute("select date, agenda_status, body_name from events").fetchone()
    assert tuple(row) == ("2025-03-12", "Cancelled", "PLAN COMMISSION")


def test_upsert_attachment(conn):
    a = {"MatterAttachmentId": 5, "MatterAttachmentName": "Staff report", "MatterAttachmentHyperlink": "https://x/a.pdf"}
    store.upsert_attachment(conn, "dane", 7, a)
    store.upsert_attachment(conn, "dane", 7, a)
    assert [tuple(r) for r in conn.execute("select matter_id, name, url from attachments")] == [(7, "Staff report", "https://x/a.pdf")]


def test_meta_roundtrip(conn):
    assert store.get_meta(conn, "k") is None
    store.set_meta(conn, "k", "1")
    store.set_meta(conn, "k", "2")
    assert store.get_meta(conn, "k") == "2"
