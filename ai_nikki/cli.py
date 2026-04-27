from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from .config import load_config
from .day_materials import build_day_materials, diary_paths
from .db import (
    DATA_SCHEMA,
    available_days,
    begin_run,
    connect_day_db,
    connect_month_db,
    connect_state_db,
    finish_run,
    get_file_state,
    month_db_path,
    month_key_for_day_key,
    touched_month_keys,
    update_file_state,
    upsert_actions,
    upsert_messages,
    upsert_sessions,
    upsert_source,
)
from .personas import write_persona_config_from_db_dir
from .importers import EXTRACTOR_VERSION, SOURCE_DEFINITIONS, discover_files, file_fingerprint, parse_file
from .post_validator import mark_review_needed, publish_diary, validate_draft
from .reports import export_day_jsonl, write_schedule_file
from .soul_analysis import build_soul_analysis_package
from .util import ensure_directory, now_utc_iso, stable_id


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_nikki")
    parser.add_argument("--config", help="Path to config JSON", default=None)
    subparsers = parser.add_subparsers(dest="command", required=False)

    ingest = subparsers.add_parser("ingest", help="Ingest logs into the monthly SQLite stores only")
    ingest.add_argument("--day", help="Optional day to force into the result summary", default=None)

    generate_diaries = subparsers.add_parser(
        "generate-diaries",
        help="Build daily JSONL, diary materials, and writer prompts from the monthly databases",
    )
    generate_diaries.add_argument("--day", help="Generate one specific day only", default=None)
    generate_diaries.add_argument("--from-day", help="Lower bound day key (YYYY-MM-DD)", default=None)
    generate_diaries.add_argument("--to-day", help="Upper bound day key (YYYY-MM-DD)", default=None)
    generate_diaries.add_argument(
        "--missing-only",
        action="store_true",
        help="Only build days whose JSONL, materials, or writer prompt does not exist yet",
    )

    personas = subparsers.add_parser(
        "prepare-personas",
        help="Create or refresh an editable Japanese AI-Nikki persona Markdown file",
    )
    personas.add_argument("--subject-name", default=None, help="Display name used inside the diary setting")
    personas.add_argument("--overwrite", action="store_true", help="Overwrite existing local persona file")

    sync = subparsers.add_parser("sync", help="Ingest logs, build diary materials, and refresh schedules")
    sync.add_argument("--day", help="Only regenerate a specific day after sync", default=None)

    build_materials = subparsers.add_parser("build-diary-materials", help="Build writer materials for one diary day")
    build_materials.add_argument("--day", required=True, help="Day key in YYYY-MM-DD")

    validate = subparsers.add_parser("validate-diary", help="Validate AI-written diary draft files")
    validate.add_argument("--day", required=True, help="Day key in YYYY-MM-DD")

    publish = subparsers.add_parser("publish-diary", help="Publish validated AI-written diary draft files")
    publish.add_argument("--day", required=True, help="Day key in YYYY-MM-DD")
    publish.add_argument("--force", action="store_true", help="Publish even when validation fails")

    review_needed = subparsers.add_parser("mark-review-needed", help="Keep a failed draft and write review notes")
    review_needed.add_argument("--day", required=True, help="Day key in YYYY-MM-DD")
    review_needed.add_argument("--attempts", type=int, default=3, help="Number of AI rewrite attempts")

    export_day = subparsers.add_parser("export-day", help="Export one day JSONL from the existing databases")
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


def _select_days(
    available_day_keys: list[str],
    *,
    day: str | None,
    from_day: str | None,
    to_day: str | None,
) -> list[str]:
    if day:
        return [day]
    selected = available_day_keys
    if from_day:
        selected = [value for value in selected if value >= from_day]
    if to_day:
        selected = [value for value in selected if value <= to_day]
    return selected


def _ensure_month_connection(cache: dict[str, sqlite3.Connection], db_dir: str, month_key: str) -> sqlite3.Connection:
    connection = cache.get(month_key)
    if connection is None:
        connection = connect_month_db(db_dir, month_key)
        cache[month_key] = connection
    return connection


def _split_records_by_month(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        month_key = month_key_for_day_key(record.get("day_key"))
        if not month_key:
            continue
        grouped.setdefault(month_key, []).append(record)
    return grouped


def _copy_table_rows(source: sqlite3.Connection, target: sqlite3.Connection, table: str) -> None:
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})").fetchall()]
    if not columns:
        return
    rows = source.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    if not rows:
        return
    placeholders = ", ".join("?" for _ in columns)
    target.executemany(
        f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def _build_aggregate_connection(config: dict[str, Any], *, from_day: str | None, to_day: str | None) -> sqlite3.Connection:
    selected_days = _select_days(available_days(config["paths"]["db_dir"]), day=None, from_day=from_day, to_day=to_day)
    month_keys = sorted({month_key_for_day_key(day_key) for day_key in selected_days if month_key_for_day_key(day_key)})
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(DATA_SCHEMA)
    for month_key in month_keys:
        if not month_key:
            continue
        source = connect_month_db(config["paths"]["db_dir"], month_key)
        try:
            for table in ("sessions", "messages", "actions", "diary_posts"):
                _copy_table_rows(source, connection, table)
        finally:
            source.close()
    connection.commit()
    return connection


def _run_ingest(config: dict[str, Any], explicit_day: str | None = None) -> dict[str, Any]:
    state_connection = connect_state_db(config["paths"]["db_dir"])
    month_connections: dict[str, sqlite3.Connection] = {}
    shared_state: dict[str, Any] = {}
    run_id = stable_id("run", now_utc_iso())
    begin_run(state_connection, run_id, "ingest")
    stats = {
        "discovered_files": 0,
        "processed_files": 0,
        "skipped_files": 0,
        "sessions_upserted": 0,
        "messages_upserted": 0,
        "actions_upserted": 0,
    }
    touched_days: set[str] = set()
    touched_months: set[str] = set()
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
                state_connection,
                source_id=source_id,
                display_name=definition.display_name,
                source_type=definition.source_type,
                root_path=";".join(patterns),
                extractor_version=EXTRACTOR_VERSION,
            )
            for file_path in files:
                stats["discovered_files"] += 1
                fingerprint = file_fingerprint(file_path)
                file_state = get_file_state(state_connection, source_id, str(file_path))
                force_reparse = source_id == "codex_desktop_bridge" and shared_state.get("force_codex_bridge")
                if file_state and file_state["fingerprint"] == fingerprint and not force_reparse:
                    stats["skipped_files"] += 1
                    continue

                parsed = parse_file(source_id, file_path, config, shared_state)
                fallback_ts = None
                if parsed["sessions"]:
                    fallback_ts = parsed["sessions"][0].get("started_at") or parsed["sessions"][0].get("ended_at")
                months = touched_month_keys(parsed["messages"], parsed["actions"], fallback_ts=fallback_ts)
                message_groups = _split_records_by_month(parsed["messages"])
                action_groups = _split_records_by_month(parsed["actions"])
                for month_key in months:
                    connection = _ensure_month_connection(month_connections, config["paths"]["db_dir"], month_key)
                    month_messages = message_groups.get(month_key, [])
                    month_actions = action_groups.get(month_key, [])
                    if parsed["sessions"]:
                        upsert_sessions(connection, parsed["sessions"])
                    if month_messages:
                        upsert_messages(connection, month_messages)
                    if month_actions:
                        upsert_actions(connection, month_actions)
                    touched_months.add(month_key)
                update_file_state(
                    state_connection,
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
        finish_run(state_connection, run_id, status="completed", message="ingest completed", stats=stats)
        state_connection.commit()
        for connection in month_connections.values():
            connection.commit()
        return {
            "run_id": run_id,
            "stats": stats,
            "touched_days": sorted(touched_days),
            "touched_months": sorted(touched_months),
            "db_dir": config["paths"]["db_dir"],
        }
    except Exception as exc:
        finish_run(state_connection, run_id, status="failed", message=str(exc), stats=stats)
        state_connection.commit()
        for connection in month_connections.values():
            connection.rollback()
        raise
    finally:
        state_connection.close()
        for connection in month_connections.values():
            connection.close()


def _run_generate_diaries(
    config: dict[str, Any],
    *,
    day: str | None,
    from_day: str | None,
    to_day: str | None,
    missing_only: bool,
) -> dict[str, Any]:
    daily_dir = Path(config["paths"]["daily_dir"])
    report_dir = Path(config["paths"]["report_dir"])
    ensure_directory(daily_dir)
    ensure_directory(report_dir)
    available_day_keys = available_days(config["paths"]["db_dir"])
    selected_days = _select_days(available_day_keys, day=day, from_day=from_day, to_day=to_day)
    built_days: list[str] = []
    skipped_days: list[str] = []
    month_connections: dict[str, sqlite3.Connection] = {}
    try:
        for day_key in selected_days:
            paths = diary_paths(config, day_key)
            if (
                missing_only
                and paths["daily_jsonl"].exists()
                and paths["materials_json"].exists()
                and paths["writer_prompt"].exists()
            ):
                skipped_days.append(day_key)
                continue
            month_key = month_key_for_day_key(day_key)
            if not month_key:
                continue
            connection = month_connections.get(month_key)
            if connection is None:
                connection = connect_month_db(config["paths"]["db_dir"], month_key)
                month_connections[month_key] = connection
            build_day_materials(connection, config, day_key)
            built_days.append(day_key)
        for connection in month_connections.values():
            connection.commit()
        return {
            "built_days": built_days,
            "skipped_days": skipped_days,
            "selected_days": selected_days,
        }
    finally:
        for connection in month_connections.values():
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
        "built_days": diary_result["built_days"],
        "schedule_path": str(schedule_path),
    }


def _run_export_day(config: dict[str, Any], day_key: str) -> dict[str, Any]:
    connection = connect_day_db(config["paths"]["db_dir"], day_key)
    try:
        count = export_day_jsonl(connection, day_key, Path(config["paths"]["daily_dir"]) / f"{day_key}.jsonl")
        connection.commit()
        return {"day_key": day_key, "records": count}
    finally:
        connection.close()


def _run_build_diary_materials(config: dict[str, Any], day_key: str) -> dict[str, Any]:
    connection = connect_day_db(config["paths"]["db_dir"], day_key)
    try:
        result = build_day_materials(connection, config, day_key)
        connection.commit()
        return result
    finally:
        connection.close()


def _run_prepare_personas(
    config: dict[str, Any],
    *,
    subject_name: str | None,
    overwrite: bool,
) -> dict[str, Any]:
    return write_persona_config_from_db_dir(config, subject_name=subject_name, overwrite=overwrite)


def _run_build_soul_analysis(
    config: dict[str, Any],
    *,
    subject_name: str,
    label: str,
    from_day: str | None,
    to_day: str | None,
) -> dict[str, Any]:
    connection = _build_aggregate_connection(config, from_day=from_day, to_day=to_day)
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
        print(f"DB directory: {result['db_dir']}")
        print(f"Touched months: {', '.join(result['touched_months']) or '(none)'}")
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
        print(f"Built material days: {', '.join(result['built_days']) or '(none)'}")
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
        print(f"DB directory: {result['db_dir']}")
        print(f"Touched months: {', '.join(result['touched_months']) or '(none)'}")
        print(f"Touched days: {', '.join(result['touched_days']) or '(none)'}")
        print(f"Built material days: {', '.join(result['built_days']) or '(none)'}")
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
    if command == "build-diary-materials":
        result = _run_build_diary_materials(config, args.day)
        print(f"Daily JSONL: {result['daily_path']}")
        print(f"Materials: {result['materials_path']}")
        print(f"Writer prompt: {result['writer_prompt_path']}")
        print(f"Records: {result['records']}")
        return 0
    if command == "validate-diary":
        result = validate_draft(config, args.day)
        print(f"Validation: {'OK' if result['ok'] else 'NG'}")
        if result["errors"]:
            print("Errors:")
            for error in result["errors"]:
                print(f"- {error}")
        return 0 if result["ok"] else 1
    if command == "publish-diary":
        result = publish_diary(config, args.day, force=args.force)
        validation = result["validation"]
        if result["published"]:
            print(f"Published diary: {result['published_markdown']}")
            print(f"Published posts: {result['published_posts_json']}")
            if result["forced"]:
                print("Published with --force despite validation errors.")
            return 0
        print("Diary draft was not published because validation failed.")
        for error in validation["errors"]:
            print(f"- {error}")
        return 1
    if command == "mark-review-needed":
        result = mark_review_needed(config, args.day, attempts=args.attempts)
        print(f"Review needed: {result['review_needed_path']}")
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
