"""Validation of parsed episodes: the JSON Schema plus cross-field integrity checks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .timecode import seconds_to_tc

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "episode.schema.json"


def _inline_refs(node: object, defs: dict) -> object:
    """Replace local {"$ref": "#/$defs/x"} with the definition itself (validation runs ~30% faster)."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if len(node) == 1 and isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline_refs(defs[ref.rsplit("/", 1)[1]], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(_inline_refs(schema, schema.get("$defs", {})))


def schema_errors(doc: object) -> list[str]:
    """Violations of schema/episode.schema.json, as 'path: message' strings."""
    errors = sorted(_validator().iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in errors]


def integrity_errors(doc: dict) -> list[str]:
    """Checks JSON Schema cannot express. Assumes the document is already schema-valid."""
    problems: list[str] = []
    tag = f"E{doc['episode_number']:03d}-"
    blocks, scenes = doc["blocks"], doc["scenes"]
    block_ids = [b["block_id"] for b in blocks]
    scene_ids = [s["scene_id"] for s in scenes]

    if len(set(block_ids)) != len(block_ids):
        problems.append("duplicate block_id")
    if len(set(scene_ids)) != len(scene_ids):
        problems.append("duplicate scene_id")
    foreign = [i for i in block_ids + scene_ids if not i.startswith(tag)]
    if foreign:
        problems.append(f"ids not prefixed with {tag}: {foreign[:3]}")
    if sorted(b["index"] for b in blocks) != list(range(1, len(blocks) + 1)):
        problems.append("block index values are not 1..n")
    if [s["index"] for s in scenes] != list(range(1, len(scenes) + 1)):
        problems.append("scene index values are not 1..n in order")

    order = [(b["start_s"], b["index"]) for b in blocks]
    if order != sorted(order):
        problems.append("blocks are not sorted by (start_s, index)")

    listed_in = {bid: s["scene_id"] for s in scenes for bid in s["block_ids"]}
    if sorted(listed_in) != sorted(block_ids) or sum(len(s["block_ids"]) for s in scenes) != len(blocks):
        problems.append("scene block_ids do not list every block exactly once")

    for b in blocks:
        bid = b["block_id"]
        if listed_in.get(bid) != b["scene_id"]:
            problems.append(f"{bid}: scene_id {b['scene_id']} does not list it")
        if b["end_s"] < b["start_s"]:
            problems.append(f"{bid}: ends before it starts")
        if b["start_tc"] != seconds_to_tc(b["start_s"]) or b["end_tc"] != seconds_to_tc(b["end_s"]):
            problems.append(f"{bid}: timecode text does not match seconds")
        if (b["kind"] == "DIALOGUE") != (b["speaker"] is not None):
            problems.append(f"{bid}: speaker must be present for DIALOGUE blocks only")

    for s in scenes:
        if s["end_s"] < s["start_s"]:
            problems.append(f"{s['scene_id']}: ends before it starts")
        if s["start_tc"] != seconds_to_tc(s["start_s"]) or s["end_tc"] != seconds_to_tc(s["end_s"]):
            problems.append(f"{s['scene_id']}: timecode text does not match seconds")

    if blocks and doc["duration_s"] != max(b["end_s"] for b in blocks):
        problems.append("duration_s is not the end of the last block")
    return problems


def validate_episode(doc: object) -> list[str]:
    """Every problem with a parsed episode; an empty list means it is valid."""
    problems = schema_errors(doc)
    return problems if problems else integrity_errors(doc)  # type: ignore[arg-type]
