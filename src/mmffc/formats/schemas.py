"""JSON Schema definitions for structural (phase 1) validation.

Semantic (phase 2) validation lives in the format modules
(:mod:`mmffc.formats.fancymenu`, :mod:`mmffc.formats.ftbquests`).
"""

from __future__ import annotations

from typing import Any

# FancyMenu layout document, in its plain (JSON) view:
# {"type": "fancymenu_layout", "sections": [{"name": ..., "properties": {...}}]}
FANCYMENU_LAYOUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FancyMenuLayout",
    "type": "object",
    "required": ["type", "sections"],
    "additionalProperties": False,
    "properties": {
        "type": {"const": "fancymenu_layout"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "properties"],
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "^[a-z0-9_-]+(?::[a-z0-9_-]+)*$",
                    },
                    "properties": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
        },
    },
}

# FTB Quests chapter, in its plain (JSON) view.
FTBQUESTS_CHAPTER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FTBQuestsChapter",
    "type": "object",
    "required": ["name", "description", "icon", "quests"],
    "additionalProperties": True,
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9_.-]+$"},
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "icon": {"type": "string", "minLength": 1},
        "quests": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "name", "description", "icon"],
                "additionalProperties": True,
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9_.-]+$"},
                    "name": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "icon": {"type": "string", "minLength": 1},
                    "tasks": {"type": "array"},
                    "rewards": {"type": "array"},
                    "dependencies": {"type": "array"},
                },
            },
        },
    },
}

SCHEMAS = {
    "fancymenu": FANCYMENU_LAYOUT_SCHEMA,
    "ftbquests": FTBQUESTS_CHAPTER_SCHEMA,
}
