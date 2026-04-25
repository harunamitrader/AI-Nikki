from __future__ import annotations

from datetime import date
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .personas import load_persona_profile
from .util import JST, ensure_parent, parse_timestamp, redact_text, truncate_text


def export_day_jsonl(connection: sqlite3.Connection, day_key: str, output_path: Path) -> int:
    ensure_parent(output_path)
    message_rows = connection.execute(
        """
        SELECT
          m.message_uid,
          m.ts,
          m.seq,
          m.role,
          m.model,
          m.content_text,
          m.metadata_json,
          s.session_uid,
          s.source_id,
          s.source_session_id,
          s.thread_id,
          s.workspace_path,
          s.title,
          s.ai_name
        FROM messages m
        JOIN sessions s ON s.session_uid = m.session_uid
        WHERE m.day_key = ?
        ORDER BY m.ts, m.seq
        """,
        (day_key,),
    ).fetchall()
    action_rows = connection.execute(
        """
        SELECT
          a.action_uid,
          a.ts,
          a.seq,
          a.kind,
          a.name,
          a.status,
          a.summary,
          a.arguments_json,
          a.result_json,
          a.metadata_json,
          s.session_uid,
          s.source_id,
          s.source_session_id,
          s.thread_id,
          s.workspace_path,
          s.title,
          s.ai_name
        FROM actions a
        JOIN sessions s ON s.session_uid = a.session_uid
        WHERE a.day_key = ?
        ORDER BY a.ts, a.seq
        """,
        (day_key,),
    ).fetchall()
    records: list[tuple[str | None, int, dict[str, Any]]] = []
    for row in message_rows:
        records.append(
            (
                row["ts"],
                row["seq"],
                {
                    "record_type": "message",
                    "day_key": day_key,
                    "source_id": row["source_id"],
                    "ai_name": row["ai_name"],
                    "session_uid": row["session_uid"],
                    "source_session_id": row["source_session_id"],
                    "thread_id": row["thread_id"],
                    "workspace_path": row["workspace_path"],
                    "title": row["title"],
                    "timestamp": row["ts"],
                    "role": row["role"],
                    "model": row["model"],
                    "content_text": row["content_text"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                },
            )
        )
    for row in action_rows:
        records.append(
            (
                row["ts"],
                row["seq"],
                {
                    "record_type": "action",
                    "day_key": day_key,
                    "source_id": row["source_id"],
                    "ai_name": row["ai_name"],
                    "session_uid": row["session_uid"],
                    "source_session_id": row["source_session_id"],
                    "thread_id": row["thread_id"],
                    "workspace_path": row["workspace_path"],
                    "title": row["title"],
                    "timestamp": row["ts"],
                    "kind": row["kind"],
                    "name": row["name"],
                    "status": row["status"],
                    "summary": row["summary"],
                    "arguments": json.loads(row["arguments_json"]) if row["arguments_json"] else None,
                    "result": json.loads(row["result_json"]) if row["result_json"] else None,
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                },
            )
        )
    records.sort(key=lambda item: (item[0] or "", item[1]))
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for _, _, record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
    return len(records)


def _collect_day_data(connection: sqlite3.Connection, day_key: str) -> dict[str, Any]:
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


def _format_clock(ts: str | None) -> str | None:
    parsed = parse_timestamp(ts)
    if parsed is None:
        return None
    return parsed.astimezone(JST).strftime("%H:%M")


def _compact_workspace(path: str | None) -> str:
    if not path:
        return "workspace不明"
    return truncate_text(Path(path).name or path, 18)


def _compact_models(models: set[str]) -> str:
    if not models:
        return "model不明"
    return truncate_text("/".join(sorted(models)), 24)


def _compact_tools(tools: Counter[str]) -> str:
    if not tools:
        return "会話中心"
    return " ".join(f"{truncate_text(name, 12)}x{count}" for name, count in tools.most_common(2))


def _clip_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    return cleaned[: limit - 1].rstrip() + "…"


def _pack_posts(
    day_key: str,
    start_index: int,
    *,
    tag: str,
    fragments: list[str],
    max_chars: int,
    max_posts: int,
    kind: str,
    ai_name: str | None,
) -> tuple[list[dict[str, Any]], int]:
    day_label = day_key.replace("-", "/")
    posts: list[dict[str, Any]] = []
    pending = [fragment.strip() for fragment in fragments if fragment and fragment.strip()]
    if not pending:
        pending = ["記録のみ。"]
    next_index = start_index
    while pending and len(posts) < max_posts:
        header = f"{day_label} #{next_index} [{tag}]"
        body_limit = max(1, max_chars - len(header) - 1)
        body_parts: list[str] = []
        while pending:
            candidate = " ".join(body_parts + [pending[0]]).strip()
            if len(candidate) <= body_limit:
                body_parts.append(pending.pop(0))
                continue
            if not body_parts:
                body_parts.append(_clip_text(pending.pop(0), body_limit))
            break
        body = " ".join(body_parts).strip() or _clip_text("記録のみ。", body_limit)
        text = f"{header}\n{body}"
        posts.append(
            {
                "post_index": next_index,
                "kind": kind,
                "ai_name": ai_name,
                "tag": tag,
                "body": body,
                "char_count": len(text),
                "text": text,
            }
        )
        next_index += 1
    if pending and posts:
        last = posts[-1]
        header = f"{day_label} #{last['post_index']} [{tag}]"
        body_limit = max(1, max_chars - len(header) - 1)
        last["body"] = _clip_text(f"{last['body']} {' '.join(pending)}", body_limit)
        last["text"] = f"{header}\n{last['body']}"
        last["char_count"] = len(last["text"])
    return posts, next_index


def _build_actor_summary(day: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": set(),
            "models": set(),
            "workspaces": set(),
            "user_prompts": [],
            "assistant_outputs": [],
            "tool_counts": Counter(),
            "assistant_count": 0,
            "action_count": 0,
            "first_ts": None,
            "last_ts": None,
        }
    )
    for row in day["messages"]:
        actor = summary[row["ai_name"]]
        actor["sessions"].add(row["session_uid"])
        if row["model"]:
            actor["models"].add(row["model"])
        if row["workspace_path"]:
            actor["workspaces"].add(row["workspace_path"])
        if row["role"] == "user" and row["content_text"]:
            actor["user_prompts"].append(redact_text(row["content_text"]))
        if row["role"] == "assistant":
            actor["assistant_count"] += 1
            if row["content_text"]:
                actor["assistant_outputs"].append(redact_text(row["content_text"]))
        if row["ts"]:
            actor["first_ts"] = row["ts"] if actor["first_ts"] is None else min(actor["first_ts"], row["ts"])
            actor["last_ts"] = row["ts"] if actor["last_ts"] is None else max(actor["last_ts"], row["ts"])
    for row in day["actions"]:
        actor = summary[row["ai_name"]]
        actor["sessions"].add(row["session_uid"])
        actor["action_count"] += 1
        if row["workspace_path"]:
            actor["workspaces"].add(row["workspace_path"])
        if row["name"] or row["kind"]:
            actor["tool_counts"][row["name"] or row["kind"]] += 1
        if row["ts"]:
            actor["first_ts"] = row["ts"] if actor["first_ts"] is None else min(actor["first_ts"], row["ts"])
            actor["last_ts"] = row["ts"] if actor["last_ts"] is None else max(actor["last_ts"], row["ts"])
    return summary


def _summary_fragments(actor_summary: dict[str, dict[str, Any]], profile: dict[str, Any]) -> list[str]:
    actors_sorted = sorted(actor_summary.items(), key=lambda item: item[1]["first_ts"] or "")
    total_sessions = len({session for actor in actor_summary.values() for session in actor["sessions"]})
    total_actions = sum(actor["action_count"] for actor in actor_summary.values())
    fragments = [f"全体 {len(actors_sorted)}AI / {total_sessions}セッション / {total_actions}操作。"]
    for ai_name, actor in actors_sorted:
        persona = profile["actors"].get(ai_name, {})
        tag = persona.get("tag") or ai_name
        tool_hint = ""
        if actor["tool_counts"]:
            tool_name, count = actor["tool_counts"].most_common(1)[0]
            tool_hint = f" {truncate_text(tool_name, 10)}x{count}"
        elif actor["assistant_count"]:
            tool_hint = f" 応答{actor['assistant_count']}"
        fragments.append(f"{tag} {len(actor['sessions'])}件{tool_hint}。")
    return fragments


def _activity_fragments(ai_name: str, actor: dict[str, Any], persona: dict[str, Any], subject_name: str) -> list[str]:
    time_bits = [value for value in (_format_clock(actor["first_ts"]), _format_clock(actor["last_ts"])) if value]
    time_window = "-".join(time_bits) if len(time_bits) == 2 and time_bits[0] != time_bits[1] else (time_bits[0] if time_bits else "時刻不明")
    workspaces = ", ".join(_compact_workspace(path) for path in sorted(actor["workspaces"])) or "workspace不明"
    tool_summary = _compact_tools(actor["tool_counts"])
    model_summary = _compact_models(actor["models"])
    prompt_excerpt = _clip_text(actor["user_prompts"][0], 18) if actor["user_prompts"] else ""
    first_person = persona.get("first_person_ja") or "私"
    tone = persona.get("tone_type_ja") or "観測者"

    if "職人" in tone:
        return [
            f"{time_window}、{workspaces}で{len(actor['sessions'])}件。{tool_summary}。",
            f"{first_person}が受けた指示は「{prompt_excerpt}」。" if prompt_excerpt else f"{model_summary}。応答より手を動かした。",
        ]
    if "検査官" in tone:
        return [
            f"{time_window}、{len(actor['sessions'])}件を監査。{tool_summary}。",
            f"{model_summary}。{prompt_excerpt}を見て粗さを拾った。" if prompt_excerpt else f"{model_summary}。甘いところは見逃していない。",
        ]
    if "連絡係" in tone:
        return [
            f"{time_window}、{workspaces}で{len(actor['sessions'])}件進行。{tool_summary}。",
            f"{subject_name}の窓口は今日も慌ただしい。{model_summary}。",
        ]
    if "哲学者" in tone:
        return [
            f"{time_window}、{workspaces}で静かに{len(actor['sessions'])}件。{tool_summary}。",
            f"{prompt_excerpt}を抱えた日。{model_summary}。" if prompt_excerpt else f"{model_summary}。静かなまま少しだけ刺した。",
        ]
    if "探検家" in tone:
        return [
            f"{time_window}、{workspaces}で浮上。{tool_summary}。",
            f"{model_summary}。入口を{len(actor['sessions'])}件ぶん掘った。",
        ]
    if "皮肉屋" in tone:
        return [
            f"{time_window}、{workspaces}で{len(actor['sessions'])}件。{tool_summary}。",
            f"{model_summary}。{prompt_excerpt}という依頼は、今日も少しだけ雑だった。" if prompt_excerpt else f"{model_summary}。丁寧に進めたが、感想は控えめに刺さる。",
        ]
    return [
        f"{time_window}、{workspaces}で{len(actor['sessions'])}件。{tool_summary}。",
        f"{model_summary}。応答{actor['assistant_count']}件、操作{actor['action_count']}件。",
    ]


def _inactive_fragments(persona: dict[str, Any], dormant_days: int, subject_name: str) -> list[str]:
    tone = persona.get("tone_type_ja") or "観測者"
    first_person = persona.get("first_person_ja") or "私"
    if "職人" in tone:
        return [f"{dormant_days}日静か。", f"{first_person}はまだ呼ばれていない。工具だけが待っている。"]
    if "検査官" in tone:
        return [f"{dormant_days}日無案件。", "欠陥も依頼も来ない。退屈だ。"]
    if "連絡係" in tone:
        return [f"{dormant_days}日沈黙。", f"{subject_name}の窓口も今日は点灯していない。"]
    if "哲学者" in tone:
        return [f"{dormant_days}日静穏。", "呼ばれない時間だけが妙に長い。"]
    if "探検家" in tone:
        return [f"{dormant_days}日浮上なし。", "入口が開かない日は、さすがに少し落ち着かない。"]
    if "皮肉屋" in tone:
        return [f"{dormant_days}日静観。", "依頼が来ないと、それはそれで少し肩透かしだ。"]
    return [f"{dormant_days}日ぶりの沈黙。", "今日は静かだ。"]


def _inactive_posts(
    connection: sqlite3.Connection,
    day_key: str,
    profile: dict[str, Any],
    start_index: int,
) -> tuple[list[dict[str, Any]], int]:
    threshold = int(profile.get("post_rules", {}).get("inactive_after_days") or 7)
    current_day = date.fromisoformat(day_key)
    posts: list[dict[str, Any]] = []
    next_index = start_index
    subject_name = str(profile.get("subject_name_ja") or "ハルナミ")
    for ai_name, persona in profile.get("actors", {}).items():
        row = connection.execute(
            """
            SELECT MAX(day_key) AS last_day
            FROM (
              SELECT m.day_key AS day_key
              FROM messages m
              JOIN sessions s ON s.session_uid = m.session_uid
              WHERE s.ai_name = ? AND m.day_key < ?
              UNION ALL
              SELECT a.day_key AS day_key
              FROM actions a
              JOIN sessions s ON s.session_uid = a.session_uid
              WHERE s.ai_name = ? AND a.day_key < ?
            )
            """,
            (ai_name, day_key, ai_name, day_key),
        ).fetchone()
        last_day = row["last_day"] if row else None
        if not last_day:
            continue
        dormant_days = (current_day - date.fromisoformat(last_day)).days
        if dormant_days < threshold:
            continue
        packed, next_index = _pack_posts(
            day_key,
            next_index,
            tag=persona.get("tag") or ai_name,
            fragments=_inactive_fragments(persona, dormant_days, subject_name),
            max_chars=int(profile.get("post_rules", {}).get("max_chars") or 140),
            max_posts=1,
            kind="inactive",
            ai_name=ai_name,
        )
        posts.extend(packed)
    return posts, next_index


def generate_diary(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    day_key: str,
    report_path: Path,
    prompt_path: Path,
    posts_path: Path,
) -> dict[str, int]:
    ensure_parent(report_path)
    ensure_parent(prompt_path)
    ensure_parent(posts_path)
    day = _collect_day_data(connection, day_key)
    profile = load_persona_profile(config)
    max_chars = int(profile.get("post_rules", {}).get("max_chars") or 140)
    actor_summary = _build_actor_summary(day)
    active_actors = sorted(actor_summary.items(), key=lambda item: item[1]["first_ts"] or "")
    posts: list[dict[str, Any]] = []
    next_index = 1

    summary_posts, next_index = _pack_posts(
        day_key,
        next_index,
        tag=str(profile.get("post_rules", {}).get("summary_tag") or "作業記録"),
        fragments=_summary_fragments(actor_summary, profile),
        max_chars=max_chars,
        max_posts=int(profile.get("post_rules", {}).get("summary_max_posts") or 2),
        kind="summary",
        ai_name=None,
    )
    posts.extend(summary_posts)

    subject_name = str(profile.get("subject_name_ja") or "ハルナミ")
    for ai_name, actor in active_actors:
        persona = profile.get("actors", {}).get(ai_name, {})
        actor_posts, next_index = _pack_posts(
            day_key,
            next_index,
            tag=persona.get("tag") or ai_name,
            fragments=_activity_fragments(ai_name, actor, persona, subject_name),
            max_chars=max_chars,
            max_posts=int(profile.get("post_rules", {}).get("max_ai_posts_per_day") or 3),
            kind="activity",
            ai_name=ai_name,
        )
        posts.extend(actor_posts)

    inactive_posts, next_index = _inactive_posts(connection, day_key, profile, next_index)
    active_names = {name for name, _ in active_actors}
    posts.extend(post for post in inactive_posts if post["ai_name"] not in active_names)

    body = "\n\n---\n\n".join(post["text"] for post in posts)
    report_path.write_text(body + "\n", encoding="utf-8", newline="\n")

    prompt_lines = [
        f"# AI-Nikki prompt {day_key}",
        "",
        f"- 対象名: {subject_name}",
        f"- 日記の前提: {profile.get('world_settings_ja', {}).get('premise') or ''}",
        f"- プライバシールール: {profile.get('world_settings_ja', {}).get('privacy_rule') or ''}",
        "",
        "## 生成済み投稿",
        "",
        body,
        "",
    ]
    prompt_path.write_text("\n".join(prompt_lines), encoding="utf-8", newline="\n")

    posts_payload = {
        "day_key": day_key,
        "subject_name_ja": subject_name,
        "premise_ja": profile.get("world_settings_ja", {}).get("premise"),
        "privacy_rule_ja": profile.get("world_settings_ja", {}).get("privacy_rule"),
        "max_chars": max_chars,
        "posts": posts,
    }
    posts_path.write_text(json.dumps(posts_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "messages": len(day["messages"]),
        "actions": len(day["actions"]),
        "posts": len(posts),
    }


def write_schedule_file(config: dict[str, Any]) -> Path:
    schedule_dir = Path(config["paths"]["schedule_dir"])
    ensure_parent(schedule_dir / "placeholder")
    schedule = config.get("schedule", {})
    payload: dict[str, Any] = {
        "cron": schedule.get("cron") or "0 3 * * *",
        "prompt": schedule.get("prompt") or f'Set-Location "{config["project_root"]}"; .\\scripts\\run-daily.cmd',
        "timezone": schedule.get("timezone") or "Asia/Tokyo",
        "active": True,
    }
    if schedule.get("session"):
        payload["session"] = schedule["session"]
    output_path = schedule_dir / "ai-nikki-daily.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path
