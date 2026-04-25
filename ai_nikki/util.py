from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9), "JST")
UTC = timezone.utc
MODEL_PATTERN = re.compile(r"\b(?:gpt-[A-Za-z0-9._-]+|gemini-[A-Za-z0-9._-]+|claude-[A-Za-z0-9._-]+|o[1-9][A-Za-z0-9._-]*)\b")
SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b(?:token|secret|password|api[_-]?key)\s*[:=]\s*[^\s\"']+\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{20,}\b"),
]


def now_utc_iso() -> str:
    return to_iso(datetime.now(tz=UTC))


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def day_key_for_timestamp(value: Any, cutoff_hour: int = 3) -> str | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    local = parsed.astimezone(JST)
    if local.hour < cutoff_hour:
        local = local - timedelta(days=1)
    return local.date().isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def stable_id(*parts: Any) -> str:
    joined = "||".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_text_best_effort(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def truncate_text(text: str, limit: int = 240) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def redact_text(text: str) -> str:
    value = str(text or "")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [extract_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        preferred_keys = (
            "text",
            "content",
            "message",
            "output",
            "response",
            "summary",
            "display",
            "query",
            "command",
            "body",
            "pattern",
            "title",
        )
        parts: list[str] = []
        for key in preferred_keys:
            if key in value:
                part = extract_text(value[key])
                if part:
                    parts.append(part)
        if parts:
            return "\n".join(parts)
        nested = [extract_text(item) for item in value.values()]
        return "\n".join(part for part in nested if part)
    return str(value)


def extract_claude_text(content: Any) -> str:
    if not isinstance(content, list):
        return extract_text(content)
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if text:
                parts.append(str(text))
    return "\n".join(parts) if parts else extract_text(content)


def find_model_string(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("model", "newModel", "selectedModel", "model_name", "model_slug"):
            candidate = value.get(key)
            if isinstance(candidate, str) and MODEL_PATTERN.search(candidate):
                return MODEL_PATTERN.search(candidate).group(0)
        for nested in value.values():
            found = find_model_string(nested)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = find_model_string(item)
            if found:
                return found
        return None
    if isinstance(value, str):
        match = MODEL_PATTERN.search(value)
        return match.group(0) if match else None
    return None

