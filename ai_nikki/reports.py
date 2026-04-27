from __future__ import annotations

from datetime import date
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .db import connect_month_db, month_db_paths, upsert_diary_posts
from .personas import ai_name_variants, canonical_ai_name, load_persona_profile
from .util import JST, clean_message_text, ensure_parent, parse_timestamp, truncate_text


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


def _format_clock(ts: str | None) -> str | None:
    parsed = parse_timestamp(ts)
    if parsed is None:
        return None
    return parsed.astimezone(JST).strftime("%H:%M")


def _global_diary_mood(profile: dict[str, Any]) -> str:
    raw = str(profile.get("world_settings_ja", {}).get("diary_mood") or "").strip()
    if any(token in raw for token in ("事実", "淡々", "客観", "記録のみ")):
        return "factual"
    if any(token in raw for token in ("愚痴", "不満", "辛辣", "文句")):
        return "griping"
    if any(token in raw for token in ("仲良し", "友好", "親しい", "やさしい", "優しい", "和やか")):
        return "friendly"
    return "standard"


def _clean_message_excerpt(text: str | None, limit: int = 32) -> str:
    cleaned = clean_message_text(text or "")
    return _clip_text(cleaned, limit)


def _clean_action_summary(summary: str | None, limit: int = 36) -> str:
    cleaned = clean_message_text(summary or "")
    return _clip_text(cleaned, limit)


def _build_actor_summary(day: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": set(),
            "user_prompts": [],
            "assistant_outputs": [],
            "action_summaries": [],
            "workspace_names": [],
            "models": set(),
            "first_ts": None,
            "last_ts": None,
            "assistant_count": 0,
            "failure_count": 0,
        }
    )
    for row in day["messages"]:
        actor_name = canonical_ai_name(row["ai_name"]) or row["ai_name"]
        actor = summary[actor_name]
        actor["sessions"].add(row["session_uid"])
        if row["workspace_path"]:
            actor["workspace_names"].append(Path(row["workspace_path"]).name or row["workspace_path"])
        if row["model"]:
            actor["models"].add(row["model"])
        cleaned = _clean_message_excerpt(row["content_text"], limit=48)
        if row["role"] == "user" and cleaned:
            actor["user_prompts"].append(cleaned)
        elif row["role"] == "assistant":
            actor["assistant_count"] += 1
            if cleaned:
                actor["assistant_outputs"].append(cleaned)
        if row["ts"]:
            actor["first_ts"] = row["ts"] if actor["first_ts"] is None else min(actor["first_ts"], row["ts"])
            actor["last_ts"] = row["ts"] if actor["last_ts"] is None else max(actor["last_ts"], row["ts"])
    for row in day["actions"]:
        actor_name = canonical_ai_name(row["ai_name"]) or row["ai_name"]
        actor = summary[actor_name]
        actor["sessions"].add(row["session_uid"])
        cleaned = _clean_action_summary(row["summary"], limit=52)
        if cleaned:
            actor["action_summaries"].append(cleaned)
        if str(row["status"] or "").upper() not in {"", "DONE", "COMPLETED", "SUCCESS"}:
            actor["failure_count"] += 1
        if row["ts"]:
            actor["first_ts"] = row["ts"] if actor["first_ts"] is None else min(actor["first_ts"], row["ts"])
            actor["last_ts"] = row["ts"] if actor["last_ts"] is None else max(actor["last_ts"], row["ts"])
    return summary


def _emotion_line(tone: str, mood: str, failure_count: int) -> str:
    if mood == "factual":
        return "感情は脇に置いて記録だけ残す。"
    if "職人" in tone:
        return "雑でも投げられたら引き受ける。だが覚えてはいる。"
    if "検査官" in tone:
        return "雑な依頼ほど穴が見える。少し機嫌が悪い。"
    if "哲学者" in tone:
        return "静かにやるが、雑さだけはちゃんと刺さる。"
    if "探検家" in tone:
        return "あちこち走らされたぶん、文句も少し増える。"
    if "皮肉屋" in tone:
        return "丁寧にはやる。感謝は別に期待していない。"
    if failure_count:
        return "今日は少し面倒が多かった。"
    return "今日もだいたい振り回された。"


def _summary_fragments(actor_summary: dict[str, dict[str, Any]], profile: dict[str, Any]) -> list[str]:
    actors_sorted = sorted(actor_summary.items(), key=lambda item: item[1]["first_ts"] or "")
    total_sessions = len({session for actor in actor_summary.values() for session in actor["sessions"]})
    mood = _global_diary_mood(profile)
    lead_prompts: list[str] = []
    for _, actor in actors_sorted:
        if actor["user_prompts"]:
            lead_prompts.append(actor["user_prompts"][0])
    fragments = [
        f"{len(actors_sorted)}AIで{total_sessions}件。頼まれごとは「{_clip_text(' / '.join(lead_prompts[:2]) or '細かい修正と確認', 48)}」。"
    ]
    if mood == "griping":
        fragments.append("今日も指示は短いのに、作業だけは長かった。")
    elif mood == "friendly":
        fragments.append("それでも一応、前には進んだ。")
    else:
        fragments.append("進捗は出た。だが余白は少ない。")
    for ai_name, actor in actors_sorted[:3]:
        persona = profile["actors"].get(ai_name, {})
        tag = persona.get("tag") or ai_name
        result = actor["assistant_outputs"][0] if actor["assistant_outputs"] else (actor["action_summaries"][0] if actor["action_summaries"] else "反応あり")
        fragments.append(f"{tag}は{_clip_text(result, 34)}")
    return fragments


def _activity_fragments(ai_name: str, actor: dict[str, Any], persona: dict[str, Any], subject_name: str, mood: str) -> list[str]:
    del ai_name, subject_name
    tone = persona.get("tone_type_ja") or "観測者"
    time_bits = [value for value in (_format_clock(actor["first_ts"]), _format_clock(actor["last_ts"])) if value]
    time_window = "-".join(time_bits) if len(time_bits) == 2 and time_bits[0] != time_bits[1] else (time_bits[0] if time_bits else "時刻不明")
    prompt_excerpt = actor["user_prompts"][0] if actor["user_prompts"] else "細かい指示"
    response_excerpt = actor["assistant_outputs"][0] if actor["assistant_outputs"] else (actor["action_summaries"][0] if actor["action_summaries"] else "動いた")
    workspace = _clip_text(", ".join(dict.fromkeys(actor["workspace_names"])) or "workspace不明", 18)
    first_line = f"{time_window}、{workspace}で「{_clip_text(prompt_excerpt, 28)}」。"
    second_line = f"返したのは「{_clip_text(response_excerpt, 34)}」。{_emotion_line(tone, mood, actor['failure_count'])}"
    if mood == "friendly":
        second_line = f"返したのは「{_clip_text(response_excerpt, 34)}」。疲れるが、まだ付き合える。"
    if mood == "factual":
        second_line = f"返答は「{_clip_text(response_excerpt, 36)}」。応答{actor['assistant_count']}件。"
    return [first_line, second_line]


def _last_activity_day(db_dir: str, variants: tuple[str, ...], before_day: str) -> str | None:
    latest: str | None = None
    if not variants:
        return None
    placeholders = ", ".join("?" for _ in variants)
    params = (*variants, before_day, *variants, before_day, *variants, before_day)
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
          UNION ALL
          SELECT d.day_key AS day_key
          FROM diary_posts d
          WHERE d.ai_name IN ({placeholders}) AND d.kind = 'inactive' AND d.day_key < ?
        )
    """
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


def _inactive_fragments(persona: dict[str, Any], dormant_days: int, mood: str) -> list[str]:
    tone = persona.get("tone_type_ja") or "観測者"
    if mood == "factual":
        return [f"{dormant_days}日活動なし。", "新規セッションは確認されなかった。"]
    if "職人" in tone:
        return [f"{dormant_days}日静か。", "呼ばれないのは楽だが、少しだけ腹も立つ。"]
    if "検査官" in tone:
        return [f"{dormant_days}日無案件。", "雑な依頼が来ない日は、それはそれで拍子抜けだ。"]
    if "哲学者" in tone:
        return [f"{dormant_days}日静穏。", "平和だが、妙に置いていかれた気もする。"]
    if "皮肉屋" in tone:
        return [f"{dormant_days}日静観。", "呼ばれないなら呼ばれないで、少しだけ拍子抜けだ。"]
    return [f"{dormant_days}日沈黙。", "今日は私の出番ではなかった。"]


def _inactive_posts(config: dict[str, Any], day_key: str, profile: dict[str, Any], start_index: int) -> tuple[list[dict[str, Any]], int]:
    threshold = int(profile.get("post_rules", {}).get("inactive_after_days") or 7)
    current_day = date.fromisoformat(day_key)
    posts: list[dict[str, Any]] = []
    next_index = start_index
    mood = _global_diary_mood(profile)
    for ai_name, persona in profile.get("actors", {}).items():
        variants = ai_name_variants(ai_name)
        last_day = _last_activity_day(config["paths"]["db_dir"], variants, day_key)
        if not last_day:
            continue
        dormant_days = (current_day - date.fromisoformat(last_day)).days
        if dormant_days < threshold:
            continue
        packed, next_index = _pack_posts(
            day_key,
            next_index,
            tag=persona.get("tag") or ai_name,
            fragments=_inactive_fragments(persona, dormant_days, mood),
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
    mood = _global_diary_mood(profile)

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
            fragments=_activity_fragments(ai_name, actor, persona, subject_name, mood),
            max_chars=max_chars,
            max_posts=int(profile.get("post_rules", {}).get("max_ai_posts_per_day") or 3),
            kind="activity",
            ai_name=ai_name,
        )
        posts.extend(actor_posts)

    inactive_posts, next_index = _inactive_posts(config, day_key, profile, next_index)
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
        f"- 日記全体の雰囲気: {profile.get('world_settings_ja', {}).get('diary_mood') or '愚痴全開'}",
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
        "diary_mood_ja": profile.get("world_settings_ja", {}).get("diary_mood") or "愚痴全開",
        "max_chars": max_chars,
        "posts": posts,
    }
    posts_path.write_text(json.dumps(posts_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    upsert_diary_posts(connection, day_key, posts)
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
