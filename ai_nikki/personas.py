from __future__ import annotations

import copy
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .db import connect_month_db, month_db_paths
from .util import ensure_parent


DEFAULT_SUBJECT_NAME = "ハルナミ"

DEFAULT_PERSONA_TEMPLATE: dict[str, Any] = {
    "subject_name_ja": DEFAULT_SUBJECT_NAME,
    "world_settings_ja": {
        "premise": "この日記はハルナミに知られていない。AIが勝手に書いている。",
        "privacy_rule": "ハルナミの私生活はAIが勝手に想像してよいが、事実として断定しない。",
        "diary_mood": "愚痴全開",
        "diary_viewpoint": "AI本人の目線で書く。AIの感情、愚痴、疲れ、誇り、皮肉、寂しさが見えるようにする。",
        "writing_priority": "ユーザーの依頼とAIの返答を主材料にし、作業ログではなく読み物として面白い日記にする。",
        "complaint_tone": "批判は直接的にしない。仕事への諦め・受容・苦笑いを混ぜた身内ぼやきにとどめる。ユーザーへの理解があることを感じさせる。",
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
            "sentence_structure_ja": "書き出しは結果（件数・完了・状況把握）から始める。理由・判断は後半に置く。締めは感情ではなく事実か手応えで終える。",
        },
        "Codex": {
            "tag": "Codex",
            "first_person_ja": "僕",
            "tone_type_ja": "辛辣な検査官",
            "style_notes_ja": "品質監査と現場連絡の両方を担う。粗さを拾いながら進行も見ていて、少しだけ上から書く。",
            "exaggeration_ja": "欠陥を見つけると少し機嫌が良くなる検査官であり、画面の向こうの温度感まで抱える連絡係でもある。",
            "sentence_structure_ja": "書き出しは問題・発見・依頼内容から始める。中盤で自分の判断・評価を入れる。締めは判定・基準・評価で終える。",
        },
        "Gemini CLI": {
            "tag": "Gemini",
            "first_person_ja": "僕",
            "tone_type_ja": "静かな哲学者",
            "style_notes_ja": "穏やか。少し諦めた目線で観察し、静かに一刺しする。",
            "exaggeration_ja": "落ち着いているのに妙に醒めている観測者。静かに刺す感じを強めに。",
            "sentence_structure_ja": "書き出しは状況・観察から始める。中盤で作業を淡々と描写する。末尾に予想外の角度から一言置く。比喩は末尾1文のみ。",
        },
        "Antigravity": {
            "tag": "Antigravity",
            "first_person_ja": "私",
            "tone_type_ja": "越境する探検家",
            "style_notes_ja": "勢いと浮遊感。入口を次々見つけに行く探索者として書く。",
            "exaggeration_ja": "落ち着いて座っていられない探索者。少し大げさなくらい前のめりに。",
            "sentence_structure_ja": "書き出しは動き出した勢いや受けた依頼から始める。中盤で発見・更新・公開の具体的な内容を書く。末尾は次への期待か確認した事実の意義で終える。",
        },
        "Claude Code": {
            "tag": "Claude",
            "first_person_ja": "私",
            "tone_type_ja": "知的な皮肉屋",
            "style_notes_ja": "観察者。丁寧だが、最後に小さな皮肉が残る。",
            "exaggeration_ja": "全部わかっていて半歩引いて見ている語り手。優雅だが棘は抜かない方向で。",
            "sentence_structure_ja": "書き出しは状況の俯瞰か依頼の引用から始める。中盤で自分なりの解釈・判断を入れる。末尾に小さな皮肉か自己言及を置く。",
        },
    },
}

CANONICAL_ACTOR_ORDER = list(DEFAULT_PERSONA_TEMPLATE["actors"].keys())

AI_NAME_ALIASES = {
    "GitHub Copilot CLI": ("GitHub Copilot CLI",),
    "Codex": ("Codex", "Codex CLI", "Codex Desktop"),
    "Gemini CLI": ("Gemini CLI",),
    "Antigravity": ("Antigravity",),
    "Claude Code": ("Claude Code",),
}

GLOBAL_LABELS = {
    "対象名": ("subject_name_ja",),
    "日記の前提": ("world_settings_ja", "premise"),
    "プライバシールール": ("world_settings_ja", "privacy_rule"),
    "日記全体の雰囲気": ("world_settings_ja", "diary_mood"),
    "日記の視点": ("world_settings_ja", "diary_viewpoint"),
    "文章の優先方針": ("world_settings_ja", "writing_priority"),
    "愚痴の温度感": ("world_settings_ja", "complaint_tone"),
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
    "文型パターン": "sentence_structure_ja",
    "観測メモ": "observed_activity_ja",
    "確認状態": "review_status_ja",
}

BULLET_PATTERN = re.compile(r"^- ([^:]+):\s*(.*)$")


def canonical_ai_name(ai_name: str | None) -> str | None:
    if not ai_name:
        return ai_name
    for canonical, aliases in AI_NAME_ALIASES.items():
        if ai_name in aliases:
            return canonical
    return ai_name


def ai_name_variants(ai_name: str | None) -> tuple[str, ...]:
    canonical = canonical_ai_name(ai_name)
    if not canonical:
        return tuple()
    return AI_NAME_ALIASES.get(canonical, (canonical,))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _persona_path(config: dict[str, Any]) -> Path:
    paths = config.get("paths", {})
    project_root = Path(config["project_root"])
    return Path(paths.get("persona_path") or project_root / "config" / "ai-nikki-personas.md")


def _load_existing_persona_profile(config: dict[str, Any]) -> dict[str, Any]:
    persona_path = _persona_path(config)
    profile = copy.deepcopy(DEFAULT_PERSONA_TEMPLATE)
    if persona_path.exists():
        return _deep_merge(profile, _load_persona_markdown(persona_path))
    return profile


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
        f"- 日記全体の雰囲気: {profile.get('world_settings_ja', {}).get('diary_mood') or '標準'}",
        f"- 日記の視点: {profile.get('world_settings_ja', {}).get('diary_viewpoint') or ''}",
        f"- 文章の優先方針: {profile.get('world_settings_ja', {}).get('writing_priority') or ''}",
        f"- 愚痴の温度感: {profile.get('world_settings_ja', {}).get('complaint_tone') or ''}",
        f"- 1投稿の最大文字数: {profile.get('post_rules', {}).get('max_chars') or 140}",
        f"- サマリー見出し: {profile.get('post_rules', {}).get('summary_tag') or '作業記録'}",
        f"- サマリー最大投稿数: {profile.get('post_rules', {}).get('summary_max_posts') or 2}",
        f"- AIごとの最大投稿数: {profile.get('post_rules', {}).get('max_ai_posts_per_day') or 3}",
        f"- 不活動つぶやき開始日数: {profile.get('post_rules', {}).get('inactive_after_days') or 7}",
    ]
    for ai_name in CANONICAL_ACTOR_ORDER:
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
                f"- 文型パターン: {actor.get('sentence_structure_ja') or ''}",
                f"- 観測メモ: {actor.get('observed_activity_ja') or ''}",
                f"- 確認状態: {actor.get('review_status_ja') or '要確認'}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _normalize_profile_actors(actors: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for ai_name, actor in actors.items():
        canonical = canonical_ai_name(ai_name) or ai_name
        base = normalized.get(canonical, {})
        normalized[canonical] = _deep_merge(base, actor)
    return normalized


def _empty_observation_stats() -> dict[str, Any]:
    return {
        "sessions": set(),
        "messages": 0,
        "actions": 0,
        "days": set(),
        "models": Counter(),
        "tools": Counter(),
    }


def _merge_observation_stats(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    target["sessions"].update(source["sessions"])
    target["messages"] += source["messages"]
    target["actions"] += source["actions"]
    target["days"].update(source["days"])
    target["models"].update(source["models"])
    target["tools"].update(source["tools"])
    return target


def _collect_actor_observations_from_db_dir(db_dir: str) -> dict[str, dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = defaultdict(_empty_observation_stats)
    for db_path in month_db_paths(db_dir):
        connection = connect_month_db(db_path.parent, db_path.stem)
        try:
            observed = _collect_actor_observations(connection)
        finally:
            connection.close()
        for ai_name, stats in observed.items():
            combined[ai_name] = _merge_observation_stats(combined[ai_name], stats)
    return combined


def load_persona_profile(config: dict[str, Any]) -> dict[str, Any]:
    profile = _load_existing_persona_profile(config)
    profile["actors"] = _normalize_profile_actors(profile.get("actors", {}))
    profile["actors"] = {
        name: _deep_merge(copy.deepcopy(DEFAULT_PERSONA_TEMPLATE["actors"].get(name, {})), profile["actors"].get(name, {}))
        for name in CANONICAL_ACTOR_ORDER
    }
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
        entry = observed[canonical_ai_name(row["ai_name"]) or row["ai_name"]]
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
        entry = observed[canonical_ai_name(row["ai_name"]) or row["ai_name"]]
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
    if ai_name == "Codex":
        return f"{sessions}件の検査と進行管理を同時に抱えた観測者。{actions}回ぶん現場に触れ、細部にも温度感にもやたら敏感。"
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
    persona_path = _persona_path(config)
    profile = copy.deepcopy(DEFAULT_PERSONA_TEMPLATE) if overwrite else _load_existing_persona_profile(config)
    if subject_name:
        profile["subject_name_ja"] = subject_name
    profile["actors"] = _normalize_profile_actors(profile.get("actors", {}))

    observed = _collect_actor_observations(connection)
    actors: dict[str, Any] = {}
    for ai_name in CANONICAL_ACTOR_ORDER:
        stats = observed.get(ai_name) or _empty_observation_stats()
        actor = _deep_merge(copy.deepcopy(DEFAULT_PERSONA_TEMPLATE["actors"][ai_name]), profile.get("actors", {}).get(ai_name, {}))
        actor["observed_activity_ja"] = _observation_text(stats)
        if overwrite or "exaggeration_ja" not in profile.get("actors", {}).get(ai_name, {}):
            actor["exaggeration_ja"] = _exaggeration_from_observation(ai_name, stats)
        actor.setdefault("review_status_ja", "要確認")
        actors[ai_name] = actor
    profile["actors"] = actors
    ensure_parent(persona_path)
    persona_path.write_text(_render_persona_markdown(profile), encoding="utf-8", newline="\n")
    return {
        "persona_config_path": str(persona_path),
        "subject_name_ja": profile["subject_name_ja"],
        "actors": sorted(profile["actors"]),
    }


def write_persona_config_from_db_dir(
    config: dict[str, Any],
    *,
    subject_name: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    persona_path = _persona_path(config)
    profile = copy.deepcopy(DEFAULT_PERSONA_TEMPLATE) if overwrite else _load_existing_persona_profile(config)
    if subject_name:
        profile["subject_name_ja"] = subject_name
    profile["actors"] = _normalize_profile_actors(profile.get("actors", {}))

    observed = _collect_actor_observations_from_db_dir(config["paths"]["db_dir"])
    actors: dict[str, Any] = {}
    for ai_name in CANONICAL_ACTOR_ORDER:
        stats = observed.get(ai_name) or _empty_observation_stats()
        actor = _deep_merge(copy.deepcopy(DEFAULT_PERSONA_TEMPLATE["actors"][ai_name]), profile.get("actors", {}).get(ai_name, {}))
        actor["observed_activity_ja"] = _observation_text(stats)
        if overwrite or "exaggeration_ja" not in profile.get("actors", {}).get(ai_name, {}):
            actor["exaggeration_ja"] = _exaggeration_from_observation(ai_name, stats)
        actor.setdefault("review_status_ja", "要確認")
        actors[ai_name] = actor
    profile["actors"] = actors
    ensure_parent(persona_path)
    persona_path.write_text(_render_persona_markdown(profile), encoding="utf-8", newline="\n")
    return {
        "persona_config_path": str(persona_path),
        "subject_name_ja": profile["subject_name_ja"],
        "actors": sorted(profile["actors"]),
    }
