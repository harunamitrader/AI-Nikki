from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "ai-nikki.json"
DEFAULT_LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "ai-nikki.local.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _resolve_path(base_dir: Path, value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate)
    return str((base_dir / candidate).resolve())


def load_config(config_path: str | None = None) -> dict[str, Any]:
    if config_path:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        config = _deep_merge({}, json.loads(path.read_text(encoding="utf-8")))
    else:
        path = DEFAULT_CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        config = _deep_merge({}, json.loads(path.read_text(encoding="utf-8")))
        if DEFAULT_LOCAL_CONFIG_PATH.exists():
            override = json.loads(DEFAULT_LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
            config = _deep_merge(config, override)
    base_dir = PROJECT_ROOT
    if "paths" in config:
        config["paths"] = {
            key: _resolve_path(base_dir, value)
            for key, value in config["paths"].items()
        }
    for source in config.get("sources", {}).values():
        source["patterns"] = [
            _resolve_path(path.parent, pattern) if not Path(pattern).is_absolute() else pattern
            for pattern in source.get("patterns", [])
        ]
    config["project_root"] = str(PROJECT_ROOT)
    config["config_path"] = str(path)
    return config
