from __future__ import annotations

import glob
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import (
    day_key_for_timestamp,
    extract_claude_text,
    extract_text,
    find_model_string,
    json_dumps,
    now_utc_iso,
    parse_timestamp,
    read_text_best_effort,
    stable_id,
    truncate_text,
)

EXTRACTOR_VERSION = "0.1.0"


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    display_name: str
    source_type: str


SOURCE_DEFINITIONS = {
    "copilot_cli": SourceDefinition("copilot_cli", "GitHub Copilot CLI", "cli"),
    "codex_cli": SourceDefinition("codex_cli", "Codex CLI", "cli"),
    "codex_desktop_live_log": SourceDefinition("codex_desktop_live_log", "Codex Desktop Live Log", "desktop"),
    "codex_desktop_bridge": SourceDefinition("codex_desktop_bridge", "Codex Desktop Bridge", "desktop"),
    "gemini_cli": SourceDefinition("gemini_cli", "Gemini CLI", "cli"),
    "antigravity": SourceDefinition("antigravity", "Antigravity", "desktop"),
    "claude_code_history": SourceDefinition("claude_code_history", "Claude Code History", "cli"),
    "claude_code_projects": SourceDefinition("claude_code_projects", "Claude Code", "cli"),
}


def discover_files(patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in glob.glob(pattern, recursive=True):
            candidate = Path(match)
            key = str(candidate.resolve())
            if candidate.is_file() and key not in seen:
                seen.add(key)
                found.append(candidate)
    return sorted(found)


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _make_session(
    source_id: str,
    source_session_id: str,
    *,
    ai_name: str,
    model: str | None,
    started_at: str | None,
    ended_at: str | None,
    workspace_key: str | None,
    workspace_path: str | None,
    title: str | None,
    thread_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    raw_payload: Any | None = None,
) -> dict[str, Any]:
    return {
        "session_uid": stable_id(source_id, source_session_id),
        "source_id": source_id,
        "source_session_id": source_session_id,
        "thread_id": thread_id,
        "workspace_key": workspace_key,
        "workspace_path": workspace_path,
        "title": title,
        "ai_name": ai_name,
        "model": model,
        "started_at": started_at,
        "ended_at": ended_at,
        "metadata_json": json_dumps(metadata or {}),
        "raw_payload": json_dumps(raw_payload or {}),
        "updated_at": now_utc_iso(),
    }


def _make_message(
    source_id: str,
    source_session_id: str,
    source_message_id: str,
    *,
    seq: int,
    ts: str | None,
    role: str,
    content_text: str,
    model: str | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    tokens_total: int | None = None,
    is_final: int = 1,
    metadata: dict[str, Any] | None = None,
    raw_payload: Any | None = None,
) -> dict[str, Any]:
    return {
        "message_uid": stable_id(source_id, source_session_id, source_message_id),
        "session_uid": stable_id(source_id, source_session_id),
        "source_id": source_id,
        "source_message_id": source_message_id,
        "ts": ts,
        "day_key": day_key_for_timestamp(ts),
        "seq": seq,
        "role": role,
        "model": model,
        "content_text": content_text,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "tokens_total": tokens_total,
        "is_final": is_final,
        "metadata_json": json_dumps(metadata or {}),
        "raw_payload": json_dumps(raw_payload or {}),
    }


def _make_action(
    source_id: str,
    source_session_id: str,
    source_action_id: str,
    *,
    seq: int,
    ts: str | None,
    kind: str,
    name: str | None,
    status: str | None,
    summary: str | None,
    arguments: Any | None = None,
    result: Any | None = None,
    metadata: dict[str, Any] | None = None,
    raw_payload: Any | None = None,
) -> dict[str, Any]:
    return {
        "action_uid": stable_id(source_id, source_session_id, source_action_id),
        "session_uid": stable_id(source_id, source_session_id),
        "source_id": source_id,
        "source_action_id": source_action_id,
        "ts": ts,
        "day_key": day_key_for_timestamp(ts),
        "seq": seq,
        "kind": kind,
        "name": name,
        "status": status,
        "summary": summary,
        "arguments_json": json_dumps(arguments) if arguments is not None else None,
        "result_json": json_dumps(result) if result is not None else None,
        "metadata_json": json_dumps(metadata or {}),
        "raw_payload": json_dumps(raw_payload or {}),
    }


def parse_file(source_id: str, path: Path, config: dict[str, Any], shared_state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if source_id == "copilot_cli":
        return parse_copilot_events(path)
    if source_id == "codex_cli":
        return parse_codex_cli_session(path)
    if source_id == "codex_desktop_bridge":
        return parse_codex_desktop_bridge(path, shared_state.get("codex_live_log"))
    if source_id == "codex_desktop_live_log":
        shared_state["codex_live_log"] = parse_codex_live_log(path)
        return {"sessions": [], "messages": [], "actions": []}
    if source_id == "gemini_cli":
        return parse_gemini_file(path)
    if source_id == "antigravity":
        return parse_antigravity_overview(path)
    if source_id == "claude_code_history":
        return parse_claude_history(path)
    if source_id == "claude_code_projects":
        return parse_claude_project(path)
    raise ValueError(f"Unsupported source: {source_id}")


def parse_copilot_events(path: Path) -> dict[str, list[dict[str, Any]]]:
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    session_id = path.parent.name
    workspace_path = None
    model = None
    started_at = None
    ended_at = None
    tool_actions: dict[str, dict[str, Any]] = {}
    title = None
    with path.open("r", encoding="utf-8") as handle:
        for seq, line in enumerate(handle):
            obj = json.loads(line)
            event_type = obj.get("type")
            data = obj.get("data", {})
            ts = obj.get("timestamp")
            ended_at = ts or ended_at
            if event_type == "session.start":
                session_id = data.get("sessionId") or session_id
                started_at = data.get("startTime") or ts
                workspace_path = ((data.get("context") or {}).get("cwd")) or workspace_path
                model = data.get("selectedModel") or model
                title = Path(workspace_path).name if workspace_path else title
            elif event_type == "session.model_change":
                model = data.get("newModel") or model
            elif event_type == "user.message":
                content = data.get("transformedContent") or data.get("content") or ""
                messages.append(
                    _make_message(
                        "copilot_cli",
                        session_id,
                        obj.get("id") or f"user:{seq}",
                        seq=seq,
                        ts=ts,
                        role="user",
                        content_text=str(content),
                        model=model,
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
            elif event_type == "assistant.message":
                content = data.get("content") or ""
                messages.append(
                    _make_message(
                        "copilot_cli",
                        session_id,
                        data.get("messageId") or obj.get("id") or f"assistant:{seq}",
                        seq=seq,
                        ts=ts,
                        role="assistant",
                        content_text=str(content),
                        model=model,
                        tokens_output=data.get("outputTokens"),
                        is_final=1 if data.get("phase") != "thinking" else 0,
                        metadata={"path": str(path), "phase": data.get("phase")},
                        raw_payload=obj,
                    )
                )
                for index, tool_request in enumerate(data.get("toolRequests") or []):
                    actions.append(
                        _make_action(
                            "copilot_cli",
                            session_id,
                            f"planned:{tool_request.get('toolCallId') or seq}:{index}",
                            seq=seq,
                            ts=ts,
                            kind="tool_request",
                            name=tool_request.get("name"),
                            status="planned",
                            summary=tool_request.get("intentionSummary"),
                            arguments=tool_request.get("arguments"),
                            metadata={"path": str(path)},
                            raw_payload=tool_request,
                        )
                    )
            elif event_type == "tool.execution_start":
                tool_call_id = data.get("toolCallId") or f"tool:{seq}"
                tool_actions[tool_call_id] = _make_action(
                    "copilot_cli",
                    session_id,
                    tool_call_id,
                    seq=seq,
                    ts=ts,
                    kind="tool",
                    name=data.get("toolName"),
                    status="started",
                    summary=None,
                    arguments=data.get("arguments"),
                    metadata={"path": str(path)},
                    raw_payload=obj,
                )
            elif event_type == "tool.execution_complete":
                tool_call_id = data.get("toolCallId") or f"tool:{seq}"
                existing = tool_actions.get(tool_call_id)
                action = _make_action(
                    "copilot_cli",
                    session_id,
                    tool_call_id,
                    seq=existing["seq"] if existing else seq,
                    ts=ts or (existing["ts"] if existing else None),
                    kind="tool",
                    name=(existing["name"] if existing else None) or ((data.get("toolTelemetry") or {}).get("toolName")),
                    status="completed" if data.get("success") else "failed",
                    summary=truncate_text(extract_text(data.get("result")), 400),
                    arguments=json.loads(existing["arguments_json"]) if existing and existing.get("arguments_json") else None,
                    result=data.get("result"),
                    metadata={"path": str(path), "model": data.get("model")},
                    raw_payload=obj,
                )
                tool_actions[tool_call_id] = action
            elif event_type == "session.plan_changed":
                actions.append(
                    _make_action(
                        "copilot_cli",
                        session_id,
                        obj.get("id") or f"plan:{seq}",
                        seq=seq,
                        ts=ts,
                        kind="plan",
                        name="session.plan_changed",
                        status=data.get("operation"),
                        summary=data.get("operation"),
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
    actions.extend(sorted(tool_actions.values(), key=lambda item: item["seq"]))
    sessions.append(
        _make_session(
            "copilot_cli",
            session_id,
            ai_name="GitHub Copilot CLI",
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            workspace_key=workspace_path,
            workspace_path=workspace_path,
            title=title,
            metadata={"path": str(path)},
            raw_payload={"path": str(path)},
        )
    )
    return {"sessions": sessions, "messages": messages, "actions": actions}


def parse_codex_cli_session(path: Path) -> dict[str, list[dict[str, Any]]]:
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    session_id = path.stem
    thread_id = None
    workspace_path = None
    model = None
    started_at = None
    ended_at = None
    with path.open("r", encoding="utf-8") as handle:
        for seq, line in enumerate(handle):
            obj = json.loads(line)
            payload = obj.get("payload") or {}
            record_type = obj.get("type")
            ts = obj.get("timestamp")
            ended_at = ts or ended_at
            if record_type == "session_meta":
                session_id = payload.get("id") or session_id
                thread_id = payload.get("id") or thread_id
                workspace_path = payload.get("cwd") or workspace_path
                model = find_model_string(payload) or model
                started_at = payload.get("timestamp") or ts
            elif record_type == "response_item" and isinstance(payload, dict) and payload.get("role"):
                content = extract_text(payload.get("content"))
                if content:
                    messages.append(
                        _make_message(
                            "codex_cli",
                            session_id,
                            f"line:{seq}",
                            seq=seq,
                            ts=ts,
                            role=payload.get("role"),
                            content_text=content,
                            model=model,
                            metadata={"path": str(path), "record_type": record_type},
                            raw_payload=obj,
                        )
                    )
            else:
                payload_type = payload.get("type") if isinstance(payload, dict) else None
                summary = truncate_text(extract_text(payload), 400) if payload else None
                actions.append(
                    _make_action(
                        "codex_cli",
                        session_id,
                        f"line:{seq}",
                        seq=seq,
                        ts=ts,
                        kind=record_type,
                        name=payload_type or record_type,
                        status=payload.get("status") if isinstance(payload, dict) else None,
                        summary=summary,
                        arguments=payload.get("args") if isinstance(payload, dict) else None,
                        result=payload.get("result") if isinstance(payload, dict) else payload,
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
    sessions.append(
        _make_session(
            "codex_cli",
            session_id,
            ai_name="Codex CLI",
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            workspace_key=workspace_path,
            workspace_path=workspace_path,
            title=path.stem,
            thread_id=thread_id,
            metadata={"path": str(path)},
            raw_payload={"path": str(path)},
        )
    )
    return {"sessions": sessions, "messages": messages, "actions": actions}


def parse_codex_live_log(path: Path) -> dict[str, dict[str, Any]]:
    thread_map: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    line_pattern = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<body>.*)$")
    with path.open("r", encoding="utf-8") as handle:
        for index, raw_line in enumerate(handle):
            line = raw_line.rstrip("\n")
            match = line_pattern.match(line)
            if not match:
                continue
            ts = match.group("ts")
            body = match.group("body")
            if "===== Codex run" in body and "started" in body:
                current = {"ts": ts, "cwd": None, "model": None, "thread_id": None, "actions": []}
                continue
            if current is None:
                continue
            if body.startswith("cwd: "):
                current["cwd"] = body[5:].strip()
            elif body.startswith("model: "):
                current["model"] = body[7:].strip()
            elif body.startswith("threadId: "):
                value = body[10:].strip()
                if value != "(new thread)":
                    current["thread_id"] = value
            elif body.startswith("[stdout] "):
                payload_text = body[9:].strip()
                try:
                    payload = json.loads(payload_text)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    if payload.get("type") == "thread.started":
                        current["thread_id"] = payload.get("thread_id") or current.get("thread_id")
                    elif payload.get("type") == "item.completed":
                        item = payload.get("item") or {}
                        current["actions"].append(
                            {
                                "ts": ts,
                                "seq": index,
                                "kind": item.get("type") or "stdout_item",
                                "name": item.get("type"),
                                "status": item.get("status") or "completed",
                                "summary": truncate_text(extract_text(item), 400),
                                "arguments": {"command": item.get("command"), "query": item.get("query")},
                                "result": item,
                            }
                        )
            thread_id = current.get("thread_id")
            if thread_id:
                entry = thread_map.setdefault(thread_id, {"cwd": None, "model": None, "actions": []})
                if current.get("cwd"):
                    entry["cwd"] = current["cwd"]
                if current.get("model"):
                    entry["model"] = current["model"]
                if current.get("actions"):
                    entry["actions"] = current["actions"]
    return thread_map


def parse_codex_desktop_bridge(path: Path, live_log_state: dict[str, dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    live_log_state = live_log_state or {}
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    session_rows = connection.execute("SELECT * FROM sessions ORDER BY created_at").fetchall()
    thread_to_session: dict[str, str] = {}
    for session_row in session_rows:
        row = dict(session_row)
        thread_id = row.get("codex_thread_id")
        session_id = row["id"]
        supplemental = live_log_state.get(thread_id or "", {})
        workspace_path = supplemental.get("cwd")
        model = row.get("model") or supplemental.get("model")
        sessions.append(
            _make_session(
                "codex_desktop_bridge",
                session_id,
                ai_name="Codex Desktop",
                model=model,
                started_at=row.get("created_at"),
                ended_at=row.get("updated_at"),
                workspace_key=workspace_path or thread_id,
                workspace_path=workspace_path,
                title=row.get("title"),
                thread_id=thread_id,
                metadata={"path": str(path), "status": row.get("status")},
                raw_payload=row,
            )
        )
        if thread_id:
            thread_to_session[thread_id] = session_id
    event_rows = connection.execute("SELECT * FROM session_events ORDER BY created_at, id").fetchall()
    for seq, event_row in enumerate(event_rows):
        row = dict(event_row)
        session_id = row["session_id"]
        payload = json.loads(row.get("payload_json") or "{}")
        event_type = row.get("event_type")
        ts = row.get("created_at")
        if event_type == "message.user":
            messages.append(
                _make_message(
                    "codex_desktop_bridge",
                    session_id,
                    row["id"],
                    seq=seq,
                    ts=ts,
                    role="user",
                    content_text=extract_text(payload.get("text")),
                    metadata={"path": str(path), "source": row.get("source")},
                    raw_payload=row,
                )
            )
        elif event_type == "message.assistant":
            messages.append(
                _make_message(
                    "codex_desktop_bridge",
                    session_id,
                    row["id"],
                    seq=seq,
                    ts=ts,
                    role="assistant",
                    content_text=extract_text(payload.get("text")),
                    model=None,
                    is_final=1 if payload.get("isFinal", True) else 0,
                    metadata={"path": str(path), "source": row.get("source")},
                    raw_payload=row,
                )
            )
        else:
            actions.append(
                _make_action(
                    "codex_desktop_bridge",
                    session_id,
                    row["id"],
                    seq=seq,
                    ts=ts,
                    kind=event_type or "event",
                    name=event_type,
                    status=payload.get("status"),
                    summary=truncate_text(extract_text(payload), 400),
                    arguments=payload,
                    result=payload,
                    metadata={"path": str(path), "source": row.get("source")},
                    raw_payload=row,
                )
            )
    for thread_id, data in live_log_state.items():
        session_id = thread_to_session.get(thread_id)
        if not session_id:
            continue
        for index, action in enumerate(data.get("actions", [])):
            actions.append(
                _make_action(
                    "codex_desktop_bridge",
                    session_id,
                    f"live-log:{thread_id}:{index}",
                    seq=100_000 + index,
                    ts=action.get("ts"),
                    kind=action.get("kind") or "live_log",
                    name=action.get("name"),
                    status=action.get("status"),
                    summary=action.get("summary"),
                    arguments=action.get("arguments"),
                    result=action.get("result"),
                    metadata={"path": str(path), "thread_id": thread_id, "source": "live_log"},
                    raw_payload=action,
                )
            )
    connection.close()
    return {"sessions": sessions, "messages": messages, "actions": actions}


def parse_gemini_file(path: Path) -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return parse_gemini_logs_json(path, data)
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    session_id = data.get("sessionId") or path.stem
    workspace_path = str(path.parent.parent.parent)
    workspace_key = path.parent.parent.name
    started_at = data.get("startTime")
    ended_at = data.get("lastUpdated")
    model = None
    for seq, message in enumerate(data.get("messages") or []):
        message_type = str(message.get("type") or "").lower()
        role = "assistant" if message_type == "gemini" else message_type or "unknown"
        content_value = message.get("content")
        content_text = extract_text(content_value)
        if role == "assistant":
            model = message.get("model") or model
        messages.append(
            _make_message(
                "gemini_cli",
                session_id,
                message.get("id") or f"message:{seq}",
                seq=seq,
                ts=message.get("timestamp"),
                role=role,
                content_text=content_text,
                model=message.get("model") or model,
                tokens_input=((message.get("tokens") or {}).get("input")),
                tokens_output=((message.get("tokens") or {}).get("output")),
                tokens_total=((message.get("tokens") or {}).get("total")),
                metadata={"path": str(path)},
                raw_payload=message,
            )
        )
        for action_index, tool_call in enumerate(message.get("toolCalls") or []):
            actions.append(
                _make_action(
                    "gemini_cli",
                    session_id,
                    tool_call.get("id") or f"tool:{seq}:{action_index}",
                    seq=seq * 100 + action_index,
                    ts=tool_call.get("timestamp") or message.get("timestamp"),
                    kind="tool",
                    name=tool_call.get("name"),
                    status=tool_call.get("status"),
                    summary=tool_call.get("description") or truncate_text(extract_text(tool_call.get("result")), 400),
                    arguments=tool_call.get("args"),
                    result=tool_call.get("result"),
                    metadata={"path": str(path), "display_name": tool_call.get("displayName")},
                    raw_payload=tool_call,
                )
            )
    sessions.append(
        _make_session(
            "gemini_cli",
            session_id,
            ai_name="Gemini CLI",
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            workspace_key=workspace_key,
            workspace_path=workspace_path,
            title=workspace_key,
            metadata={"path": str(path), "project_hash": data.get("projectHash")},
            raw_payload={"path": str(path)},
        )
    )
    return {"sessions": sessions, "messages": messages, "actions": actions}


def parse_gemini_logs_json(path: Path, entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    session_id = entries[0].get("sessionId") if entries else path.stem
    started_at = entries[0].get("timestamp") if entries else None
    ended_at = entries[-1].get("timestamp") if entries else None
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for seq, entry in enumerate(entries):
        role = "assistant" if entry.get("type") == "gemini" else entry.get("type") or "user"
        messages.append(
            _make_message(
                "gemini_cli",
                session_id,
                f"logs:{entry.get('messageId', seq)}",
                seq=seq,
                ts=entry.get("timestamp"),
                role=role,
                content_text=extract_text(entry.get("message")),
                metadata={"path": str(path)},
                raw_payload=entry,
            )
        )
    sessions = [
        _make_session(
            "gemini_cli",
            session_id,
            ai_name="Gemini CLI",
            model=None,
            started_at=started_at,
            ended_at=ended_at,
            workspace_key=path.parent.name,
            workspace_path=str(path.parent),
            title=path.parent.name,
            metadata={"path": str(path)},
            raw_payload={"path": str(path)},
        )
    ]
    return {"sessions": sessions, "messages": messages, "actions": actions}


def parse_antigravity_overview(path: Path) -> dict[str, list[dict[str, Any]]]:
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    session_id = path.parents[2].name
    base_dir = path.parents[2]
    conversation_pb = path.parents[4] / "conversations" / f"{session_id}.pb"
    started_at = None
    ended_at = None
    model = None
    with path.open("r", encoding="utf-8") as handle:
        for seq, line in enumerate(handle):
            obj = json.loads(line)
            ts = obj.get("created_at")
            started_at = started_at or ts
            ended_at = ts or ended_at
            record_type = obj.get("type")
            if record_type == "USER_INPUT":
                messages.append(
                    _make_message(
                        "antigravity",
                        session_id,
                        f"step:{obj.get('step_index', seq)}",
                        seq=seq,
                        ts=ts,
                        role="user",
                        content_text=extract_text(obj.get("content")),
                        metadata={"path": str(path), "source": obj.get("source")},
                        raw_payload=obj,
                    )
                )
            elif record_type == "PLANNER_RESPONSE" and obj.get("content"):
                model = find_model_string(obj) or model
                messages.append(
                    _make_message(
                        "antigravity",
                        session_id,
                        f"step:{obj.get('step_index', seq)}",
                        seq=seq,
                        ts=ts,
                        role="assistant",
                        content_text=extract_text(obj.get("content")),
                        model=model,
                        metadata={"path": str(path), "source": obj.get("source")},
                        raw_payload=obj,
                    )
                )
            for index, tool_call in enumerate(obj.get("tool_calls") or []):
                actions.append(
                    _make_action(
                        "antigravity",
                        session_id,
                        f"step:{obj.get('step_index', seq)}:tool:{index}",
                        seq=seq * 100 + index,
                        ts=ts,
                        kind="tool",
                        name=tool_call.get("name"),
                        status=obj.get("status"),
                        summary=tool_call.get("args", {}).get("toolAction") or tool_call.get("args", {}).get("toolSummary"),
                        arguments=tool_call.get("args"),
                        metadata={"path": str(path), "source": obj.get("source")},
                        raw_payload=tool_call,
                    )
                )
    sessions.append(
        _make_session(
            "antigravity",
            session_id,
            ai_name="Antigravity",
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            workspace_key=session_id,
            workspace_path=str(base_dir),
            title=session_id,
            metadata={"path": str(path), "conversation_pb": str(conversation_pb) if conversation_pb.exists() else None},
            raw_payload={"path": str(path)},
        )
    )
    return {"sessions": sessions, "messages": messages, "actions": actions}


def parse_claude_history(path: Path) -> dict[str, list[dict[str, Any]]]:
    sessions: dict[str, dict[str, Any]] = {}
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for seq, line in enumerate(handle):
            obj = json.loads(line)
            session_id = obj.get("sessionId") or "history"
            project = obj.get("project")
            sessions.setdefault(
                session_id,
                _make_session(
                    "claude_code_history",
                    session_id,
                    ai_name="Claude Code",
                    model=None,
                    started_at=None,
                    ended_at=None,
                    workspace_key=project,
                    workspace_path=project,
                    title=Path(project).name if project else session_id,
                    metadata={"path": str(path)},
                    raw_payload={"path": str(path)},
                ),
            )
            messages.append(
                _make_message(
                    "claude_code_history",
                    session_id,
                    f"history:{seq}",
                    seq=seq,
                    ts=parse_timestamp(obj.get("timestamp")).isoformat().replace("+00:00", "Z") if parse_timestamp(obj.get("timestamp")) else None,
                    role="user",
                    content_text=extract_text(obj.get("display")),
                    metadata={"path": str(path), "project": project},
                    raw_payload=obj,
                )
            )
    return {"sessions": list(sessions.values()), "messages": messages, "actions": actions}


def parse_claude_project(path: Path) -> dict[str, list[dict[str, Any]]]:
    sessions: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    session_id = path.stem
    workspace_path = None
    started_at = None
    ended_at = None
    model = None
    with path.open("r", encoding="utf-8") as handle:
        for seq, line in enumerate(handle):
            obj = json.loads(line)
            ts = obj.get("timestamp")
            started_at = started_at or ts
            ended_at = ts or ended_at
            session_id = obj.get("sessionId") or session_id
            workspace_path = obj.get("cwd") or workspace_path
            entry_type = obj.get("type")
            if entry_type == "user":
                messages.append(
                    _make_message(
                        "claude_code_projects",
                        session_id,
                        obj.get("uuid") or f"user:{seq}",
                        seq=seq,
                        ts=ts,
                        role="user",
                        content_text=extract_text((obj.get("message") or {}).get("content")),
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
            elif entry_type == "assistant":
                assistant_message = obj.get("message") or {}
                model = assistant_message.get("model") or model
                messages.append(
                    _make_message(
                        "claude_code_projects",
                        session_id,
                        obj.get("uuid") or f"assistant:{seq}",
                        seq=seq,
                        ts=ts,
                        role="assistant",
                        content_text=extract_claude_text(assistant_message.get("content")),
                        model=model,
                        tokens_input=((assistant_message.get("usage") or {}).get("input_tokens")),
                        tokens_output=((assistant_message.get("usage") or {}).get("output_tokens")),
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
            elif entry_type == "attachment":
                attachment = obj.get("attachment") or {}
                actions.append(
                    _make_action(
                        "claude_code_projects",
                        session_id,
                        obj.get("uuid") or f"attachment:{seq}",
                        seq=seq,
                        ts=ts,
                        kind="attachment",
                        name=attachment.get("type"),
                        status="attached",
                        summary=truncate_text(extract_text(attachment), 400),
                        result=attachment,
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
            elif entry_type not in {"permission-mode"}:
                actions.append(
                    _make_action(
                        "claude_code_projects",
                        session_id,
                        obj.get("uuid") or f"event:{seq}",
                        seq=seq,
                        ts=ts,
                        kind="event",
                        name=entry_type,
                        status=obj.get("status"),
                        summary=truncate_text(extract_text(obj), 400),
                        result=obj,
                        metadata={"path": str(path)},
                        raw_payload=obj,
                    )
                )
    sessions.append(
        _make_session(
            "claude_code_projects",
            session_id,
            ai_name="Claude Code",
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            workspace_key=workspace_path,
            workspace_path=workspace_path,
            title=Path(workspace_path).name if workspace_path else path.stem,
            metadata={"path": str(path)},
            raw_payload={"path": str(path)},
        )
    )
    return {"sessions": sessions, "messages": messages, "actions": actions}

