"""FTB Quests 章节格式：JSON5（2026+）与 SNBT（传统）。

任务对象必需字段：id、name、description、icon；
id 必须匹配 ``^[a-z0-9_.-]+$``。

章节对象（chapter）：

.. code-block:: json5

    {
      id: "my_chapter"
      name: "My Chapter"
      description: "..."
      icon: "minecraft:book"
      quests: [
        {
          id: "first_quest"
          name: "First Quest"
          description: "..."
          icon: "minecraft:diamond"
          tasks: []
          rewards: []
          dependencies: []
        }
      ]
    }

根据目标 FTB Quests 版本自动选择输出格式：
JSON5（默认，对应 2026 年及以后的版本）或 SNBT（传统）。
"""

from __future__ import annotations

import re
from typing import Any

from mmffc.core.errors import SchemaError
from mmffc.formats import json5 as json5_module
from mmffc.formats import snbt as snbt_module

ID_PATTERN = re.compile(r"^[a-z0-9_.-]+$")

# Task/reward types known to MMFFC validation (non-exhaustive;
# unknown task types are allowed for forward compatibility).
KNOWN_TASK_TYPES = {
    "item",
    "block",
    "fluid",
    "entity",
    "xp",
    "location",
    "command",
    "dim",
    "observation",
    "variable",
    "kill",
    "ticker",
    "checkmark",
    "advancement",
    "item_interaction",
    "right_click",
    "custom",
}


# ----------------------------------------------------------------------
# parse / serialize
# ----------------------------------------------------------------------
def parse_chapter(text: str, *, fmt: str | None = None) -> dict[str, Any]:
    """Parse chapter content. fmt: 'json5' (default) or 'snbt'.

    When fmt is None the format is auto-detected: content whose
    first non-space character is '{' and which contains SNBT-style
    unquoted keys with colons is treated as SNBT.
    """
    if fmt is None:
        fmt = detect_format(text)
    if fmt == "snbt":
        parsed = snbt_module.snbt_loads(text)
    elif fmt == "json5":
        parsed = json5_module.json5_loads(text)
    else:
        raise SchemaError(f"未知的 FTB Quests 格式: {fmt!r}")
    if not isinstance(parsed, dict):
        raise SchemaError("FTB Quests 章节必须是复合对象")
    return parsed


def detect_format(text: str) -> str:
    """Auto-detect json5 vs snbt for a chapter document.

    Heuristics (most specific first):

    1. leading comments -> json5 (SNBT has no comments)
    2. trailing comma before ``}``/``]`` -> json5
    3. typed lists ``[I;...]`` or numeric suffixes (``1b``, ``2L``)
       -> snbt
    4. quoted keys at line starts -> snbt (our serializer style)
    5. ambiguous -> try parsing as json5, fall back to snbt
    """
    stripped = text.lstrip()
    if stripped.startswith("//") or stripped.startswith("/*"):
        return "json5"

    bare = _strip_strings(text)
    if re.search(r",\s*[}\]]", bare):
        return "json5"
    if re.search(r"\[[BISLFD];", bare) or re.search(
        r"\d[bBsSlLdDfF]\b", bare
    ):
        return "snbt"
    if re.search(r'^\s*"', text, re.M):
        return "snbt"

    try:
        json5_module.json5_loads(text)
        return "json5"
    except SchemaError:
        return "snbt"


def _strip_strings(text: str) -> str:
    """Blank out quoted spans so heuristics only see structure."""
    output: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in ('"', "'"):
            quote = char
            output.append('""')
            index += 1
            while index < length and text[index] != quote:
                if text[index] == "\\":
                    index += 1
                index += 1
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def serialize_chapter(
    chapter: dict[str, Any], *, fmt: str = "json5", indent: int = 2
) -> str:
    if fmt == "snbt":
        return snbt_module.snbt_dumps(chapter, indent=indent)
    if fmt == "json5":
        return json5_module.json5_dumps(chapter, indent=indent)
    raise SchemaError(f"未知的 FTB Quests 格式: {fmt!r}")


# ----------------------------------------------------------------------
# semantic validation
# ----------------------------------------------------------------------
def validate_chapter(chapter: dict[str, Any]) -> list[str]:
    """Validate a parsed chapter. Returns a list of error strings."""
    errors: list[str] = []

    for field in ("name", "icon"):
        value = chapter.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"章节缺少必需的非空字段: {field}")

    description = chapter.get("description")
    if not isinstance(description, str):
        errors.append("章节缺少必需的字符串字段: description")

    chapter_id = chapter.get("id")
    if chapter_id is not None:
        if not isinstance(chapter_id, str) or not ID_PATTERN.match(
            chapter_id
        ):
            errors.append(
                f"章节 id 必须匹配 ^[a-z0-9_.-]+$ (得到 {chapter_id!r})"
            )

    quests = chapter.get("quests")
    if not isinstance(quests, list) or not quests:
        errors.append("章节缺少非空的 quests 数组")
        return errors

    seen_ids: set[str] = set()
    for index, quest in enumerate(quests):
        where = f"quests[{index}]"
        if not isinstance(quest, dict):
            errors.append(f"{where}: 必须是对象")
            continue
        for field in ("name", "icon"):
            value = quest.get(field)
            if not isinstance(value, str) or not value:
                errors.append(f"{where}: 任务缺少必需的非空字段: {field}")
        if not isinstance(quest.get("description"), str):
            errors.append(
                f"{where}: 任务缺少必需的字符串字段: description"
            )
        quest_id = quest.get("id")
        if isinstance(quest_id, str):
            if not ID_PATTERN.match(quest_id):
                errors.append(
                    f"{where}: 任务 id 必须匹配 ^[a-z0-9_.-]+$ "
                    f"(得到 {quest_id!r})"
                )
            elif quest_id in seen_ids:
                errors.append(f"{where}: 重复的任务 id: {quest_id}")
            else:
                seen_ids.add(quest_id)
        for field in ("tasks", "rewards", "dependencies"):
            value = quest.get(field)
            if value is not None and not isinstance(value, list):
                errors.append(f"{where}: {field} 必须是数组")
        tasks = quest.get("tasks")
        if isinstance(tasks, list):
            for task_index, task in enumerate(tasks):
                if isinstance(task, dict):
                    task_type = task.get("type")
                    if (
                        isinstance(task_type, str)
                        and task_type not in KNOWN_TASK_TYPES
                    ):
                        # forward compatible: unknown task types are
                        # allowed, but reported as warnings via the
                        # returned list prefix
                        errors.append(
                            f"警告: {where}.tasks[{task_index}]: "
                            f"未知任务类型 {task_type!r}"
                        )
        dependencies = quest.get("dependencies")
        if isinstance(dependencies, list):
            for dep in dependencies:
                if isinstance(dep, str) and dep not in seen_ids:
                    errors.append(
                        f"{where}: 依赖 {dep!r} 指向不存在的任务"
                    )

    return errors


def default_filename(chapter: dict[str, Any], *, fmt: str = "json5") -> str:
    """Derive a chapter file name from the document."""
    chapter_id = chapter.get("id")
    if isinstance(chapter_id, str) and ID_PATTERN.match(chapter_id):
        slug = chapter_id
    else:
        name = chapter.get("name") or "chapter"
        slug = re.sub(r"[^a-z0-9_.-]+", "_", str(name).lower()).strip("_")
        slug = slug or "chapter"
    extension = "snbt" if fmt == "snbt" else "json5"
    return f"{slug}.{extension}"
