"""SQLite schema and writes. Upserts take raw Legistar JSON objects."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists matters (
    client text not null, matter_id integer not null,
    file text, type text, status text, title text, name text, body_name text,
    intro_date text, last_modified text, text text, text_fetched integer not null default 0,
    primary key (client, matter_id));
create table if not exists events (
    client text not null, event_id integer not null,
    body_name text, date text, time text, location text, agenda_url text, site_url text, agenda_status text,
    primary key (client, event_id));
create table if not exists event_items (
    client text not null, event_item_id integer not null,
    event_id integer not null, matter_id integer, title text,
    primary key (client, event_item_id));
create index if not exists event_items_matter on event_items (client, matter_id);
create table if not exists attachments (
    client text not null, attachment_id integer not null,
    matter_id integer not null, name text, url text,
    primary key (client, attachment_id));
create table if not exists verdicts (
    client text not null, matter_id integer not null, stage integer not null,
    relevant integer, category text, confidence real, reason text, summary text, model text, created_at text,
    primary key (client, matter_id, stage));
create table if not exists labels (
    client text not null, matter_id integer not null,
    relevant integer not null, category text, source text, note text,
    primary key (client, matter_id));
create table if not exists digested (
    client text not null, matter_id integer not null, event_id integer not null, sent_at text,
    primary key (client, matter_id, event_id));
create table if not exists meta (key text primary key, value text);
-- One row per matter. `flagged` is the model's answer; `relevant` is the answer to use: a human label wins.
create view if not exists final as
select m.client, m.matter_id,
       case when s2.relevant = 1 then 1 else 0 end as flagged,
       case when l.matter_id is not null then l.relevant when s2.relevant = 1 then 1 else 0 end as relevant,
       case when l.matter_id is not null then (case when l.relevant = 1 then coalesce(l.category, s2.category) end)
            when s2.relevant = 1 then s2.category end as category,
       s2.summary, s2.reason, s2.confidence, l.source as label_source
from matters m
left join verdicts s2 on s2.client = m.client and s2.matter_id = m.matter_id and s2.stage = 2
left join labels l on l.client = m.client and l.matter_id = m.matter_id;
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _day(ts: str | None) -> str | None:
    return ts[:10] if ts else None


def upsert_matter(conn, client: str, m: dict) -> None:
    conn.execute(
        """insert into matters (client, matter_id, file, type, status, title, name, body_name, intro_date, last_modified)
           values (?,?,?,?,?,?,?,?,?,?)
           on conflict (client, matter_id) do update set
             file=excluded.file, type=excluded.type, status=excluded.status, title=excluded.title,
             name=excluded.name, body_name=excluded.body_name, intro_date=excluded.intro_date,
             last_modified=excluded.last_modified""",
        (client, m["MatterId"], m.get("MatterFile"), m.get("MatterTypeName"), m.get("MatterStatusName"),
         m.get("MatterTitle"), m.get("MatterName"), m.get("MatterBodyName"), _day(m.get("MatterIntroDate")),
         m.get("MatterLastModifiedUtc")),
    )


def set_matter_text(conn, client: str, matter_id: int, text: str) -> None:
    conn.execute("update matters set text=?, text_fetched=1 where client=? and matter_id=?", (text, client, matter_id))


def upsert_event(conn, client: str, e: dict) -> None:
    conn.execute(
        "insert or replace into events (client, event_id, body_name, date, time, location, agenda_url, site_url, agenda_status)"
        " values (?,?,?,?,?,?,?,?,?)",
        (client, e["EventId"], e.get("EventBodyName"), _day(e.get("EventDate")), e.get("EventTime"),
         e.get("EventLocation"), e.get("EventAgendaFile"), e.get("EventInSiteURL"), e.get("EventAgendaStatusName")),
    )


def upsert_event_item(conn, client: str, event_id: int, item: dict) -> None:
    conn.execute(
        "insert or replace into event_items (client, event_item_id, event_id, matter_id, title) values (?,?,?,?,?)",
        (client, item["EventItemId"], event_id, item.get("EventItemMatterId"), item.get("EventItemTitle")),
    )


def upsert_attachment(conn, client: str, matter_id: int, a: dict) -> None:
    conn.execute(
        "insert or replace into attachments (client, attachment_id, matter_id, name, url) values (?,?,?,?,?)",
        (client, a["MatterAttachmentId"], matter_id, a.get("MatterAttachmentName"), a.get("MatterAttachmentHyperlink")),
    )


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("select value from meta where key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("insert or replace into meta (key, value) values (?,?)", (key, value))


def matter_url(client: str, matter_id: int) -> str:
    return f"https://{client}.legistar.com/gateway.aspx?m=l&id=/matter.aspx?key={matter_id}"
