"""zip 安全解包与世界存档导入（.zip / .mcworld）。

安全约束（PRD Phase 7）:

* **zip-slip 防护**：拒绝 ``../``/绝对路径/空路径条目
* **zip 炸弹防护**：条目数、单条目、总量上限；解包前先读中央目录清单
* **导入前校验**：目标存档必须是可识别的世界（``level.dat`` + ``region/``）
  并通过 :func:`mmffc.formats.chunk.validate` 之外的轻量检查
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from mmffc.core.errors import SchemaError

WORLD_ENTRY = "level.dat"
MAX_ENTRIES = 20_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_ZIP_SUFFIXES = (".zip", ".mcworld")


def is_archive(path) -> bool:
    return Path(path).suffix.lower() in _ZIP_SUFFIXES


@dataclass
class ArchivePlan:
    """解包计划（不安全条目单独列出，不参与解包）。"""

    path: Path
    root_prefix: str
    entries: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def safe(self) -> bool:
        return not self.rejected

    def to_dict(self) -> dict:
        return {
            "file": str(self.path),
            "entry_count": len(self.entries),
            "total_bytes": self.total_bytes,
            "root_prefix": self.root_prefix,
            "rejected": [
                {"name": name, "reason": reason} for name, reason in self.rejected
            ],
            "safe": self.safe,
        }


def _normalize(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _is_unsafe(name: str) -> str | None:
    """先看原始条目名（绝对路径），再归一化后查路径穿越。"""
    if not name.strip():
        return "空条目名"
    normalized_raw = name.replace("\\", "/")
    if normalized_raw.startswith("/") or (
        len(normalized_raw) > 1 and normalized_raw[1] == ":"
    ):
        return "绝对路径"
    normalized = normalized_raw.lstrip("/")
    if not normalized.strip():
        return "空条目名"
    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        return "路径穿越 (..)"
    return None


def plan_archive(path: Path) -> ArchivePlan:
    """读取 zip 中央目录，产出解包计划（不写盘）。"""
    if not path.is_file():
        raise SchemaError(f"归档文件不存在: {path}")
    if not is_archive(path):
        raise SchemaError(f"不支持的文件类型（仅 .zip/.mcworld）: {path.name}")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            plan = ArchivePlan(path=path, root_prefix="")
            names = [info.filename for info in infos]
            if len(infos) > MAX_ENTRIES:
                raise SchemaError(
                    f"归档条目数 {len(infos)} 超过上限 {MAX_ENTRIES}"
                )
            total = 0
            for info in infos:
                reason = _is_unsafe(info.filename)
                if reason is not None:
                    plan.rejected.append((info.filename, reason))
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    plan.rejected.append(
                        (info.filename, f"单条目 {info.file_size} 超过上限")
                    )
                    continue
                total += info.file_size
                plan.entries.append(_normalize(info.filename))
            if total > MAX_TOTAL_BYTES:
                raise SchemaError(
                    f"归档解包总大小 {total} 超过上限 {MAX_TOTAL_BYTES}"
                )
            plan.total_bytes = total
            if not any(name.rstrip("/") == WORLD_ENTRY for name in names):
                for name in names:
                    normalized = _normalize(name)
                    if normalized.count("/") == 1 and normalized.endswith(
                        "/" + WORLD_ENTRY
                    ):
                        plan.root_prefix = normalized.split("/")[0] + "/"
                        break
            return plan
    except zipfile.BadZipFile as exc:
        raise SchemaError(f"归档损坏或不是 zip: {path}: {exc}") from exc


def extract_archive(plan: ArchivePlan, dest: Path) -> Path:
    """按计划解包到 dest（已随 plan 做过安全筛查）。"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(plan.path) as archive:
        for name in plan.entries:
            info = archive.getinfo(name) if name in archive.namelist() else None
            if info is None:
                continue
            relative = name[len(plan.root_prefix):] if plan.root_prefix else name
            relative = relative.lstrip("/")
            if not relative:
                continue
            target = (dest / relative).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue  # 双保险：拒绝穿透
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)
    return dest


def import_world(
    archive_path: Path,
    saves_dir: Path,
    *,
    name: str | None = None,
) -> dict:
    """导入 .zip/.mcworld 到 ``<saves_dir>/<name>/``。

    返回 {world_dir, name, plan, overwritten}；目标已存在时不覆盖（报错）。
    """
    from mmffc.packs.detect import PACK_WORLD, detect_pack_type

    plan = plan_archive(archive_path)
    if not plan.safe:
        reasons = ", ".join(f"{item[0]}({item[1]})" for item in plan.rejected[:3])
        raise SchemaError(f"归档包含不安全条目，拒绝解包: {reasons}")

    world_name = name or archive_path.stem
    saves_dir = Path(saves_dir)
    target = saves_dir / world_name
    if target.exists():
        raise SchemaError(
            f"目标存档已存在: {target}（先移除或指定其他名称）"
        )

    staged = saves_dir / f".import-{world_name}"
    if staged.exists():
        shutil.rmtree(staged)
    extract_archive(plan, staged)

    if not (staged / WORLD_ENTRY).exists():
        nested = staged / plan.root_prefix.rstrip("/")
        if plan.root_prefix and (nested / WORLD_ENTRY).exists():
            staged = nested
        else:
            shutil.rmtree(staged, ignore_errors=True)
            raise SchemaError(f"归档内未找到 {WORLD_ENTRY}（不是世界存档）")

    if detect_pack_type(staged) != PACK_WORLD:
        shutil.rmtree(staged, ignore_errors=True)
        raise SchemaError("归档内容不是可识别的世界存档（缺 region/）")

    staged.replace(target)
    return {
        "world_dir": target,
        "name": world_name,
        "plan": plan.to_dict(),
        "overwritten": False,
    }
