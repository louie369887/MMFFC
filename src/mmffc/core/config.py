"""Application configuration resolution.

Priority (CLI.md 2.1 rule 7): CLI options > environment variables
> project config (./mmffc.yaml) > user config (~/.mmffc/config.yaml) > defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mmffc.core.errors import UsageError

DEFAULT_HOME = Path("~/.mmffc")
PROJECT_CONFIG_NAMES = ("mmffc.yaml", ".mmffc.yaml")
USER_CONFIG_NAME = "config.yaml"


@dataclass
class MMFFCConfig:
    """Resolved application configuration."""

    home: Path
    mods_dir: Path
    cache_dir: Path
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    @property
    def modrinth_token(self) -> str | None:
        return os.environ.get("MODRINTH_TOKEN") or self.data.get("modrinth_token")

    @property
    def curseforge_api_key(self) -> str | None:
        return os.environ.get("CURSEFORGE_API_KEY") or self.data.get("curseforge_api_key")


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            content = yaml.safe_load(fh) or {}
    except OSError as exc:
        raise UsageError(f"配置文件读取失败: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise UsageError(f"配置文件 YAML 格式错误: {path}: {exc}") from exc
    if not isinstance(content, dict):
        raise UsageError(f"配置文件格式错误 (应为键值映射): {path}")
    return content


def load_config(config_path: str | Path | None = None) -> MMFFCConfig:
    """Load and merge configuration from all sources."""
    env_home = os.environ.get("MMFFC_HOME")
    home = _expand(env_home) if env_home else _expand(DEFAULT_HOME)

    merged: dict[str, Any] = {}
    sources: list[str] = []

    # 1. user config (~/.mmffc/config.yaml) - lowest precedence
    user_config = home / USER_CONFIG_NAME
    if user_config.exists():
        merged.update(_read_yaml(user_config))
        sources.append(str(user_config))

    # 2. project config (./mmffc.yaml or ./.mmffc.yaml)
    for name in PROJECT_CONFIG_NAMES:
        candidate = Path.cwd() / name
        if candidate.exists():
            merged.update(_read_yaml(candidate))
            sources.append(str(candidate))
            break

    # 3. explicit --config file - highest file precedence
    if config_path:
        explicit = _expand(config_path)
        if not explicit.exists():
            raise UsageError(f"配置文件不存在: {explicit}")
        merged.update(_read_yaml(explicit))
        sources.append(str(explicit))

    mods_dir = (
        os.environ.get("MMFFC_MODS_DIR")
        or merged.get("mods_dir")
        or str(Path.cwd() / "mods")
    )
    cache_dir = (
        os.environ.get("MMFFC_CACHE_DIR")
        or merged.get("cache_dir")
        or str(home / "cache")
    )

    return MMFFCConfig(
        home=home,
        mods_dir=_expand(mods_dir),
        cache_dir=_expand(cache_dir),
        data=merged,
        sources=sources,
    )
