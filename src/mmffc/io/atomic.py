"""Atomic file writes with automatic backup (CLI.md 2.1 rule 6).

Write protocol: temp file in target directory -> fsync -> os.replace().
Every overwrite is backed up to <MMFFC_HOME>/backups/<UTC-timestamp>/ first.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from mmffc.core.errors import PermissionDeniedError


def _timestamp_dir(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = root / stamp
    path = base
    counter = 1
    while path.exists():
        path = base.with_name(f"{base.name}-{counter}")
        counter += 1
    path.mkdir(parents=True, exist_ok=True)
    return path


def _unique_name(directory: Path, name: str) -> Path:
    candidate = directory / name
    stem, suffix = os.path.splitext(name)
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def backup_file(target: Path, backup_root: Path) -> Path | None:
    """Copy an existing file into the backup tree. Returns backup path or None."""
    if not target.exists():
        return None
    backup_dir = _timestamp_dir(backup_root)
    dest = _unique_name(backup_dir, target.name)
    try:
        shutil.copy2(target, dest)
    except OSError as exc:
        raise PermissionDeniedError(f"备份失败: {target}: {exc}") from exc
    return dest


def atomic_write_bytes(
    dest: Path,
    data: bytes,
    *,
    backup_root: Path | None = None,
) -> Path | None:
    """Atomically write bytes to dest; returns backup path if a backup was made."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_file(dest, backup_root) if backup_root is not None else None

    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=dest.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return backup


def move_to_backup(target: Path, backup_root: Path) -> Path | None:
    """Move an existing file into the backup tree (used by mod remove)."""
    if not target.exists():
        return None
    backup_dir = _timestamp_dir(backup_root)
    dest = _unique_name(backup_dir, target.name)
    try:
        shutil.move(str(target), str(dest))
    except OSError as exc:
        raise PermissionDeniedError(f"移动文件到备份目录失败: {target}: {exc}") from exc
    return dest
