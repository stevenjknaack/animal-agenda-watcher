"""Command line entry point: aaw <command>."""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import httpx

from . import config as config_mod
from . import classify, digest, evalset, report, store, sync
from .legistar import Legistar

UPCOMING_DAYS = 90


def cmd_sync(args, cfg, conn) -> None:
    today = date.today()
    since = args.since or cfg.backtest_start
    for c in cfg.clients:
        lg = Legistar(c.slug)
        n = sync.sync_matters(conn, lg, c.slug, since)
        sync.log(f"{c.slug}: {n} matters")
        key = f"events_synced:{c.slug}"
        start = today - timedelta(days=14) if store.get_meta(conn, key) else since
        n = sync.sync_events(conn, lg, c.slug, start, today + timedelta(days=UPCOMING_DAYS))
        store.set_meta(conn, key, today.isoformat())
        conn.commit()
        sync.log(f"{c.slug}: {n} meetings")


def make_judge(cfg):
    from .judge import ClaudeJudge
    return ClaudeJudge(cfg.triage_model, cfg.review_model)


def cmd_classify(args, cfg, conn) -> None:
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


def cmd_label(args, cfg, conn) -> None:
    if args.source == "flagged":
        rows = evalset.unlabelled_flagged(conn)
    elif args.source == "keyword":
        rows = evalset.unlabelled_keyword_misses(conn)
    else:
        rows = evalset.sample_unflagged(conn, args.n, seed=args.seed)
    print(f"{len(rows)} items to label ({args.source}).")
    print(f"Recorded {evalset.label_session(conn, rows, args.source)} labels.")


def cmd_scores(args, cfg, conn) -> None:
    s = evalset.scores(conn)
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    print(f"hand-checked items: {s.labelled}")
    print(f"flagged and right: {s.true_pos}   flagged but wrong: {s.false_pos}   relevant but missed: {s.false_neg}")
    print(f"precision: {pct(s.precision)}   recall on hand-checked items: {pct(s.recall)}")
    print(f"random unflagged sample: {s.random_sample_size} checked, {pct(s.miss_rate_in_random_sample)} were relevant")


def cmd_report(args, cfg, conn) -> None:
    md = report.build(conn, cfg)
    if args.out:
        args.out.write_text(md)
        print(f"wrote {args.out}")
    else:
        print(md)


def cmd_digest(args, cfg, conn) -> None:
    n = digest.run(conn, cfg, date.today(), os.environ, dry_run=args.dry_run)
    sync.log(f"digest: {n} items" + (" (dry run, nothing sent)" if args.dry_run else " sent"))


def cmd_weekly(args, cfg, conn) -> None:
    args.since = args.until = args.stage = None
    args.dry_run = False
    cmd_sync(args, cfg, conn)
    cmd_classify(args, cfg, conn)
    cmd_digest(args, cfg, conn)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aaw", description="Animal Agenda Watcher")
    p.add_argument("--db", default="aaw.db", type=Path)
    p.add_argument("--config", default="config.toml", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="copy Legistar data into the local database")
    s.add_argument("--since", type=date.fromisoformat, help="first run only: earliest introduction date")
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
    sub.add_parser("weekly", help="sync, classify and send the digest").set_defaults(func=cmd_weekly)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    conn = store.connect(args.db)
    try:
        args.func(args, cfg, conn)
    except digest.DigestError as e:
        raise SystemExit(f"aaw: {e}")
    finally:
        conn.close()
