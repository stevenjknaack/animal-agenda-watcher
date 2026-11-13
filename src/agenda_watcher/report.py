"""The backtest report: what would have been flagged, and how far to trust it."""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import date

from . import evalset, store


def notice_days(conn, client: str, matter_id: int) -> int | None:
    """Days from a matter's introduction to the first non-cancelled meeting that lists it."""
    row = conn.execute(
        """select m.intro_date, min(e.date) as first_meeting
           from matters m join event_items i on i.client = m.client and i.matter_id = m.matter_id
           join events e on e.client = i.client and e.event_id = i.event_id
           where m.client = ? and m.matter_id = ? and m.intro_date is not null and e.date >= m.intro_date
             and coalesce(e.agenda_status, '') != 'Cancelled'""", (client, matter_id)).fetchone()
    if not row or not row["first_meeting"]:
        return None
    return (date.fromisoformat(row["first_meeting"]) - date.fromisoformat(row["intro_date"])).days


def _table(header: tuple[str, str], counts: list[tuple[str, int]]) -> list[str]:
    return [f"{header[0]} | {header[1]}", "--- | ---"] + [f"{k} | {v}" for k, v in counts] + [""]


def build(conn, config, today: date | None = None) -> str:
    today = today or date.today()
    names = {c.slug: c.name for c in config.clients}
    flagged = conn.execute(
        """select m.client, m.matter_id, coalesce(nullif(trim(m.title), ''), m.name, '') as title, m.body_name,
                  m.intro_date, f.category, f.summary
           from matters m join final f on f.client = m.client and f.matter_id = m.matter_id
           where f.relevant = 1 order by m.intro_date desc, m.client, m.matter_id""").fetchall()
    total = conn.execute("select count(*) from matters").fetchone()[0]
    unclassified = conn.execute(
        """select count(*) from matters m where not exists
           (select 1 from verdicts v where v.client = m.client and v.matter_id = m.matter_id and v.stage = 1)"""
    ).fetchone()[0]
    errors = conn.execute("select count(*) from verdicts where stage = 2 and relevant is null").fetchone()[0]
    pending = conn.execute(
        """select count(*) from verdicts s1 where s1.stage = 1 and s1.relevant = 1 and not exists
           (select 1 from verdicts s2 where s2.client = s1.client and s2.matter_id = s1.matter_id and s2.stage = 2)"""
    ).fetchone()[0]

    out = ["# Animal Agenda Watcher: backtest", "",
           f"Covers items introduced from {config.backtest_start.isoformat()} to {today.isoformat()} in "
           f"{' and '.join(names.values())}. {total} items were read.", ""]
    if unclassified or errors or pending:
        out += [f"> Classification is incomplete: {unclassified} items have not been classified, "
                f"{pending} are waiting for full review, and {errors} reviews failed and will be retried. "
                "The figures below undercount.", ""]

    out += ["## Headline", "", f"**{len(flagged)} items** materially affecting animals.", ""]
    by_year = Counter(((r["intro_date"] or "unknown")[:4], names.get(r["client"], r["client"])) for r in flagged)
    if by_year:
        out += _table(("Year, body", "Items"), [(f"{y}, {c}", n) for (y, c), n in sorted(by_year.items())])

    out += ["## By category", ""]
    out += _table(("Category", "Items"), Counter(r["category"] or "uncategorised" for r in flagged).most_common())

    out += ["## By committee or board (top ten)", ""]
    out += _table(("Body", "Items"), Counter(
        f"{r['body_name'] or 'unknown'} ({names.get(r['client'], r['client'])})" for r in flagged).most_common(10))

    out += ["## How much notice there was", ""]
    notices = {(r["client"], r["matter_id"]): notice_days(conn, r["client"], r["matter_id"]) for r in flagged}
    days = sorted(d for d in notices.values() if d is not None)
    if days:
        q = statistics.quantiles(days, n=4) if len(days) > 1 else [days[0]] * 3
        short = sum(1 for d in days if d < 7)
        out += [f"Of {len(flagged)} flagged items, {len(days)} appeared on a meeting agenda. From introduction to "
                f"that first meeting: median {statistics.median(days):g} days (quartiles {q[0]:g} and {q[2]:g}); "
                f"{short} of {len(days)} had fewer than 7 days.", "",
                "This is an approximation. Legistar does not record when an agenda was first posted, so the "
                "clock starts at the item's introduction date.", ""]
    else:
        out += ["No notice figures: no flagged item appeared on a meeting agenda.", ""]

    out += ["## How good is the classifier", ""]
    s = evalset.scores(conn)
    if not s.labelled:
        out += ["No items have been hand-checked yet, so accuracy is unknown. Run `aaw label` first.", ""]
    else:
        out += [f"{s.labelled} items were hand-checked."]
        if s.true_pos + s.false_pos:
            out += [f"- {s.true_pos} of {s.true_pos + s.false_pos} flagged items that were checked were right."]
        if s.true_pos + s.false_neg:
            out += [f"- {s.true_pos} of {s.true_pos + s.false_neg} checked items that do affect animals had been "
                    f"flagged; {s.false_neg} had been missed."]
        if s.random_sample_size:
            missed = round(s.miss_rate_in_random_sample * s.random_sample_size)
            out += [f"- In a random sample of {s.random_sample_size} unflagged items, {missed} turned out to be relevant."]
        out += [""]

    lines_total = conn.execute("select count(*) from event_items where matter_id is null").fetchone()[0]
    out += ["## What was not looked at", "",
            f"{lines_total} agenda lines have no legislative item attached and were not classified. Most are "
            f"procedural. {len(evalset.keyword_hits(conn)[1])} of them contain an animal-related keyword.", ""]

    out += ["## Flagged items", ""]
    for r in flagged:
        n = notices[(r["client"], r["matter_id"])]
        notice = "never on an agenda" if n is None else f"{n} days from introduction to first meeting"
        out += [f"### {r['title']}", "",
                f"{r['intro_date'] or 'undated'} | {names.get(r['client'], r['client'])}, {r['body_name'] or 'unknown'}"
                f" | {r['category'] or 'uncategorised'} | {notice}", ""]
        if r["summary"]:
            out += [r["summary"], ""]
        out += [store.matter_url(r["client"], r["matter_id"]), ""]
    return "\n".join(out)
