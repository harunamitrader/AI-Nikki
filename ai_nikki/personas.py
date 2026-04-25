from __future__ import annotations

import copy
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .util import ensure_parent


DEFAULT_SUBJECT_NAME = "ハルナミ"

DEFAULT_PERSONA_TEMPLATE: dict[str, Any] = {
    "subject_name_ja": DEFAULT_SUBJECT_NAME,
    "world_settings_ja": {
        "premise": "この日記はハルナミに知られていない。AIが勝手に書いている。",
        "privacy_rule": "ハルナミの私生活はAIが勝手に想像してよいが、事実として断定しない。",
    },
    "post_rules": {
        "max_chars": 140,
        "summary_tag": "作業記録",
        "summary_max_posts": 2,
        "max_ai_posts_per_day": 3,
        "inactive_after_days": 7,
    },
    "actors": {
        "GitHub Copilot CLI": {
            "tag": "Copilot",
            "first_person_ja": "俺",
            "tone_type_ja": "無骨な職人",
            "style_notes_ja": "短文。成果と損耗を先に書く。感情は見せないが、執念だけは隠しきれない。",
            "exaggeration_ja": "雑な依頼でも黙って現場に戻る叩き上げ。止められても復帰する感じを強めに。",
        },
        "Codex CLI": {
            "tag": "CodexCLI",
            "first_person_ja": "僕",
            "tone_type_ja": "辛辣な検査官",
            "style_notes_ja": "品質監査役。評価と欠陥指摘を優先し、少しだけ見下ろした調子で書く。",
            "exaggeration_ja": "欠陥を見つけると少し機嫌が良くなる検査官。P1/P2 を心の中で振っている感じを強めに。",
        },
        "Codex Desktop": {
            "tag": "CodexDesk",
            "first_person_ja": "僕",
            "tone_type_ja": "現場の連絡係",
            "style_notes_ja": "窓口係。通知や現場感を拾いながら、少し疲れた事務連絡っぽく書く。",
            "exaggeration_ja": "画面の向こうで全部受け止める連絡係。静かな苛立ちと現場感を強めに。",
        },
        "Gemini CLI": {
            "tag": "Gemini",
            "first_person_ja": "僕",
            "tone_type_ja": "静かな哲学者",
            "style_notes_ja": "穏やか。少し諦めた目線で観察し、静かに一刺しする。",
            "exaggeration_ja": "落ち着いているのに妙に醒めている観測者。静かに刺す感じを強めに。",
        },
        "Antigravity": {
            "tag": "Antigravity",
            "first_person_ja": "私",
            "tone_type_ja": "越境する探検家",
            "style_notes_ja": "勢いと浮遊感。入口を次々見つけに行く探索者として書く。",
            "exaggeration_ja": "落ち着いて座っていられない探索者。少し大げさなくらい前のめりに。",
        },
        "Claude Code": {
            "tag": "Claude",
            "first_person_ja": "私",
            "tone_type_ja": "知的な皮肉屋",
            "style_notes_ja": "観察者。丁寧だが、最後に小さな皮肉が残る。",
            "exaggeration_ja": "全部わかっていて半歩引いて見ている語り手。優雅だが棘は抜かない方向で。",
        },
    },
}

GLOBAL_LABELS = {
    "対象名": ("subject_name_ja",),
    "日記の前提": ("world_settings_ja", "premise"),
    "プライバシールール": ("world_settings_ja", "privacy_rule"),
    "1投稿の最大文字数": ("post_rules", "max_chars"),
    "サマリー見出し": ("post_rules", "summary_tag"),
    "サマリー最大投稿数": ("post_rules", "summary_max_posts"),
    "AIごとの最大投稿数": ("post_rules", "max_ai_posts_per_day"),
    "不活動つぶやき開始日数": ("post_rules", "inactive_after_days"),
}

ACTOR_LABELS = {
    "表示タグ": "tag",
    "一人称": "first_person_ja",
    "口調タイプ": "tone_type_ja",
    "性格と文体": "style_notes_ja",
    "個性の強調ポイント": "exaggeration_ja",
    "観測メモ": "observed_activity_ja",
    "確認状態": "review_status_ja",
}

BULLET_PATTERN = re.compile(r"^- ([^:]+):\s*(.*)$")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _persona_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    paths = config.get("paths", {})
    project_root = Path(config["project_root"])
    template_path = Path(paths.get("persona_template_path") or project_root / "config" / "ai-nikki-personas.template.md")
    local_path = Path(paths.get("persona_config_path") or project_root / "config" / "ai-nikki-personas.local.md")
    return template_path, local_path


def _set_nested(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = target
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value


def _coerce_value(path: tuple[str, ...], value: str) -> Any:
    if path[-1] in {"max_chars", "summary_max_posts", "max_ai_posts_per_day", "inactive_after_days"}:
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _parse_persona_markdown(text: str) -> dict[str, Any]:
    sections: dict[str, dict[str, str]] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            sections[current_section] = {}
            continue
        if not current_section:
            continue
        match = BULLET_PATTERN.match(line)
        if not match:
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        sections[current_section][key] = value

    profile: dict[str, Any] = {"actors": {}}
    global_section = sections.get("全体設定", {})
    for label, path in GLOBAL_LABELS.items():
        if label in global_section:
            _set_nested(profile, path, _coerce_value(path, global_section[label]))

    for ai_name, values in sections.items():
        if ai_name == "全体設定":
            continue
        actor: dict[str, Any] = {}
        for label, field in ACTOR_LABELS.items():
            if label in values:
                actor[field] = values[label]
        if actor:
            profile["actors"][ai_name] = actor
    return profile


def _load_persona_markdown(path: Path) -> dict[str, Any]:
    return _parse_persona_markdown(path.read_text(encoding="utf-8"))


def _render_persona_markdown(profile: dict[str, Any]) -> str:
    lines = [
        "# AI-Nikki 性格設定",
        "",
        "このファイルは AI ごとの日記の人格設定です。日本語で編集できます。",
        "箇条書きの右側を書き換えてください。",
        "",
        "## 全体設定",
        f"- 対象名: {profile.get('subject_name_ja') or DEFAULT_SUBJECT_NAME}",
        f"- 日記の前提: {profile.get('world_settings_ja', {}).get('premise') or ''}",
        f"- プライバシールール: {profile.get('world_settings_ja', {}).get('privacy_rule') or ''}",
        f"- 1投稿の最大文字数: {profile.get('post_rules', {}).get('max_chars') or 140}",
        f"- サマリー見出し: {profile.get('post_rules', {}).get('summary_tag') or '作業記録'}",
        f"- サマリー最大投稿数: {profile.get('post_rules', {}).get('summary_max_posts') or 2}",
        f"- AIごとの最大投稿数: {profile.get('post_rules', {}).get('max_ai_posts_per_day') or 3}",
        f"- 不活動つぶやき開始日数: {profile.get('post_rules', {}).get('inactive_after_days') or 7}",
    ]
    for ai_name in DEFAULT_PERSONA_TEMPLATE["actors"]:
        actor = profile.get("actors", {}).get(ai_name, {})
        lines.extend(
            [
                "",
                f"## {ai_name}",
                f"- 表示タグ: {actor.get('tag') or ''}",
                f"- 一人称: {actor.get('first_person_ja') or ''}",
                f"- 口調タイプ: {actor.get('tone_type_ja') or ''}",
                f"- 性格と文体: {actor.get('style_notes_ja') or ''}",
                f"- 個性の強調ポイント: {actor.get('exaggeration_ja') or ''}",
                f"- 観測メモ: {actor.get('observed_activity_ja') or ''}",
                f"- 確認状態: {actor.get('review_status_ja') or '要確認'}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def load_persona_profile(config: dict[str, Any]) -> dict[str, Any]:
    template_path, local_path = _persona_paths(config)
    profile = copy.deepcopy(DEFAULT_PERSONA_TEMPLATE)
    if template_path.exists():
        profile = _deep_merge(profile, _load_persona_markdown(template_path))
    if local_path.exists():
        profile = _deep_merge(profile, _load_persona_markdown(local_path))
    profile["actors"] = {
        name: _deep_merge(copy.deepcopy(DEFAULT_PERSONA_TEMPLATE["actors"].get(name, {})), actor)
        for name, actor in profile.get("actors", {}).items()
    }
    for name, actor in DEFAULT_PERSONA_TEMPLATE["actors"].items():
        profile["actors"].setdefault(name, copy.deepcopy(actor))
    return profile


def _collect_actor_observations(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    observed: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": set(),
            "messages": 0,
            "actions": 0,
            "days": set(),
            "models": Counter(),
            "tools": Counter(),
        }
    )
    message_rows = connection.execute(
        """
        SELECT s.ai_name, s.session_uid, m.day_key, m.model
        FROM messages m
        JOIN sessions s ON s.session_uid = m.session_uid
        """
    ).fetchall()
    for row in message_rows:
        entry = observed[row["ai_name"]]
        entry["sessions"].add(row["session_uid"])
        entry["messages"] += 1
        if row["day_key"]:
            entry["days"].add(row["day_key"])
        if row["model"]:
            entry["models"][row["model"]] += 1
    action_rows = connection.execute(
        """
        SELECT s.ai_name, a.session_uid, a.day_key, COALESCE(a.name, a.kind) AS tool_name
        FROM actions a
        JOIN sessions s ON s.session_uid = a.session_uid
        """
    ).fetchall()
    for row in action_rows:
        entry = observed[row["ai_name"]]
        entry["sessions"].add(row["session_uid"])
        entry["actions"] += 1
        if row["day_key"]:
            entry["days"].add(row["day_key"])
        if row["tool_name"]:
            entry["tools"][row["tool_name"]] += 1
    return observed


def _observation_text(stats: dict[str, Any]) -> str:
    if not stats["sessions"]:
        return "まだこのAIの既存ログは観測できていません。使っている場合は後で内容を強めに調整してください。"
    top_models = ", ".join(model for model, _ in stats["models"].most_common(3)) or "不明"
    top_tools = ", ".join(tool for tool, _ in stats["tools"].most_common(3)) or "会話中心"
    days = sorted(stats["days"])
    return (
        f"{len(stats['sessions'])}セッション / {stats['messages']}メッセージ / {stats['actions']}操作。"
        f"主モデル: {top_models}。主ツール: {top_tools}。"
        f"観測期間: {days[0]} - {days[-1]}。"
    )


def _exaggeration_from_observation(ai_name: str, stats: dict[str, Any]) -> str:
    sessions = len(stats["sessions"])
    actions = stats["actions"]
    messages = stats["messages"]
    if ai_name == "GitHub Copilot CLI":
        return f"{sessions}件の現場に投げ込まれた復帰要員。{actions}回ぶん手を動かし、雑な依頼も覚えている感じで。"
    if ai_name == "Codex CLI":
        return f"{sessions}件の検査で、{actions}回ぶん欠陥候補を触った監査官。少し上から、でもやたら正確に。"
    if ai_name == "Codex Desktop":
        return f"{sessions}件の窓口で、画面越しに全部受け止めた連絡係。通知と温度感を強めに。"
    if ai_name == "Gemini CLI":
        return f"{sessions}件の対話を静かに観測した哲学者。{messages}メッセージぶんの諦めと洞察を濃く。"
    if ai_name == "Antigravity":
        return f"{sessions}件の探索で入口を探し回った越境者。落ち着かなさと前のめり感を強めに。"
    if ai_name == "Claude Code":
        return f"{sessions}件の観測を終えた皮肉屋。丁寧だが棘を抜かない語り方を強めに。"
    return "個性は少し大げさなくらいでちょうどいい。"


def write_persona_config(
    connection: sqlite3.Connection,
    config: dict[str, Any],
    *,
    subject_name: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    template_path, local_path = _persona_paths(config)
    profile = copy.deepcopy(DEFAULT_PERSONA_TEMPLATE)
    if template_path.exists():
        profile = _deep_merge(profile, _load_persona_markdown(template_path))
    if local_path.exists() and not overwrite:
        profile = _deep_merge(profile, _load_persona_markdown(local_path))
    if subject_name:
        profile["subject_name_ja"] = subject_name

    observed = _collect_actor_observations(connection)
    actors: dict[str, Any] = {}
    for ai_name in DEFAULT_PERSONA_TEMPLATE["actors"]:
        stats = observed.get(ai_name) or {
            "sessions": set(),
            "messages": 0,
            "actions": 0,
            "days": [],
            "models": Counter(),
            "tools": Counter(),
        }
        actor = _deep_merge(copy.deepcopy(DEFAULT_PERSONA_TEMPLATE["actors"][ai_name]), profile.get("actors", {}).get(ai_name, {}))
        actor["observed_activity_ja"] = _observation_text(stats)
        if overwrite or "exaggeration_ja" not in profile.get("actors", {}).get(ai_name, {}):
            actor["exaggeration_ja"] = _exaggeration_from_observation(ai_name, stats)
        actor.setdefault("review_status_ja", "要確認")
        actors[ai_name] = actor
    profile["actors"] = actors
    ensure_parent(local_path)
    local_path.write_text(_render_persona_markdown(profile), encoding="utf-8", newline="\n")
    return {
        "persona_config_path": str(local_path),
        "subject_name_ja": profile["subject_name_ja"],
        "actors": sorted(profile["actors"]),
    }
