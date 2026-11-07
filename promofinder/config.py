"""Run configuration: config/default.toml, optionally overridden by a user file."""

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "default.toml"


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None) -> dict:
    """Defaults, with any keys from `path` laid on top."""
    cfg = tomllib.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    if path:
        cfg = _merge(cfg, tomllib.loads(Path(path).read_text(encoding="utf-8")))
    return cfg


def resolve(path: str | Path) -> Path:
    """Paths inside the config are relative to the repository root."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode("utf-8")).hexdigest()[:12]
