"""Format modules: FancyMenu DSL, FTB Quests (JSON5/SNBT).

Each format module exposes a common interface used by
mmffc.io.file_io (the file_io_engine):

* parse(text) -> document           (raises SchemaError on syntax errors)
* serialize(document) -> str
* validate(document) -> list[str]   (semantic errors)
* to_plain(document) -> Any         (JSON-serializable view for schema validation)
* default_filename(document) -> str
"""
