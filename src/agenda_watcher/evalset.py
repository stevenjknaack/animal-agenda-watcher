"""A small hand-checked set, so the report can say how good the classifier is."""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

from . import store

KEYWORDS = ["slaughter", "meat processing", "livestock", "feedlot", "animal feeding", "CAFO", "manure", "dairy",
            "poultry", "hog", "swine", "mink", "fur", "foie gras", "rodeo", "circus", "zoo", "Vilas", "deer",
            "geese", "goose", "coyote", "beaver", "trapping", "cull", "rodenticide", "pesticide", "pet store",
            "puppy", "kennel", "animal control", "humane society", "hunting", "fishing", "plant-based"]

_MATTER_COLS = """m.client, m.matter_id, coalesce(nullif(trim(m.title), ''), m.name, '') as title, m.type,
                  m.body_name, m.intro_date, f.flagged, f.category, f.summary, f.reason"""
_FROM = "from matters m join final f on f.client = m.client and f.matter_id = m.matter_id"


@dataclass(frozen=True)
class Scores:
    labelled: int
    true_pos: int
    false_pos: int
    false_neg: int
    precision: float | None
    recall: float | None
    miss_rate_in_random_sample: float | None
    random_sample_size: int


def _pattern(terms: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"\b(?:{alts})(?:s|es|ing|ed)?\b", re.IGNORECASE)


def keyword_hits(conn, terms: list[str] = KEYWORDS) -> tuple[list, list]:
    """(matters, agenda lines that have no matter) whose title contains one of the terms as a whole word."""
    pat = _pattern(terms)
    matters = [r for r in conn.execute(f"select {_MATTER_COLS} {_FROM} order by m.client, m.matter_id")
               if pat.search(r["title"])]
    lines = [r for r in conn.execute(
        """select i.client, i.event_item_id, i.event_id, i.title, e.body_name, e.date
           from event_items i join events e on e.client = i.client and e.event_id = i.event_id
           where i.matter_id is null and i.title is not null order by i.client, i.event_item_id""")
        if pat.search(r["title"])]
    return matters, lines


def unlabelled_flagged(conn) -> list:
    return conn.execute(f"select {_MATTER_COLS} {_FROM} where f.flagged = 1 and f.label_source is null"
                        " order by m.client, m.matter_id").fetchall()


def unlabelled_keyword_misses(conn, terms: list[str] = KEYWORDS) -> list:
    labelled = {(r["client"], r["matter_id"]) for r in conn.execute("select client, matter_id from labels")}
    return [r for r in keyword_hits(conn, terms)[0]
            if not r["flagged"] and (r["client"], r["matter_id"]) not in labelled]


def sample_unflagged(conn, n: int, seed: int) -> list:
    rows = conn.execute(f"select {_MATTER_COLS} {_FROM} where f.flagged = 0 and f.label_source is null"
                        " order by m.client, m.matter_id").fetchall()
    return rows if len(rows) <= n else random.Random(seed).sample(rows, n)


def record_label(conn, client: str, matter_id: int, relevant: bool, category: str | None, source: str,
                 note: str = "") -> None:
    conn.execute("insert or replace into labels (client, matter_id, relevant, category, source, note)"
                 " values (?,?,?,?,?,?)", (client, matter_id, int(relevant), category, source, note))
    conn.commit()


def scores(conn) -> Scores:
    rows = conn.execute("select flagged, relevant, label_source from final where label_source is not null").fetchall()
    tp = sum(1 for r in rows if r["relevant"] and r["flagged"])
    fp = sum(1 for r in rows if not r["relevant"] and r["flagged"])
    fn = sum(1 for r in rows if r["relevant"] and not r["flagged"])
    rand = [r for r in rows if r["label_source"] == "random"]
    return Scores(
        labelled=len(rows), true_pos=tp, false_pos=fp, false_neg=fn,
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        miss_rate_in_random_sample=sum(1 for r in rand if r["relevant"]) / len(rand) if rand else None,
        random_sample_size=len(rand))


def label_session(conn, rows: list, source: str, ask=input, out=print) -> int:
    """Show items one at a time. y = affects animals, n = does not, s = skip, q = stop. Returns labels recorded."""
    done = 0
    for n, r in enumerate(rows, 1):
        out(f"\n[{n}/{len(rows)}] {r['title']}\n    {r['body_name'] or ''} | {r['type'] or ''} | {r['intro_date'] or ''}"
            f"\n    {store.matter_url(r['client'], r['matter_id'])}")
        if r["summary"]:
            out(f"    model: {r['category'] or '-'}: {r['summary']}")
        answer = ""
        while answer not in ("y", "n", "s", "q"):
            answer = ask("    affects animals? [y/n/s=skip/q=quit] ").strip().lower()
        if answer == "q":
            break
        if answer == "s":
            continue
        record_label(conn, r["client"], r["matter_id"], answer == "y", r["category"] if answer == "y" else None, source)
        done += 1
    return done
