from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .day_materials import diary_paths
from .util import ensure_parent


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\]]+")
META_TAG_RE = re.compile(r"<(?:environment_context|developer|system|user|assistant|USER_REQUEST|SYSTEM|INSTRUCTIONS)[^>]*>", re.IGNORECASE)


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    if not path.exists():
        errors.append(f"missing file: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid json: {path}: {exc}")
        return {}


def _split_markdown_posts(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*---\s*\n", text.strip()) if part.strip()]


def validate_draft(config: dict[str, Any], day_key: str) -> dict[str, Any]:
    paths = diary_paths(config, day_key)
    errors: list[str] = []
    warnings: list[str] = []
    draft_md = paths["draft_markdown"]
    draft_json = paths["draft_posts_json"]
    validation_path = paths["validation_json"]
    payload = _read_json(draft_json, errors)
    markdown_text = ""
    markdown_posts: list[str] = []
    if not draft_md.exists():
        errors.append(f"missing file: {draft_md}")
    else:
        markdown_text = draft_md.read_text(encoding="utf-8")
        markdown_posts = _split_markdown_posts(markdown_text)
        if not markdown_posts:
            errors.append("draft markdown has no posts")

    posts = payload.get("posts") if isinstance(payload, dict) else None
    if payload and payload.get("day_key") != day_key:
        errors.append(f"json day_key must be {day_key}")
    if not isinstance(posts, list) or not posts:
        errors.append("draft posts json must contain a non-empty posts array")
        posts = []

    day_label = day_key.replace("-", "/")
    for expected_index, post in enumerate(posts, start=1):
        if not isinstance(post, dict):
            errors.append(f"post #{expected_index} must be an object")
            continue
        text = str(post.get("text") or "")
        body = str(post.get("body") or "")
        tag = str(post.get("tag") or "")
        header = f"{day_label} #{expected_index} [{tag}]"
        if post.get("post_index") != expected_index:
            errors.append(f"post #{expected_index} has invalid post_index")
        if not tag:
            errors.append(f"post #{expected_index} has no tag")
        if not text:
            errors.append(f"post #{expected_index} has no text")
        if text and not text.startswith(header + "\n"):
            errors.append(f"post #{expected_index} header must be: {header}")
        if body and text and body not in text:
            warnings.append(f"post #{expected_index} body is not contained in text")
        actual_count = len(text)
        if post.get("char_count") != actual_count:
            errors.append(f"post #{expected_index} char_count must be {actual_count}")
        if actual_count > 140:
            errors.append(f"post #{expected_index} exceeds 140 chars: {actual_count}")
        kind = str(post.get("kind") or "")
        if kind == "activity":
            min_chars = int(post.get("min_chars") or 120)
            if actual_count < min_chars:
                errors.append(f"post #{expected_index} is too short for activity post: {actual_count} chars (min {min_chars})")
        for label, pattern in (("uuid", UUID_RE), ("windows path", WINDOWS_PATH_RE), ("meta tag", META_TAG_RE)):
            if pattern.search(text):
                errors.append(f"post #{expected_index} contains {label}")
        if "..." in text or "…" in text:
            errors.append(f"post #{expected_index} contains an ellipsis-like truncation")

    if markdown_posts and posts and len(markdown_posts) != len(posts):
        errors.append(f"markdown post count {len(markdown_posts)} does not match json post count {len(posts)}")
    for index, md_post in enumerate(markdown_posts, start=1):
        if index <= len(posts) and md_post != str(posts[index - 1].get("text") or "").strip():
            errors.append(f"markdown post #{index} does not match json text")

    result = {
        "day_key": day_key,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "draft_markdown": str(draft_md),
        "draft_posts_json": str(draft_json),
    }
    ensure_parent(validation_path)
    validation_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def publish_diary(config: dict[str, Any], day_key: str, *, force: bool = False) -> dict[str, Any]:
    paths = diary_paths(config, day_key)
    validation = validate_draft(config, day_key)
    if not paths["draft_markdown"].exists() or not paths["draft_posts_json"].exists():
        return {
            "day_key": day_key,
            "published": False,
            "forced": False,
            "validation": validation,
        }
    if not validation["ok"] and not force:
        return {
            "day_key": day_key,
            "published": False,
            "forced": False,
            "validation": validation,
        }
    ensure_parent(paths["published_markdown"])
    ensure_parent(paths["published_posts_json"])
    shutil.copyfile(paths["draft_markdown"], paths["published_markdown"])
    shutil.copyfile(paths["draft_posts_json"], paths["published_posts_json"])
    if force:
        validation["forced"] = True
        paths["validation_json"].write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "day_key": day_key,
        "published": True,
        "forced": force and not validation["ok"],
        "validation": validation,
        "published_markdown": str(paths["published_markdown"]),
        "published_posts_json": str(paths["published_posts_json"]),
    }


def mark_review_needed(config: dict[str, Any], day_key: str, *, attempts: int) -> dict[str, Any]:
    paths = diary_paths(config, day_key)
    validation = validate_draft(config, day_key)
    lines = [
        f"# AI-Nikki review needed {day_key}",
        "",
        f"- Attempts: {attempts}",
        f"- Draft Markdown: `{paths['draft_markdown']}`",
        f"- Draft posts JSON: `{paths['draft_posts_json']}`",
        f"- Validation JSON: `{paths['validation_json']}`",
        "",
        "## Status",
        "",
        "自動検査に合格しなかったため、draft を捨てずにレビュー待ちとして残しました。",
        "",
        "## Errors",
        "",
    ]
    if validation["errors"]:
        lines.extend(f"- {error}" for error in validation["errors"])
    else:
        lines.append("- 検査エラーはありません。")
    if validation["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in validation["warnings"])
    ensure_parent(paths["review_needed"])
    paths["review_needed"].write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "day_key": day_key,
        "review_needed_path": str(paths["review_needed"]),
        "validation": validation,
    }
