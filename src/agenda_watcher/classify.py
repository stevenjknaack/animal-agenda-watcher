"""Two-stage classification. Stage 1 triages titles in batches; stage 2 reviews survivors in full."""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import groupby

from .judge import Judge, JudgeError, TriageItem


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
