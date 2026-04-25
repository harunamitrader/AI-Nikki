from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .config import load_config
from .db import (
    begin_run,
    connect_db,
    finish_run,
    get_file_state,
    update_file_state,
    upsert_actions,
    upsert_messages,
    upsert_sessions,
    upsert_source,
)
from .personas import write_persona_config
from .importers import EXTRACTOR_VERSION, SOURCE_DEFINITIONS, discover_files, file_fingerprint, parse_file
from .reports import export_day_jsonl, generate_diary, write_schedule_file
from .soul_analysis import build_soul_analysis_package
from .util import ensure_directory, now_utc_iso, stable_id


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_nikki")
    parser.add_argument("--config", help="Path to config JSON", default=None)
    subparsers = parser.add_subparsers(dest="command", required=False)

    ingest = subparsers.add_parser("ingest", help="Ingest logs into the unified SQLite database only")
    ingest.add_argument("--day", help="Optional day to force into the result summary", default=None)

    generate_diaries = subparsers.add_parser(
        "generate-diaries",
        help="Generate daily JSONL files and AI-Nikki diary files from the existing unified database",
    )
    generate_diaries.add_argument("--day", help="Generate one specific day only", default=None)
    generate_diaries.add_argument("--from-day", help="Lower bound day key (YYYY-MM-DD)", default=None)
    generate_diaries.add_argument("--to-day", help="Upper bound day key (YYYY-MM-DD)", default=None)
    generate_diaries.add_argument(
        "--missing-only",
        action="store_true",
        help="Only generate days whose JSONL or AI-Nikki output does not exist yet",
    )

    personas = subparsers.add_parser(
        "prepare-personas",
        help="Create or refresh an editable Japanese AI-Nikki persona Markdown file",
    )
    personas.add_argument("--subject-name", default=None, help="Display name used inside the diary setting")
    personas.add_argument("--overwrite", action="store_true", help="Overwrite existing local persona file")

    sync = subparsers.add_parser("sync", help="Ingest logs, export JSONL, generate diary files, and refresh schedules")
    sync.add_argument("--day", help="Only regenerate a specific day after sync", default=None)

    export_day = subparsers.add_parser("export-day", help="Regenerate one day from the existing database")
    export_day.add_argument("--day", required=True, help="Day key in YYYY-MM-DD")

    subparsers.add_parser("write-schedules", help="Write AI-Nikki schedule JSON files")
    build_soul = subparsers.add_parser(
        "build-soul-analysis",
        help="Create a reusable Soul Analysis workflow package with local packets, web intake guides, and complete-analysis prompts",
    )
    build_soul.add_argument("--subject-name", default="Unknown Subject", help="Display name for the analysis subject")
    build_soul.add_argument("--label", default="latest", help="Output label folder under the soul-analysis directory")
    build_soul.add_argument("--from-day", default=None, help="Optional day lower bound (YYYY-MM-DD)")
    build_soul.add_argument("--to-day", default=None, help="Optional day upper bound (YYYY-MM-DD)")
    return parser


def _run_ingest(config: dict[str, Any], explicit_day: str | None = None) -> dict[str, Any]:
    connection = connect_db(config["paths"]["db"])
    shared_state: dict[str, Any] = {}
    run_id = stable_id("run", now_utc_iso())
    begin_run(connection, run_id, "ingest")
    stats = {
        "discovered_files": 0,
        "processed_files": 0,
        "skipped_files": 0,
        "sessions_upserted": 0,
        "messages_upserted": 0,
        "actions_upserted": 0,
    }
    touched_days: set[str] = set()
    try:
        for source_id, definition in SOURCE_DEFINITIONS.items():
            source_config = config.get("sources", {}).get(source_id, {})
            patterns = source_config.get("patterns", [])
            if not patterns:
                continue
            files = discover_files(patterns)
            if not files:
                continue
            upsert_source(
                connection,
                source_id=source_id,
                display_name=definition.display_name,
                source_type=definition.source_type,
                root_path=";".join(patterns),
                extractor_version=EXTRACTOR_VERSION,
            )
            for file_path in files:
                stats["discovered_files"] += 1
                fingerprint = file_fingerprint(file_path)
                file_state = get_file_state(connection, source_id, str(file_path))
                force_reparse = source_id == "codex_desktop_bridge" and shared_state.get("force_codex_bridge")
                if file_state and file_state["fingerprint"] == fingerprint and not force_reparse:
                    stats["skipped_files"] += 1
                    continue
                parsed = parse_file(source_id, file_path, config, shared_state)
                upsert_sessions(connection, parsed["sessions"])
                upsert_messages(connection, parsed["messages"])
                upsert_actions(connection, parsed["actions"])
                update_file_state(
                    connection,
                    source_id=source_id,
                    path=str(file_path),
                    size=file_path.stat().st_size,
                    mtime_ns=file_path.stat().st_mtime_ns,
                    fingerprint=fingerprint,
                    run_id=run_id,
                    status="ok",
                )
                stats["processed_files"] += 1
                stats["sessions_upserted"] += len(parsed["sessions"])
                stats["messages_upserted"] += len(parsed["messages"])
                stats["actions_upserted"] += len(parsed["actions"])
                if source_id == "codex_desktop_live_log":
                    shared_state["force_codex_bridge"] = True
                elif source_id == "codex_desktop_bridge":
                    shared_state["force_codex_bridge"] = False
                for record in parsed["messages"]:
                    if record.get("day_key"):
                        touched_days.add(record["day_key"])
                for record in parsed["actions"]:
                    if record.get("day_key"):
                        touched_days.add(record["day_key"])
        if explicit_day:
            touched_days = {explicit_day}
        finish_run(connection, run_id, status="completed", message="ingest completed", stats=stats)
        connection.commit()
        return {
            "run_id": run_id,
            "stats": stats,
            "touched_days": sorted(touched_days),
            "db_path": config["paths"]["db"],
        }
    except Exception as exc:
        finish_run(connection, run_id, status="failed", message=str(exc), stats=stats)
        connection.commit()
        raise
    finally:
        connection.close()


def _available_days(connection: Any) -> list[str]:
    rows = connection.execute(
        """
        SELECT day_key
        FROM (
          SELECT day_key FROM messages WHERE day_key IS NOT NULL
          UNION
          SELECT day_key FROM actions WHERE day_key IS NOT NULL
        )
        ORDER BY day_key
        """
    ).fetchall()
    return [row[0] for row in rows]


def _select_days(
    available_days: list[str],
    *,
    day: str | None,
    from_day: str | None,
    to_day: str | None,
) -> list[str]:
    if day:
        return [day]
    selected = available_days
    if from_day:
        selected = [value for value in selected if value >= from_day]
    if to_day:
        selected = [value for value in selected if value <= to_day]
    return selected


def _run_generate_diaries(
    config: dict[str, Any],
    *,
    day: str | None,
    from_day: str | None,
    to_day: str | None,
    missing_only: bool,
) -> dict[str, Any]:
    connection = connect_db(config["paths"]["db"])
    try:
        daily_dir = Path(config["paths"]["daily_dir"])
        report_dir = Path(config["paths"]["report_dir"])
        ensure_directory(daily_dir)
        ensure_directory(report_dir)
        available_days = _available_days(connection)
        selected_days = _select_days(available_days, day=day, from_day=from_day, to_day=to_day)
        generated_days: list[str] = []
        skipped_days: list[str] = []
        for day_key in selected_days:
            daily_path = daily_dir / f"{day_key}.jsonl"
            report_path = report_dir / f"{day_key}-ai-nikki.md"
            prompt_path = report_dir / f"{day_key}-ai-nikki-prompt.txt"
            posts_path = report_dir / f"{day_key}-ai-nikki-posts.json"
            if missing_only and daily_path.exists() and report_path.exists() and prompt_path.exists() and posts_path.exists():
                skipped_days.append(day_key)
                continue
            export_day_jsonl(connection, day_key, daily_path)
            generate_diary(connection, config, day_key, report_path, prompt_path, posts_path)
            generated_days.append(day_key)
        return {
            "generated_days": generated_days,
            "skipped_days": skipped_days,
            "selected_days": selected_days,
        }
    finally:
        connection.close()


def _run_sync(config: dict[str, Any], explicit_day: str | None = None) -> dict[str, Any]:
    ingest_result = _run_ingest(config, explicit_day=explicit_day)
    diary_result = _run_generate_diaries(
        config,
        day=explicit_day,
        from_day=None,
        to_day=None,
        missing_only=False,
    )
    schedule_path = write_schedule_file(config)
    return {
        **ingest_result,
        "generated_days": diary_result["generated_days"],
        "schedule_path": str(schedule_path),
    }


def _run_export_day(config: dict[str, Any], day_key: str) -> dict[str, Any]:
    connection = connect_db(config["paths"]["db"])
    try:
        count = export_day_jsonl(connection, day_key, Path(config["paths"]["daily_dir"]) / f"{day_key}.jsonl")
        generate_diary(
            connection,
            config,
            day_key,
            Path(config["paths"]["report_dir"]) / f"{day_key}-ai-nikki.md",
            Path(config["paths"]["report_dir"]) / f"{day_key}-ai-nikki-prompt.txt",
            Path(config["paths"]["report_dir"]) / f"{day_key}-ai-nikki-posts.json",
        )
        return {"day_key": day_key, "records": count}
    finally:
        connection.close()


def _run_prepare_personas(
    config: dict[str, Any],
    *,
    subject_name: str | None,
    overwrite: bool,
) -> dict[str, Any]:
    connection = connect_db(config["paths"]["db"])
    try:
        return write_persona_config(connection, config, subject_name=subject_name, overwrite=overwrite)
    finally:
        connection.close()


def _run_build_soul_analysis(
    config: dict[str, Any],
    *,
    subject_name: str,
    label: str,
    from_day: str | None,
    to_day: str | None,
) -> dict[str, Any]:
    connection = connect_db(config["paths"]["db"])
    try:
        return build_soul_analysis_package(
            connection,
            config,
            subject_name=subject_name,
            label=label,
            from_day=from_day,
            to_day=to_day,
        )
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    command = args.command or "sync"
    if command == "ingest":
        result = _run_ingest(config, explicit_day=args.day)
        print(f"DB: {result['db_path']}")
        print(f"Touched days: {', '.join(result['touched_days']) or '(none)'}")
        print(
            "Processed {processed_files} files, skipped {skipped_files}, sessions {sessions_upserted}, messages {messages_upserted}, actions {actions_upserted}".format(
                **result["stats"]
            )
        )
        return 0
    if command == "generate-diaries":
        result = _run_generate_diaries(
            config,
            day=args.day,
            from_day=args.from_day,
            to_day=args.to_day,
            missing_only=args.missing_only,
        )
        print(f"Generated days: {', '.join(result['generated_days']) or '(none)'}")
        print(f"Skipped days: {', '.join(result['skipped_days']) or '(none)'}")
        return 0
    if command == "prepare-personas":
        result = _run_prepare_personas(config, subject_name=args.subject_name, overwrite=args.overwrite)
        print(f"Persona settings: {result['persona_config_path']}")
        print(f"Subject name: {result['subject_name_ja']}")
        print(f"Actors: {', '.join(result['actors'])}")
        return 0
    if command == "sync":
        result = _run_sync(config, explicit_day=args.day)
        print(f"DB: {result['db_path']}")
        print(f"Touched days: {', '.join(result['touched_days']) or '(none)'}")
        print(f"Schedule file: {result['schedule_path']}")
        print(
            "Processed {processed_files} files, skipped {skipped_files}, sessions {sessions_upserted}, messages {messages_upserted}, actions {actions_upserted}".format(
                **result["stats"]
            )
        )
        return 0
    if command == "export-day":
        result = _run_export_day(config, args.day)
        print(f"Exported {result['records']} records for {result['day_key']}")
        return 0
    if command == "write-schedules":
        path = write_schedule_file(config)
        print(f"Wrote schedule file: {path}")
        return 0
    if command == "build-soul-analysis":
        result = _run_build_soul_analysis(
            config,
            subject_name=args.subject_name,
            label=args.label,
            from_day=args.from_day,
            to_day=args.to_day,
        )
        print(f"Package root: {result['package_root']}")
        print(f"Manual input root: {result['manual_input_root']}")
        print(f"Complete prompt: {result['complete_prompt']}")
        return 0
    parser.error(f"Unsupported command: {command}")
    return 2
