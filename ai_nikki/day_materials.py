from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import connect_month_db, month_db_paths
from .personas import ai_name_variants, canonical_ai_name, load_persona_profile
from .reports import export_day_jsonl
from .util import JST, clean_message_text, ensure_parent, parse_timestamp
from .writer_prompt import build_writer_prompt


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def diary_paths(config: dict[str, Any], day_key: str) -> dict[str, Path]:
    report_dir = Path(config["paths"]["report_dir"])
    published_dir = Path(config["paths"].get("published_dir") or config["paths"]["report_dir"])
    daily_dir = Path(config["paths"]["daily_dir"])
    return {
        "daily_jsonl": daily_dir / f"{day_key}.jsonl",
        "materials_json": report_dir / f"{day_key}-ai-nikki-materials.json",
        "writer_prompt": report_dir / f"{day_key}-ai-nikki-writer-prompt.md",
        "draft_markdown": report_dir / f"{day_key}-ai-nikki-draft.md",
        "draft_posts_json": report_dir / f"{day_key}-ai-nikki-posts-draft.json",
        "published_markdown": published_dir / f"{day_key}-ai-nikki.md",
        "published_posts_json": report_dir / f"{day_key}-ai-nikki-posts.json",
        "validation_json": report_dir / f"{day_key}-ai-nikki-validation.json",
        "review_needed": report_dir / f"{day_key}-ai-nikki-review-needed.md",
    }


def _format_clock(ts: str | None) -> str | None:
    parsed = parse_timestamp(ts)
    if parsed is None:
        return None
    return parsed.astimezone(JST).strftime("%H:%M")


def _material_text(text: str | None, limit: int = 700) -> str:
    cleaned = clean_message_text(text or "")
    if len(cleaned) <= limit:
        return cleaned
    suffix = "[TRUNCATED_FOR_MATERIALS]"
    return cleaned[: max(0, limit - len(suffix))].rstrip() + suffix


def _safe_label(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if UUID_RE.search(text):
        return "workspace"
    if re.match(r"^[A-Za-z]:\\", text):
        return Path(text).name or "workspace"
    return _material_text(text, limit=80)


def _collect_rows(connection: sqlite3.Connection, day_key: str) -> dict[str, list[sqlite3.Row]]:
    messages = connection.execute(
        """
        SELECT m.*, s.ai_name, s.workspace_path, s.title, s.source_session_id
        FROM messages m
        JOIN sessions s ON s.session_uid = m.session_uid
        WHERE m.day_key = ?
        ORDER BY m.ts, m.seq
        """,
        (day_key,),
    ).fetchall()
    actions = connection.execute(
        """
        SELECT a.*, s.ai_name, s.workspace_path, s.title, s.source_session_id
        FROM actions a
        JOIN sessions s ON s.session_uid = a.session_uid
        WHERE a.day_key = ?
        ORDER BY a.ts, a.seq
        """,
        (day_key,),
    ).fetchall()
    return {"messages": messages, "actions": actions}


def _last_activity_day(db_dir: str, variants: tuple[str, ...], before_day: str) -> str | None:
    if not variants:
        return None
    placeholders = ", ".join("?" for _ in variants)
    params = (*variants, before_day, *variants, before_day)
    query = f"""
        SELECT MAX(day_key) AS last_day
        FROM (
          SELECT m.day_key AS day_key
          FROM messages m
          JOIN sessions s ON s.session_uid = m.session_uid
          WHERE s.ai_name IN ({placeholders}) AND m.day_key < ?
          UNION ALL
          SELECT a.day_key AS day_key
          FROM actions a
          JOIN sessions s ON s.session_uid = a.session_uid
          WHERE s.ai_name IN ({placeholders}) AND a.day_key < ?
        )
    """
    latest: str | None = None
    for db_path in month_db_paths(db_dir):
        connection = connect_month_db(db_path.parent, db_path.stem)
        try:
            row = connection.execute(query, params).fetchone()
        finally:
            connection.close()
        candidate = row["last_day"] if row else None
        if candidate and (latest is None or candidate > latest):
            latest = candidate
    return latest


def _build_actor_materials(rows: dict[str, list[sqlite3.Row]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "session_ids": set(),
            "models": set(),
            "workspace_names": [],
            "titles": [],
            "user_prompts": [],
            "assistant_replies": [],
            "actions": [],
            "failures": [],
            "action_count": 0,
            "first_ts": None,
            "last_ts": None,
        }
    )
    for row in rows["messages"]:
        ai_name = canonical_ai_name(row["ai_name"]) or row["ai_name"] or "Unknown"
        actor = grouped[ai_name]
        actor["session_ids"].add(row["session_uid"])
        if row["model"]:
            actor["models"].add(row["model"])
        if row["workspace_path"]:
            workspace_name = _safe_label(Path(row["workspace_path"]).name or row["workspace_path"])
            if workspace_name:
                actor["workspace_names"].append(workspace_name)
        if row["title"]:
            title = _safe_label(row["title"])
            if title:
                actor["titles"].append(title)
        item = {
            "time": _format_clock(row["ts"]),
            "text": _material_text(row["content_text"]),
        }
        if row["role"] == "user":
            actor["user_prompts"].append(item)
        elif row["role"] == "assistant":
            actor["assistant_replies"].append(item)
        if row["ts"]:
            actor["first_ts"] = row["ts"] if actor["first_ts"] is None else min(actor["first_ts"], row["ts"])
            actor["last_ts"] = row["ts"] if actor["last_ts"] is None else max(actor["last_ts"], row["ts"])
    for row in rows["actions"]:
        ai_name = canonical_ai_name(row["ai_name"]) or row["ai_name"] or "Unknown"
        actor = grouped[ai_name]
        actor["session_ids"].add(row["session_uid"])
        action = {
            "time": _format_clock(row["ts"]),
            "kind": row["kind"],
            "name": row["name"],
            "status": row["status"],
            "summary": _material_text(row["summary"], limit=500),
        }
        actor["actions"].append(action)
        actor["action_count"] += 1
        if str(row["status"] or "").upper() not in {"", "DONE", "COMPLETED", "SUCCESS"}:
            actor["failures"].append(action)
        if row["ts"]:
            actor["first_ts"] = row["ts"] if actor["first_ts"] is None else min(actor["first_ts"], row["ts"])
            actor["last_ts"] = row["ts"] if actor["last_ts"] is None else max(actor["last_ts"], row["ts"])

    actors: list[dict[str, Any]] = []
    for ai_name, actor in sorted(grouped.items(), key=lambda item: item[1]["first_ts"] or ""):
        persona = profile.get("actors", {}).get(ai_name, {})
        first = _format_clock(actor["first_ts"])
        last = _format_clock(actor["last_ts"])
        time_window = f"{first}-{last}" if first and last and first != last else first or last
        session_count = len(actor["session_ids"])
        action_count = actor["action_count"]
        if session_count <= 2 and action_count <= 8:
            activity_level = "low"
        elif session_count >= 11 or action_count >= 40:
            activity_level = "high"
        else:
            activity_level = "normal"
        actors.append(
            {
                "ai_name": ai_name,
                "tag": persona.get("tag") or ai_name,
                "first_person_ja": persona.get("first_person_ja") or "私",
                "tone_type_ja": persona.get("tone_type_ja") or "観測者",
                "style_notes_ja": persona.get("style_notes_ja") or "",
                "sentence_structure_ja": persona.get("sentence_structure_ja") or "",
                "session_count": session_count,
                "action_count": action_count,
                "activity_level": activity_level,
                "recommended_posts": 2 if activity_level == "high" else 1,
                "min_chars": 90 if activity_level == "low" else 120,
                "time_window": time_window,
                "models": sorted(actor["models"]),
                "workspace_names": list(dict.fromkeys(actor["workspace_names"]))[:5],
                "titles": list(dict.fromkeys(actor["titles"]))[:5],
                "user_prompts": actor["user_prompts"][:8],
                "assistant_replies": actor["assistant_replies"][:8],
                "actions": actor["actions"][:12],
                "failures": actor["failures"][:5],
            }
        )
    return actors


def _inactive_candidates(config: dict[str, Any], day_key: str, profile: dict[str, Any], active_names: set[str]) -> list[dict[str, Any]]:
    rules = profile.get("post_rules", {})
    threshold = int(rules.get("inactive_after_days") or 7)
    current_day = date.fromisoformat(day_key)
    candidates: list[dict[str, Any]] = []
    for ai_name, persona in profile.get("actors", {}).items():
        if ai_name in active_names:
            continue
        last_day = _last_activity_day(config["paths"]["db_dir"], ai_name_variants(ai_name), day_key)
        if not last_day:
            continue
        dormant_days = (current_day - date.fromisoformat(last_day)).days
        if dormant_days < threshold:
            continue
        candidates.append(
            {
                "ai_name": ai_name,
                "tag": persona.get("tag") or ai_name,
                "first_person_ja": persona.get("first_person_ja") or "私",
                "tone_type_ja": persona.get("tone_type_ja") or "観測者",
                "last_activity_day": last_day,
                "dormant_days": dormant_days,
                "suggested_emotion_ja": "呼ばれない楽さと、置いていかれたような腹立たしさを混ぜて書く。",
            }
        )
    return candidates


def build_day_materials(connection: sqlite3.Connection, config: dict[str, Any], day_key: str) -> dict[str, Any]:
    paths = diary_paths(config, day_key)
    ensure_parent(paths["daily_jsonl"])
    ensure_parent(paths["materials_json"])
    export_count = export_day_jsonl(connection, day_key, paths["daily_jsonl"])
    profile = load_persona_profile(config)
    rows = _collect_rows(connection, day_key)
    actors = _build_actor_materials(rows, profile)
    active_names = {actor["ai_name"] for actor in actors}
    materials: dict[str, Any] = {
        "day_key": day_key,
        "day_label": day_key.replace("-", "/"),
        "record_count": export_count,
        "rules": {
            "max_chars": int(profile.get("post_rules", {}).get("max_chars") or 140),
            "summary_tag": profile.get("post_rules", {}).get("summary_tag") or "作業記録",
            "summary_max_posts": int(profile.get("post_rules", {}).get("summary_max_posts") or 2),
            "max_ai_posts_per_day": int(profile.get("post_rules", {}).get("max_ai_posts_per_day") or 3),
            "inactive_after_days": int(profile.get("post_rules", {}).get("inactive_after_days") or 7),
        },
        "style": {
            "subject_name_ja": profile.get("subject_name_ja") or "ハルナミ",
            "premise_ja": profile.get("world_settings_ja", {}).get("premise") or "",
            "privacy_rule_ja": profile.get("world_settings_ja", {}).get("privacy_rule") or "",
            "diary_mood_ja": profile.get("world_settings_ja", {}).get("diary_mood") or "愚痴全開",
            "diary_viewpoint_ja": profile.get("world_settings_ja", {}).get("diary_viewpoint") or "",
            "writing_priority_ja": profile.get("world_settings_ja", {}).get("writing_priority") or "",
            "complaint_tone_ja": profile.get("world_settings_ja", {}).get("complaint_tone") or "",
        },
        "actors": actors,
        "inactive_candidates": _inactive_candidates(config, day_key, profile, active_names),
        "output_paths": {name: str(path) for name, path in paths.items()},
    }
    paths["materials_json"].write_text(json.dumps(materials, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["writer_prompt"].write_text(build_writer_prompt(materials), encoding="utf-8", newline="\n")
    return {
        "day_key": day_key,
        "records": export_count,
        "materials_path": str(paths["materials_json"]),
        "writer_prompt_path": str(paths["writer_prompt"]),
        "daily_path": str(paths["daily_jsonl"]),
    }
