from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from .arxiv_client import ArxivClient
from .config import ConfigError, load_config
from .digest import render_digest, write_digest
from .export import write_exports
from .schedule import build_plist, default_python, install, plist_path, uninstall
from .store import Store
from .timeutil import end_of_day_utc, parse_date, start_of_day_utc, utc_now

DB_NAME = "papers.sqlite3"
logger = logging.getLogger("paper_radar")


def _date_arg(value: str):
    try:
        return parse_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(prog="paper-radar", description="Incremental daily arXiv paper radar")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", parents=[common], help="create state/digest directories and the database")

    run = subparsers.add_parser("run", parents=[common], help="fetch, deduplicate, and write the digest")
    run.add_argument("--since", type=_date_arg, help="override window start (UTC date)")
    run.add_argument("--until", type=_date_arg, help="override window end (UTC date)")
    run.add_argument("--topic", action="append", help="only run this topic id (repeatable)")
    run.add_argument("--dry-run", action="store_true", help="print the digest instead of writing/notifying")
    run.add_argument("--no-digest", action="store_true", help="fetch and deduplicate only")
    run.add_argument("--verbose", action="store_true")

    subparsers.add_parser("stats", parents=[common], help="show database and run statistics")

    digest = subparsers.add_parser(
        "digest", parents=[common], help="re-render a digest from the database without crawling"
    )
    digest.add_argument("--since", type=_date_arg, help="window start (default: earliest paper)")
    digest.add_argument("--until", type=_date_arg, help="window end (default: now)")
    digest.add_argument("--dry-run", action="store_true", help="print instead of writing")

    export = subparsers.add_parser(
        "export", parents=[common], help="export a Markdown/CSV table from the database"
    )
    export.add_argument("--since", type=_date_arg, required=True)
    export.add_argument("--until", type=_date_arg)
    export.add_argument("--out-dir", default="survey")
    export.add_argument("--name", help="file stem (default: survey-<since>-<until>)")

    schedule = subparsers.add_parser("schedule", parents=[common], help="print or install the launchd daily job")
    schedule.add_argument("--hour", type=int, default=9)
    schedule.add_argument("--minute", type=int, default=30)
    group = schedule.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true", help="install and load the LaunchAgent")
    group.add_argument("--uninstall", action="store_true", help="unload and remove the LaunchAgent")
    group.add_argument("--print", dest="print_plist", action="store_true", help="print the plist only")

    return parser


def _setup_logging(state_dir: Path, verbose: bool) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        state_dir / "paper-radar.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)


def _window(topic, store: Store, config, args, until: datetime) -> tuple[datetime, datetime]:
    settings = config.settings
    if args.since is not None:
        since = start_of_day_utc(args.since)
    else:
        watermark = store.get_watermark(topic.id)
        if watermark is not None:
            since = watermark - timedelta(days=settings.overlap_days)
        else:
            since = until - timedelta(days=settings.initial_lookback_days)

    floor = until - timedelta(days=settings.max_lookback_days)
    since = max(since, floor)
    return since, until


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    settings = config.settings
    _setup_logging(settings.state_dir, args.verbose)

    now = utc_now()
    until = end_of_day_utc(args.until) if args.until else now
    if until > now:
        until = now
    if args.since is not None and start_of_day_utc(args.since) >= until:
        logger.error("--since must be earlier than --until")
        return 2

    if args.topic:
        try:
            topics = [config.topic_by_id(topic_id) for topic_id in args.topic]
        except ConfigError as exc:
            logger.error("%s", exc)
            return 2
    else:
        topics = list(config.topics)

    store = Store(settings.state_dir / DB_NAME, near_dup_days=settings.near_dup_days)
    run_id = store.start_run([topic.id for topic in topics])
    client = ArxivClient(settings)
    totals = Counter()
    errors: list[str] = []
    successes = 0
    window_starts: list[datetime] = []

    try:
        for topic in topics:
            since, topic_until = _window(topic, store, config, args, until)
            window_starts.append(since)
            logger.info(
                "topic=%s window=%s..%s",
                topic.id,
                since.strftime("%Y-%m-%dT%H:%MZ"),
                topic_until.strftime("%Y-%m-%dT%H:%MZ"),
            )
            try:
                result = client.fetch_topic(topic, since, topic_until)
            except Exception as exc:
                errors.append(f"{topic.id}: {exc}")
                store.record_topic_error(topic.id, str(exc))
                logger.exception("topic=%s failed", topic.id)
                continue

            successes += 1
            totals["fetched"] += len(result.papers)
            # 水位线记录窗口内最新论文时间；窗口内没有论文时推进到
            # topic_until，避免下次仍从旧水位线重复扫描。
            watermark = max(
                (paper.published_at for paper in result.papers), default=topic_until
            )
            store.record_topic_success(topic.id, watermark)

            topic_counts = Counter()
            for paper in result.papers:
                outcome = store.upsert(paper, topic.id, run_id)
                topic_counts[outcome.status] += 1
                totals[outcome.status] += 1
                logger.debug(
                    "%s %s %s",
                    outcome.status,
                    paper.canonical_id,
                    paper.title[:90],
                )
            logger.info(
                "topic=%s fetched=%d new=%d updated=%d duplicate=%d suppressed=%d",
                topic.id,
                len(result.papers),
                topic_counts["new"],
                topic_counts["updated"],
                topic_counts["duplicate"],
                topic_counts["suppressed"],
            )

        if successes == 0:
            status = "failed"
        elif errors:
            status = "partial"
        else:
            status = "ok"

        store.finish_run(
            run_id,
            status,
            fetched=totals["fetched"],
            inserted=totals["new"],
            updated=totals["updated"],
            duplicates=totals["duplicate"],
            suppressed=totals["suppressed"],
            error="; ".join(errors) if errors else None,
        )

        stats = {
            "fetched": totals["fetched"],
            "inserted": totals["new"],
            "updated": totals["updated"],
            "duplicates": totals["duplicate"],
            "suppressed": totals["suppressed"],
        }

        if not args.no_digest:
            pending = store.get_unnotified()
            window_start = min(window_starts) if window_starts else until - timedelta(days=settings.initial_lookback_days)
            text = render_digest(config, pending, window_start, until, stats)
            if args.dry_run:
                sys.stdout.write(text)
            else:
                path = write_digest(settings.digest_dir, text)
                store.mark_notified(stored.paper.canonical_id for stored in pending)
                logger.info("digest written: %s (%d papers)", path, len(pending))
        else:
            logger.info("digest skipped (--no-digest)")

        logger.info(
            "run=%d status=%s fetched=%d new=%d updated=%d duplicate=%d suppressed=%d",
            run_id,
            status,
            totals["fetched"],
            totals["new"],
            totals["updated"],
            totals["duplicate"],
            totals["suppressed"],
        )
        return 1 if status == "failed" else 0
    finally:
        store.close()


def cmd_init(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    config.settings.state_dir.mkdir(parents=True, exist_ok=True)
    config.settings.digest_dir.mkdir(parents=True, exist_ok=True)
    store = Store(config.settings.state_dir / DB_NAME)
    store.close()
    print(f"state:  {config.settings.state_dir}")
    print(f"digest: {config.settings.digest_dir}")
    print(f"db:     {config.settings.state_dir / DB_NAME}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = config.settings.state_dir / DB_NAME
    if not db.exists():
        print(f"database not found: {db} (run `paper-radar init` or `run` first)")
        return 1
    store = Store(db)
    try:
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    settings = config.settings
    _setup_logging(settings.state_dir, False)
    db = settings.state_dir / DB_NAME
    if not db.exists():
        print(f"database not found: {db} (run `paper-radar run` first)")
        return 1

    store = Store(db, near_dup_days=settings.near_dup_days)
    try:
        now = utc_now()
        until = end_of_day_utc(args.until) if args.until else now
        if until > now:
            until = now
        since = start_of_day_utc(args.since) if args.since else store.earliest_published()
        if since is None:
            print("database is empty")
            return 1
        papers = store.get_published_since(since)
        text = render_digest(
            config, papers, since, until, {"rebuild": 1, "window_total": len(papers)}
        )
        if args.dry_run:
            sys.stdout.write(text)
            return 0
        path = write_digest(settings.digest_dir, text)
        logger.info("digest written: %s (%d papers in window)", path, len(papers))
        return 0
    finally:
        store.close()


def cmd_export(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    settings = config.settings
    _setup_logging(settings.state_dir, False)
    db = settings.state_dir / DB_NAME
    if not db.exists():
        print(f"database not found: {db} (run `paper-radar run` first)")
        return 1

    store = Store(db, near_dup_days=settings.near_dup_days)
    try:
        now = utc_now()
        window_end = end_of_day_utc(args.until) if args.until else now
        if window_end > now:
            window_end = now
        window_start = start_of_day_utc(args.since)
        papers = store.get_published_since(window_start)
        name = args.name or (
            f"survey-{window_start:%Y%m%d}-{window_end:%Y%m%d}"
        )
        md_path, csv_path = write_exports(
            config, papers, window_start, window_end, args.out_dir, name
        )
        print(f"papers: {len(papers)}")
        print(f"markdown: {md_path}")
        print(f"csv:      {csv_path}")
        return 0
    finally:
        store.close()


def cmd_schedule(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not (0 <= args.hour <= 23 and 0 <= args.minute <= 59):
        print("hour must be 0-23 and minute must be 0-59", file=sys.stderr)
        return 2

    if args.uninstall:
        ok, message = uninstall()
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    working_dir = Path(__file__).resolve().parent.parent
    text = build_plist(
        python=default_python(),
        config_path=config.path,
        working_dir=working_dir,
        log_dir=config.settings.state_dir,
        hour=args.hour,
        minute=args.minute,
    )
    if args.install:
        ok, message = install(text)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    print(text, end="")
    print(f"\n# 安装：{sys.executable} -m paper_radar schedule --install", file=sys.stderr)
    print(f"# plist 目标：{plist_path()}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "stats":
            return cmd_stats(args)
        if args.command == "digest":
            return cmd_digest(args)
        if args.command == "export":
            return cmd_export(args)
        if args.command == "schedule":
            return cmd_schedule(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 0
