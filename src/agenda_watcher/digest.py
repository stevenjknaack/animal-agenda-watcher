"""The weekly email: new flagged items, soonest meeting first, with how to comment."""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Mapping

from . import store

SETTINGS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "DIGEST_TO")
RECENT_DAYS = 14


class DigestError(Exception):
    pass


@dataclass(frozen=True)
class DigestEntry:
    client: str
    matter_id: int
    event_id: int  # 0 when the item has not been on any meeting agenda yet
    title: str
    category: str | None
    summary: str | None
    body_name: str | None
    meeting_date: date | None
    meeting_time: str | None
    location: str | None
    link: str
    how_to_comment: str
    comment_source_url: str
    note: str | None = None


_COLS = """m.client, m.matter_id, coalesce(nullif(trim(m.title), ''), m.name, '') as title, f.category, f.summary,
           m.body_name as matter_body"""


def pending(conn, config, today: date, horizon_days: int = 90) -> list[DigestEntry]:
    known = {c.slug: c for c in config.clients}
    meetings = conn.execute(
        f"""select distinct {_COLS}, e.event_id, e.body_name, e.date, e.time, e.location
            from final f join matters m on m.client = f.client and m.matter_id = f.matter_id
            join event_items i on i.client = m.client and i.matter_id = m.matter_id
            join events e on e.client = i.client and e.event_id = i.event_id
            where f.relevant = 1 and e.date between ? and ? and coalesce(e.agenda_status, '') != 'Cancelled'
              and not exists (select 1 from digested d where d.client = m.client and d.matter_id = m.matter_id
                              and d.event_id = e.event_id)""",
        (today.isoformat(), (today + timedelta(days=horizon_days)).isoformat())).fetchall()
    unscheduled = conn.execute(
        f"""select {_COLS}, 0 as event_id, m.body_name, null as date, null as time, null as location
            from final f join matters m on m.client = f.client and m.matter_id = f.matter_id
            where f.relevant = 1 and m.intro_date >= ?
              and not exists (select 1 from event_items i join events e on e.client = i.client and e.event_id = i.event_id
                              where i.client = m.client and i.matter_id = m.matter_id
                                and coalesce(e.agenda_status, '') != 'Cancelled')
              and not exists (select 1 from digested d where d.client = m.client and d.matter_id = m.matter_id
                              and d.event_id = 0)""",
        ((today - timedelta(days=RECENT_DAYS)).isoformat(),)).fetchall()
    # Introduced recently, and every meeting that listed it has already happened: the short-notice case.
    heard = conn.execute(
        f"""select {_COLS}, 0 as event_id, max(e.body_name) as body_name, null as date, null as time,
                   null as location, max(e.date) as heard_on
            from final f join matters m on m.client = f.client and m.matter_id = f.matter_id
            join event_items i on i.client = m.client and i.matter_id = m.matter_id
            join events e on e.client = i.client and e.event_id = i.event_id
            where f.relevant = 1 and m.intro_date >= ? and coalesce(e.agenda_status, '') != 'Cancelled'
              and not exists (select 1 from digested d where d.client = m.client and d.matter_id = m.matter_id)
            group by m.client, m.matter_id having max(e.date) < ?""",
        ((today - timedelta(days=RECENT_DAYS)).isoformat(), today.isoformat())).fetchall()
    entries = []
    for r in list(meetings) + list(unscheduled) + list(heard):
        if r["client"] not in known:
            continue
        c = known[r["client"]]
        entries.append(DigestEntry(
            client=r["client"], matter_id=r["matter_id"], event_id=r["event_id"], title=r["title"],
            category=r["category"], summary=r["summary"], body_name=r["body_name"],
            meeting_date=date.fromisoformat(r["date"]) if r["date"] else None, meeting_time=r["time"],
            location=" ".join(r["location"].split()) if r["location"] else None,
            link=store.matter_url(r["client"], r["matter_id"]),
            how_to_comment=c.how_to_comment, comment_source_url=c.comment_source_url,
            note=f"Already on a meeting agenda on {_long(date.fromisoformat(r['heard_on']))}, before this digest ran."
            if "heard_on" in r.keys() else None))
    return sorted(entries, key=lambda e: (e.meeting_date or date.max, e.client, e.matter_id))


def _long(d: date) -> str:
    return f"{d:%A} {d.day} {d:%B %Y}"


def health(conn) -> list[str]:
    """What the watcher could not check. Empty means every stored item has a finished verdict."""
    counts = dict(conn.execute("select state, count(*) from final group by state").fetchall())
    out = []
    n = counts.get("unclassified", 0)
    if n:
        out.append(f"{n} item{' has' if n == 1 else 's have'} not been classified.")
    n = counts.get("pending", 0)
    if n:
        out.append(f"{n} item{' is' if n == 1 else 's are'} waiting for review.")
    n = counts.get("error", 0)
    if n:
        out.append(f"{n} review{'' if n == 1 else 's'} failed and will be retried. Check these yourself:")
        out += [f"  {store.matter_url(r['client'], r['matter_id'])}" for r in conn.execute(
            "select client, matter_id from final where state = 'error' order by client, matter_id limit 10")]
    return out


def render(entries: list[DigestEntry], today: date, problems: list[str] | tuple = ()) -> tuple[str, str]:
    warning = []
    if problems:
        warning = ["NOT EVERYTHING WAS CHECKED THIS WEEK. Items affecting animals may be missing below.",
                   *problems, ""]
    if not entries:
        if problems:
            return "Animal agenda watch: could not check everything", "\n".join(warning)
        return ("Animal agenda watch: nothing new this week",
                f"The watcher ran on {today.isoformat()} and found no new items affecting animals.\n")
    n = len(entries)
    parts = warning + [f"{n} new item{'s' if n != 1 else ''} affecting animals, soonest meeting first.\n"]
    for e in sorted(entries, key=lambda e: (e.meeting_date or date.max, e.client, e.matter_id)):
        if e.meeting_date:
            when = _long(e.meeting_date) + (f", {e.meeting_time}" if e.meeting_time else "")
        else:
            when = e.note or "Not on a meeting agenda yet"
        block = ["-" * 60, e.title, ""]
        if e.summary:
            block += [e.summary, ""]
        block += [f"Body:     {e.body_name or 'unknown'}", f"When:     {when}"]
        if e.location:
            block += [f"Where:    {e.location}"]
        if e.category:
            block += [f"Category: {e.category}"]
        block += [f"Details:  {e.link}", "", f"How to comment: {e.how_to_comment}", f"Source: {e.comment_source_url}", ""]
        parts.append("\n".join(block))
    subject = f"Animal agenda watch: {n} item{'s' if n != 1 else ''}"
    return subject + (" (some checks failed)" if problems else ""), "\n".join(parts)


def send(subject: str, body: str, env: Mapping[str, str], smtp_class=None) -> None:
    missing = [k for k in SETTINGS if not env.get(k)]
    if missing:
        raise DigestError(f"missing mail settings: {', '.join(missing)}")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, env["SMTP_USER"], env["DIGEST_TO"]
    msg.set_content(body)
    port = int(env["SMTP_PORT"])
    implicit_tls = smtp_class is None and port == 465
    smtp_class = smtp_class or (smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP)
    try:
        with smtp_class(env["SMTP_HOST"], port) as smtp:
            if not implicit_tls:
                smtp.starttls()
            smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise DigestError(f"could not send the digest: {e}") from e


def mark_sent(conn, entries: list[DigestEntry], now: datetime) -> None:
    conn.executemany("insert or replace into digested (client, matter_id, event_id, sent_at) values (?,?,?,?)",
                     [(e.client, e.matter_id, e.event_id, now.isoformat(timespec="seconds")) for e in entries])
    conn.commit()


def run(conn, config, today: date, env: Mapping[str, str], dry_run: bool = False, out=print, smtp_class=None,
        problems: list[str] | tuple = ()) -> int:
    """Build and send the digest. Items are marked sent only after the mail server accepts the message.

    `problems` are failures from earlier steps (sync, classification). They and anything `health` finds are
    put at the top of the email, so a week where the watcher was blind never reads as a quiet week.
    """
    entries = pending(conn, config, today)
    subject, body = render(entries, today, list(problems) + health(conn))
    if dry_run:
        out(f"Subject: {subject}\n\n{body}")
        return len(entries)
    send(subject, body, env, smtp_class=smtp_class)
    mark_sent(conn, entries, datetime.now(timezone.utc))
    return len(entries)
