"""Copy Legistar matters, meetings and agenda lines into the store."""
from __future__ import annotations

import sys
from datetime import date

from . import store
from .legistar import LegistarError, odata_date


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def sync_matters(conn, lg, client: str, since: date) -> int:
    key = f"matters_watermark:{client}"
    watermark = store.get_meta(conn, key)
    if watermark:
        flt = f"MatterLastModifiedUtc gt datetime'{watermark}'"
    else:
        flt = f"MatterIntroDate ge {odata_date(since)}"
    count, newest = 0, watermark or ""
    for m in lg.paged("/matters", filter=flt, orderby="MatterId"):
        store.upsert_matter(conn, client, m)
        count += 1
        newest = max(newest, m.get("MatterLastModifiedUtc") or "")
    if newest:
        store.set_meta(conn, key, newest)
    conn.commit()
    return count


def sync_events(conn, lg, client: str, start: date, end: date) -> int:
    flt = f"EventDate ge {odata_date(start)} and EventDate le {odata_date(end)}"
    events = list(lg.paged("/events", filter=flt, orderby="EventId"))
    for n, e in enumerate(events, 1):
        store.upsert_event(conn, client, e)
        if not e.get("EventAgendaFile"):
            continue  # no published agenda, so no public agenda lines yet
        try:
            items = lg.get(f"/events/{e['EventId']}/eventitems", {"Attachments": 1})
            if not isinstance(items, list):
                raise LegistarError(f"event {e['EventId']}: expected a list of agenda lines")
        except LegistarError as err:
            log(f"  skipped agenda lines for {client} event {e['EventId']}: {err}")
            continue
        for it in items:
            store.upsert_event_item(conn, client, e["EventId"], it)
            for a in it.get("EventItemMatterAttachments") or []:
                if it.get("EventItemMatterId"):
                    store.upsert_attachment(conn, client, it["EventItemMatterId"], a)
        if n % 200 == 0:
            conn.commit()
            log(f"  {client}: {n}/{len(events)} meetings")
    conn.commit()
    return len(events)


def fetch_text(conn, lg, client: str, matter_id: int) -> str:
    row = conn.execute("select text, text_fetched from matters where client=? and matter_id=?",
                       (client, matter_id)).fetchone()
    if row and row["text_fetched"]:
        return row["text"] or ""
    try:
        text = lg.matter_text(matter_id)
    except LegistarError as err:
        log(f"  no text for {client} matter {matter_id}: {err}")
        return ""
    store.set_matter_text(conn, client, matter_id, text)
    conn.commit()
    return text
