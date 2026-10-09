"""Saved settings: only the theme. Everything else is gone when the viewer closes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_config_dir

from .themes import DEFAULT_PALETTE


@dataclass
class Config:
    theme: str = DEFAULT_PALETTE


def config_path() -> Path:
    return Path(user_config_dir("herdr-blueprint")) / "config.json"


def load(path: Path | None = None) -> Config:
    """Saved config, or defaults when the file is missing or broken."""
    try:
        data = json.loads((path or config_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    if not isinstance(data, dict):
        return Config()
    return Config(theme=str(data.get("theme", DEFAULT_PALETTE)))


def save(config: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
