"""Two-stage classification. Stage 1 triages titles in batches; stage 2 reviews survivors in full."""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import groupby

from . import store, sync
from .attachments import extract_text
from .judge import Judge, JudgeError, TriageItem
from .legistar import LegistarError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save(conn, client, matter_id, stage, relevant, category=None, confidence=None, reason=None,
          summary=None, model=None) -> None:
    conn.execute(
        "insert or replace into verdicts (client, matter_id, stage, relevant, category, confidence, reason,"
        " summary, model, created_at) values (?,?,?,?,?,?,?,?,?,?)",
        (client, matter_id, stage, relevant, category, confidence, reason, summary, model, _now()))


def run_triage(conn, judge: Judge, batch_size: int = 50, since: str | None = None,
               until: str | None = None) -> tuple[int, int]:
    """Judge every matter that has no stage 1 verdict. Returns (judged, survivors)."""
    rows = conn.execute(
        """select m.client, m.matter_id, coalesce(nullif(trim(m.title), ''), nullif(trim(m.name), '')) as label,
                  m.type, m.body_name
           from matters m left join verdicts v on v.client=m.client and v.matter_id=m.matter_id and v.stage=1
           where v.matter_id is null
             and (:since is null or m.intro_date >= :since) and (:until is null or m.intro_date <= :until)
           order by m.client, m.matter_id""", {"since": since, "until": until}).fetchall()
    rows = [r for r in rows if r["label"]]
    model = getattr(judge, "triage_model", None)
    judged = survivors = 0
    for client, group in groupby(rows, key=lambda r: r["client"]):
        group = list(group)
        for start in range(0, len(group), batch_size):
            batch = group[start:start + batch_size]
            items = [TriageItem(r["matter_id"], r["label"], r["type"] or "", r["body_name"] or "") for r in batch]
            try:
                keep, reason = judge.triage(items), None
            except JudgeError:
                keep, reason = {i.matter_id for i in items}, "triage failed"
            for i in items:
                _save(conn, client, i.matter_id, 1, int(i.matter_id in keep), reason=reason, model=model)
            conn.commit()
            judged += len(items)
            survivors += len(keep)
    return judged, survivors


ATTACHMENT_TEXT_CAP = 30_000


def _attachments(conn, lg, client: str, matter_id: int) -> list:
    q = "select name, url from attachments where client=? and matter_id=? order by attachment_id"
    rows = conn.execute(q, (client, matter_id)).fetchall()
    if rows:
        return rows
    try:
        found = lg.get(f"/matters/{matter_id}/attachments")
    except LegistarError as err:
        sync.log(f"  no attachment list for {client} matter {matter_id}: {err}")
        return []
    for a in found if isinstance(found, list) else []:
        store.upsert_attachment(conn, client, matter_id, a)
    conn.commit()
    return conn.execute(q, (client, matter_id)).fetchall()


def run_review(conn, legistar, judge: Judge, http, max_attachments: int = 3) -> tuple[int, int]:
    """Review every stage 1 survivor that has no finished stage 2 verdict. Returns (reviewed, flagged).

    `legistar` is one client object, or a function from client slug to client object.
    """
    rows = conn.execute(
        """select m.client, m.matter_id, coalesce(nullif(trim(m.title), ''), m.name, '') as label, m.type, m.body_name
           from verdicts s1 join matters m on m.client=s1.client and m.matter_id=s1.matter_id
           left join verdicts s2 on s2.client=s1.client and s2.matter_id=s1.matter_id and s2.stage=2
           where s1.stage=1 and s1.relevant=1 and (s2.matter_id is null or s2.relevant is null)
           order by m.client, m.matter_id""").fetchall()
    model = getattr(judge, "review_model", None)
    reviewed = flagged = 0
    for r in rows:
        client, matter_id = r["client"], r["matter_id"]
        lg = legistar(client) if callable(legistar) else legistar
        text = sync.fetch_text(conn, lg, client, matter_id)
        atts = _attachments(conn, lg, client, matter_id)
        pieces, room = [], ATTACHMENT_TEXT_CAP
        for a in atts[:max_attachments]:
            piece = extract_text(a["url"], http)[:room]
            if piece:
                pieces.append(piece)
                room -= len(piece) + 2
            if room <= 0:
                break
        args = (r["label"], r["type"] or "", r["body_name"] or "", text, [a["name"] or "" for a in atts],
                "\n\n".join(pieces))
        review, error = None, None
        for _ in range(2):
            try:
                review = judge.review(*args)
                break
            except JudgeError as e:
                error = e
        if review is None:
            _save(conn, client, matter_id, 2, None, reason=f"review failed: {error}", model=model)
        else:
            _save(conn, client, matter_id, 2, int(review.relevant), review.category, review.confidence,
                  review.reason, review.summary, model)
            flagged += int(review.relevant)
        conn.commit()
        reviewed += 1
    return reviewed, flagged
