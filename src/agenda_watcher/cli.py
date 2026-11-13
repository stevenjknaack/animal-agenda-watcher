"""Command line entry point: aaw <command>."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import httpx

from . import config as config_mod
from . import classify, store, sync
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
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    conn = store.connect(args.db)
    try:
        args.func(args, cfg, conn)
    finally:
        conn.close()
