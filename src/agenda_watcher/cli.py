"""Command line entry point: aaw <command>.

Commands that can partly fail (sync, classify, weekly) return a list of plain-language problems.
`main` prints them and exits 1, and `weekly` also puts them at the top of the email.
"""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import httpx

from . import classify, digest, evalset, report, store, sync
from . import config as config_mod
from .legistar import Legistar, LegistarError

UPCOMING_DAYS = 90


def cmd_sync(args, cfg, conn) -> list[str]:
    today = date.today()
    since = args.since or cfg.backtest_start
    problems = []
    for c in cfg.clients:
        first = store.get_meta(conn, f"since:{c.slug}")
        if first is None:
            store.set_meta(conn, f"since:{c.slug}", since.isoformat())
            conn.commit()
        elif first != since.isoformat():
            raise SystemExit(
                f"aaw: this database was first synced from {first}, so it cannot also be synced from "
                f"{since.isoformat()}. History is only fetched on the first sync. Use a new --db file, "
                f"or pass --since {first}.")
        lg = Legistar(c.slug)
        try:
            n = sync.sync_matters(conn, lg, c.slug, since)
            sync.log(f"{c.slug}: {n} matters")
            key = f"events_synced:{c.slug}"
            start = today - timedelta(days=14) if store.get_meta(conn, key) else since
            n = sync.sync_events(conn, lg, c.slug, start, today + timedelta(days=UPCOMING_DAYS))
            store.set_meta(conn, key, today.isoformat())
            conn.commit()
            sync.log(f"{c.slug}: {n} meetings")
        except LegistarError as err:
            conn.rollback()
            problems.append(f"Could not read new agendas for {c.name}: {err}")
    return problems


def make_judge(cfg):
    from .judge import ClaudeJudge
    return ClaudeJudge(cfg.triage_model, cfg.review_model)


def cmd_classify(args, cfg, conn) -> list[str]:
    try:
        judge = make_judge(cfg)
        if args.stage in (None, 1):
            judged, kept = classify.run_triage(conn, judge, since=args.since, until=args.until)
            sync.log(f"stage 1: judged {judged}, kept {kept} for review")
        if args.stage in (None, 2):
            clients = {}
            with httpx.Client(timeout=60) as http:
                reviewed, flagged = classify.run_review(
                    conn, lambda slug: clients.setdefault(slug, Legistar(slug)), judge, http)
            errors = conn.execute("select count(*) from verdicts where stage=2 and relevant is null").fetchone()[0]
            sync.log(f"stage 2: reviewed {reviewed}, flagged {flagged}, errors awaiting retry {errors}")
    except classify.Aborted as err:
        return [f"Classification did not finish: {err}"]
    except Exception as err:  # e.g. no API key configured: the client cannot even be built
        if type(err).__module__.startswith("anthropic"):
            return [f"Classification could not start: {err}"]
        raise
    return []


def cmd_label(args, cfg, conn) -> list[str]:
    if args.source == "flagged":
        rows = evalset.unlabelled_flagged(conn)
    elif args.source == "keyword":
        rows = evalset.unlabelled_keyword_misses(conn)
    else:
        rows = evalset.sample_unflagged(conn, args.n, seed=args.seed)
    print(f"{len(rows)} items to label ({args.source}).")
    print(f"Recorded {evalset.label_session(conn, rows, args.source)} labels.")
    return []


def cmd_scores(args, cfg, conn) -> list[str]:
    s = evalset.scores(conn)
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    print(f"hand-checked items: {s.labelled}")
    print(f"flagged and right: {s.true_pos}   flagged but wrong: {s.false_pos}   relevant but missed: {s.false_neg}")
    print(f"precision: {pct(s.precision)}   recall on hand-checked items: {pct(s.recall)}")
    print(f"random unflagged sample: {s.random_sample_size} checked, {pct(s.miss_rate_in_random_sample)} were relevant")
    return []


def cmd_report(args, cfg, conn) -> list[str]:
    md = report.build(conn, cfg)
    if args.out:
        args.out.write_text(md)
        print(f"wrote {args.out}")
    else:
        print(md)
    return []


def cmd_digest(args, cfg, conn, problems: list[str] | tuple = ()) -> list[str]:
    n = digest.run(conn, cfg, date.today(), os.environ, dry_run=args.dry_run, problems=problems)
    sync.log(f"digest: {n} items" + (" (dry run, nothing sent)" if args.dry_run else " sent"))
    return digest.health(conn)


def cmd_weekly(args, cfg, conn) -> list[str]:
    if args.require_existing and not conn.execute("select 1 from matters limit 1").fetchone():
        raise SystemExit(
            "aaw: the database is empty. A weekly run on an empty database would re-download three years of "
            "agendas and pay to classify all of it again. Restore the database, or run without "
            "--require-existing to rebuild on purpose.")
    missing = [k for k in digest.SETTINGS if not os.environ.get(k)]
    if missing:  # checked first, so no paid classification happens for an email that cannot be sent
        raise SystemExit(f"aaw: missing mail settings: {', '.join(missing)}")
    args.since = args.until = args.stage = None
    args.dry_run = False
    problems = cmd_sync(args, cfg, conn) + cmd_classify(args, cfg, conn)
    return problems + cmd_digest(args, cfg, conn, problems)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aaw", description="Animal Agenda Watcher")
    p.add_argument("--db", default="aaw.db", type=Path)
    p.add_argument("--config", default="config.toml", type=Path)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="copy Legistar data into the local database")
    s.add_argument("--since", type=date.fromisoformat,
                   help="earliest introduction date; fixed by the first sync of a database")
    s.set_defaults(func=cmd_sync)

    c = sub.add_parser("classify", help="flag items that affect animals (calls the Claude API)")
    c.add_argument("--stage", type=int, choices=(1, 2), help="run one stage only; default runs both")
    c.add_argument("--since", help="stage 1 only: earliest introduction date, YYYY-MM-DD")
    c.add_argument("--until", help="stage 1 only: latest introduction date, YYYY-MM-DD")
    c.set_defaults(func=cmd_classify)

    lab = sub.add_parser("label", help="hand-check items one at a time")
    lab.add_argument("--source", choices=("flagged", "keyword", "random"), required=True)
    lab.add_argument("--n", type=int, default=200, help="random source: sample size")
    lab.add_argument("--seed", type=int, default=1)
    lab.set_defaults(func=cmd_label)

    sub.add_parser("scores", help="print classifier accuracy on hand-checked items").set_defaults(func=cmd_scores)

    rep = sub.add_parser("report", help="write the backtest report")
    rep.add_argument("--out", type=Path)
    rep.set_defaults(func=cmd_report)

    dig = sub.add_parser("digest", help="email new flagged items")
    dig.add_argument("--dry-run", action="store_true", help="print the email instead of sending it")
    dig.set_defaults(func=cmd_digest)

    w = sub.add_parser("weekly", help="sync, classify and send the digest")
    w.add_argument("--require-existing", action="store_true",
                   help="refuse to run on an empty database (used by the scheduled job)")
    w.set_defaults(func=cmd_weekly)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    conn = store.connect(args.db)
    try:
        problems = args.func(args, cfg, conn)
    except digest.DigestError as e:
        raise SystemExit(f"aaw: {e}")
    finally:
        conn.close()
    if problems:
        for line in problems:
            sync.log(line)
        raise SystemExit(1)
