"""Deterministic screenplay parser.

Reads one episode file in the input-pack format (schema/EPISODE_FORMAT.md) and returns a
dict that follows schema/episode.schema.json. No model is involved: the format is regular
enough that plain rules parse every file.

Problems that would corrupt the timeline (a block with no timing line, a block that ends
before it starts) raise ParseError with the file and line. Deviations the parser can safely
absorb (uppercase keys, a sound cue running past its scene) are accepted and recorded in the
episode's "warnings" list.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import NoReturn

from .timecode import TimecodeError, is_canonical_tc, seconds_to_tc, tc_to_seconds

SCHEMA_VERSION = "1.0"

EPS = 0.05  # seconds; smaller differences are rounding noise
OUT_OF_ORDER_TOLERANCE = 1.5  # seconds a block may start before an earlier-listed block, silently

SCRIPT_START = "--- SCRIPT START ---"
SCRIPT_END = "--- SCRIPT END ---"

_HEADER = re.compile(r"^(?P<key>EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES)\s*:\s*(?P<val>.*)$", re.I)
_SCENE = re.compile(r"^SCENE\s+(?P<num>\d+)\s*$", re.I)
_SCENE_FIELD = re.compile(
    r"^(?P<key>scene heading|scene start|scene end|actions and dialogue)\s*:\s*(?P<val>.*)$", re.I
)
_CANONICAL_KEYS = {
    "scene heading": "Scene heading",
    "scene start": "Scene start",
    "scene end": "Scene end",
    "actions and dialogue": "Actions and dialogue",
}
_LABEL = re.compile(r"^\*\*(?P<label>.+?):\*\*\s*$")
_TIMING = re.compile(r"^Start:\s*\((?P<s>[^)]*)\)\s*\|\s*End:\s*\((?P<e>[^)]*)\)\s*$", re.I)
_PAREN = re.compile(r"\(([^)]*)\)")

_FILE_EPISODE = re.compile(r"episode-(\d+)", re.I)
_FILE_PLATFORM_ID = re.compile(r"Episode\s+(\d+)\s*$", re.I)

_EPISODE_REF = re.compile(r"\bEpisode\s+(\d+)\b", re.I)
_CLIFF_REF = re.compile(r"\bEpisode\s+(?P<ep>\d+)(?:['’]s)?\s+cliffhanger\b(?P<rest>.*)$", re.I | re.S)
_PREV_CLIFF_REF = re.compile(
    r"\b(?:previous|last|prior)\s+episode(?:['’]s)?\s+cliffhanger\b(?P<rest>.*)$", re.I | re.S
)

_NON_DIALOGUE = {
    "CAMERA": "CAMERA",
    "ACTION": "ACTION",
    "SOUND FX": "SOUND_FX",
    "SFX": "SOUND_FX",
    "SOUND": "SOUND_FX",
}
_VOICE = {
    "O.S.": "O.S.", "O.S": "O.S.", "OS": "O.S.", "O.C.": "O.S.",
    "OFF-SCREEN": "O.S.", "OFFSCREEN": "O.S.", "OFF SCREEN": "O.S.",
    "V.O.": "V.O.", "V.O": "V.O.", "VO": "V.O.", "VOICE-OVER": "V.O.", "VOICE OVER": "V.O.",
}
_IGNORED_PARENS = {"CONT'D", "CONT’D", "CONTD", "CONT."}
_ALIAS = re.compile(r"^[A-Z][a-z]+(?:[ '-][A-Z][a-z]+)*$")

_INT_EXT_PREFIX = re.compile(r"^(?P<p>INT\.?\s*/\s*EXT\.?|EXT\.?\s*/\s*INT\.?|I/E\.?|INT\.|EXT\.)\s*", re.I)
_HEADING_SEP = re.compile(r"\s+[–—-]\s+")
_TIME_OF_DAY = re.compile(
    r"\b(?:DAY|NIGHT|MORNING|EVENING|AFTERNOON|DAWN|DUSK|NOON|MIDNIGHT|SUNSET|SUNRISE|TWILIGHT"
    r"|LATER|CONTINUOUS|SAME TIME)\b",
    re.I,
)
_OPEN_QUOTES = '"“'
_CLOSE_QUOTES = '"”'


class ParseError(ValueError):
    """A structural problem that stops one file from being parsed."""

    def __init__(self, file: str, line: int | None, message: str):
        self.file, self.line, self.reason = file, line, message
        super().__init__(f"{file}:{line if line is not None else '-'}: {message}")


class _Context:
    def __init__(self, file: str):
        self.file = file
        self.warnings: list[dict] = []

    def warn(self, code: str, message: str, line: int | None = None) -> None:
        self.warnings.append({"code": code, "message": message, "line": line})

    def fail(self, line: int | None, message: str) -> NoReturn:
        raise ParseError(self.file, line, message)


# --------------------------------------------------------------------------- small parsers


def parse_label(label: str) -> tuple[str, dict | None]:
    """Split a block label into (kind, speaker).

    'CAMERA' -> ('CAMERA', None)
    'STATION MANAGER (Hollis) (Cold, Calculating)' -> ('DIALOGUE', {label: 'STATION MANAGER',
        alias_hint: 'Hollis', voice: 'ON', delivery: ['Cold', 'Calculating'], ...})
    The last remaining parenthetical is the delivery; an earlier one shaped like a name is
    an alias hint; voice markers ((O.S.), (V.O.), (Off-screen)) can appear anywhere.
    """
    base = label.split("(", 1)[0].strip()
    kind = _NON_DIALOGUE.get(base.upper())
    if kind:
        return kind, None
    if not base:
        raise ValueError(f"speaker label is empty in {label!r}")

    voice, alias, rest = "ON", None, []
    for part in (p.strip() for p in _PAREN.findall(label)):
        upper = part.upper()
        if upper in _VOICE:
            voice = _VOICE[upper]
        elif part and upper not in _IGNORED_PARENS:
            rest.append(part)

    extras: list[str] = []
    for part in rest[:-1]:
        if alias is None and _ALIAS.match(part):
            alias = part
        else:
            extras.append(part)
    delivery = [t.strip() for part in extras + rest[-1:] for t in part.split(",") if t.strip()]
    speaker = {"label": base, "character_id": None, "alias_hint": alias, "voice": voice, "delivery": delivery}
    return "DIALOGUE", speaker


def parse_heading(heading: str) -> tuple[str | None, str | None, str | None]:
    """'INT. COTTAGE - BEDROOM - NIGHT' -> ('INT', 'COTTAGE - BEDROOM', 'NIGHT')."""
    h = heading.strip()
    m = _INT_EXT_PREFIX.match(h)
    rest = h[m.end():] if m else h
    has_int = re.search(r"\bINT\.", h, re.I) is not None
    has_ext = re.search(r"\bEXT\.", h, re.I) is not None
    if (m and ("/" in m["p"])) or (has_int and has_ext):
        int_ext = "INT/EXT"
    elif has_int:
        int_ext = "INT"
    elif has_ext:
        int_ext = "EXT"
    else:
        int_ext = None

    location, time_of_day = rest.strip(), None
    separators = list(_HEADING_SEP.finditer(rest))
    if separators:
        candidate = rest[separators[-1].end():].strip()
        if _TIME_OF_DAY.search(candidate) and len(candidate.split()) <= 3:
            time_of_day = candidate.upper()
            location = rest[: separators[-1].start()].strip()
    return int_ext, (location or None), time_of_day


def parse_continuity(notes: str, episode: int) -> tuple[int | None, str | None]:
    """Return (episode this one follows, description of that episode's cliffhanger).

    The description is an evaluation label only; it must never be shown to the judge model.
    """
    m = _CLIFF_REF.search(notes)
    if m and int(m["ep"]) < episode:
        return int(m["ep"]), _clean_cliff_note(m["rest"])
    m = _PREV_CLIFF_REF.search(notes)
    if m and episode > 1:
        return episode - 1, _clean_cliff_note(m["rest"])
    for ref in _EPISODE_REF.finditer(notes):
        if int(ref[1]) < episode:
            return int(ref[1]), None
    return None, None


def _clean_cliff_note(rest: str) -> str | None:
    rest = re.sub(r"^[\s.,;:–—-]+", "", rest)
    rest = re.sub(r"^(?:where|in which|when)\b[\s,]*", "", rest, flags=re.I)
    return rest.strip() or None


def _strip_quotes(text: str) -> tuple[str, bool]:
    if len(text) >= 2 and text[0] in _OPEN_QUOTES and text[-1] in _CLOSE_QUOTES:
        return text[1:-1].strip(), True
    return text, False


def episode_files(folder: str | Path) -> list[Path]:
    """All .txt files in a folder, ordered by the episode number in the file name."""

    def key(p: Path) -> tuple[int, str]:
        m = _FILE_EPISODE.search(p.stem)
        return (int(m[1]) if m else 10**9, p.name)

    return sorted((p for p in Path(folder).glob("*.txt") if p.is_file()), key=key)


# --------------------------------------------------------------------------- main parser


def parse_episode(path: str | Path, show_id: str) -> dict:
    """Parse one episode file into a dict that follows schema/episode.schema.json."""
    path = Path(path)
    raw = path.read_bytes()
    ctx = _Context(path.name)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        ctx.fail(None, f"file is not valid UTF-8 ({exc.reason} at byte {exc.start})")
    lines = text.splitlines()

    i_start, i_end = _find_script(lines, ctx)
    header = _parse_header(lines[:i_start], ctx)
    episode = _episode_number(path, header, ctx)
    notes = header.get("CONTINUITY NOTES", "")
    follows, prev_cliff = parse_continuity(notes, episode)
    platform_id = _FILE_PLATFORM_ID.search(path.stem)

    raw_scenes = _read_script(lines, i_start, i_end, ctx)
    scenes, blocks = _build(raw_scenes, episode, ctx)
    duration = max(b["end_s"] for b in blocks)
    _check_episode(scenes, duration, ctx)

    return {
        "schema_version": SCHEMA_VERSION,
        "show_id": show_id,
        "episode_number": episode,
        "platform_episode_id": platform_id[1] if platform_id else None,
        "source": {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "line_count": len(lines)},
        "header": {
            "logline": header.get("EPISODE LOGLINE", ""),
            "continuity_notes": notes,
            "follows_episode": follows,
            "prev_cliffhanger_note": prev_cliff,
        },
        "duration_s": duration,
        "scenes": scenes,
        "blocks": blocks,
        "warnings": ctx.warnings,
    }


def _find_script(lines: list[str], ctx: _Context) -> tuple[int, int]:
    starts = [i for i, ln in enumerate(lines) if ln.strip() == SCRIPT_START]
    if not starts:
        ctx.fail(None, f"missing '{SCRIPT_START}' line")
    i_start = starts[0]
    i_end = next((i for i in range(i_start + 1, len(lines)) if lines[i].strip() == SCRIPT_END), None)
    if i_end is None:
        ctx.warn("MISSING_SCRIPT_END", f"no '{SCRIPT_END}' line; read to the end of the file")
        return i_start, len(lines)
    if any(ln.strip() for ln in lines[i_end + 1:]):
        ctx.warn("TRAILING_CONTENT", f"text after '{SCRIPT_END}' was ignored", i_end + 2)
    return i_start, i_end


def _parse_header(lines: list[str], ctx: _Context) -> dict[str, str]:
    values: dict[str, str] = {}
    last_key = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        m = _HEADER.match(s)
        if m:
            last_key = m["key"].upper()
            values[last_key] = m["val"].strip()
        elif last_key:
            values[last_key] += " " + s
            ctx.warn("HEADER_CONTINUATION", f"line joined to {last_key}", i + 1)
        else:
            ctx.warn("UNKNOWN_HEADER_LINE", f"ignored header line {s[:60]!r}", i + 1)
    for key in ("EPISODE LOGLINE", "CONTINUITY NOTES"):
        if key not in values:
            ctx.warn("MISSING_HEADER_FIELD", f"{key} is missing")
    return values


def _episode_number(path: Path, header: dict[str, str], ctx: _Context) -> int:
    m = _FILE_EPISODE.search(path.stem)
    from_file = int(m[1]) if m else None
    from_header = None
    if "EPISODE NUMBER" in header:
        n = re.search(r"\d+", header["EPISODE NUMBER"])
        from_header = int(n[0]) if n else None
        if from_header is None:
            ctx.warn("BAD_EPISODE_NUMBER", f"cannot read a number from {header['EPISODE NUMBER']!r}")
    else:
        ctx.warn("MISSING_HEADER_FIELD", "EPISODE NUMBER is missing; using the file name")
    episode = from_header if from_header is not None else from_file
    if episode is None:
        ctx.fail(None, "no episode number in the header or the file name")
    if not 1 <= episode <= 999:
        ctx.fail(None, f"episode number {episode} is outside 1..999")
    if from_header is not None and from_file is not None and from_header != from_file:
        ctx.warn("EPISODE_NUMBER_MISMATCH", f"header says {from_header}, file name says {from_file}; using the header")
    return episode


def _read_script(lines: list[str], i_start: int, i_end: int, ctx: _Context) -> list[dict]:
    """First pass: group lines into scenes and raw blocks (no interpretation yet)."""
    scenes: list[dict] = []
    key_case: list[tuple[int, str, int]] = []  # (scene position, key as written, line)
    i = i_start + 1
    while i < i_end:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        m = _SCENE.match(s)
        if m:
            scenes.append({"number": int(m["num"]), "line": i + 1, "fields": {}, "blocks": []})
            i += 1
            continue

        m = _SCENE_FIELD.match(s)
        if m:
            if not scenes:
                ctx.fail(i + 1, "scene field before any 'SCENE NN' line")
            key = m["key"].lower()
            if m["key"] != _CANONICAL_KEYS[key]:
                key_case.append((len(scenes), m["key"], i + 1))
            scenes[-1]["fields"][key] = (m["val"].strip(), i + 1)
            i += 1
            continue

        m = _LABEL.match(s)
        if m:
            label = m["label"].strip()
            if not scenes:
                ctx.fail(i + 1, f"block '{label}' appears before any 'SCENE NN' line")
            j = i + 1
            while j < i_end and not lines[j].strip():
                j += 1
            timing = _TIMING.match(lines[j].strip()) if j < i_end else None
            if timing is None:
                ctx.fail(i + 1, f"block '{label}' is not followed by a 'Start: (..) | End: (..)' line")
            k = j + 1
            body: list[str] = []
            while k < i_end:
                t = lines[k].strip()
                if not t or _LABEL.match(t) or _SCENE.match(t) or _SCENE_FIELD.match(t):
                    break
                body.append(t)
                k += 1
            scenes[-1]["blocks"].append(
                {"label": label, "s": timing["s"].strip(), "e": timing["e"].strip(),
                 "body": body, "line": i + 1, "timing_line": j + 1}
            )
            i = k
            continue

        ctx.warn("UNPARSED_LINE", f"ignored line {s[:60]!r}", i + 1)
        i += 1

    if not scenes:
        ctx.fail(None, "no 'SCENE NN' lines found")
    if key_case:
        positions = ", ".join(str(n) for n in sorted({n for n, _, _ in key_case}))
        code = "UPPERCASE_SCENE_KEYS" if all(k.isupper() for _, k, _ in key_case) else "SCENE_KEY_CASE"
        ctx.warn(code, f"scene keys in non-standard case (e.g. {key_case[0][1]!r}) in scene(s) {positions}; accepted",
                 key_case[0][2])
    return scenes


def _build(raw_scenes: list[dict], episode: int, ctx: _Context) -> tuple[list[dict], list[dict]]:
    """Second pass: interpret labels, times and text; assign ids; check scene windows."""
    tag = f"E{episode:03d}"
    scenes: list[dict] = []
    blocks: list[dict] = []
    index = 0
    max_start: float | None = None

    for pos, rs in enumerate(raw_scenes, start=1):
        if pos > 99:
            ctx.fail(rs["line"], "more than 99 scenes in one episode")
        scene_id = f"{tag}-S{pos:02d}"
        if rs["number"] != pos:
            ctx.warn("SCENE_NUMBER_MISMATCH", f"scene numbered {rs['number']} is scene {pos} in file order", rs["line"])

        scene_blocks = []
        for rb in rs["blocks"]:
            index += 1
            if index > 999:
                ctx.fail(rb["line"], "more than 999 blocks in one episode")
            block = _make_block(rb, tag, index, scene_id, ctx)
            if max_start is not None and block["start_s"] < max_start - OUT_OF_ORDER_TOLERANCE:
                ctx.warn("START_OUT_OF_ORDER",
                         f"{block['block_id']} starts {max_start - block['start_s']:.2f}s before an earlier-listed block",
                         rb["line"])
            max_start = block["start_s"] if max_start is None else max(max_start, block["start_s"])
            scene_blocks.append(block)

        heading, _ = rs["fields"].get("scene heading", ("", rs["line"]))
        if "scene heading" not in rs["fields"]:
            ctx.warn("MISSING_SCENE_FIELD", f"scene {pos} has no heading", rs["line"])
        int_ext, location, time_of_day = parse_heading(heading)
        start = _scene_time(rs, "scene start", min((b["start_s"] for b in scene_blocks), default=0.0), pos, ctx)
        end = _scene_time(rs, "scene end", max((b["end_s"] for b in scene_blocks), default=start), pos, ctx)
        if end < start:
            ctx.fail(rs["line"], f"scene {pos} ends before it starts")
        if not scene_blocks:
            ctx.warn("EMPTY_SCENE", f"scene {pos} has no blocks", rs["line"])

        for b in scene_blocks:
            overrun = max(start - b["start_s"], b["end_s"] - end)
            if overrun > EPS:
                ctx.warn("BLOCK_OUTSIDE_SCENE",
                         f"{b['block_id']} ({b['kind']} {b['start_tc']}-{b['end_tc']}) runs {overrun:.2f}s outside "
                         f"scene {pos} ({seconds_to_tc(start)}-{seconds_to_tc(end)}); kept",
                         b["source_line"])

        scenes.append({
            "scene_id": scene_id,
            "index": pos,
            "heading": heading,
            "int_ext": int_ext,
            "location": location,
            "time_of_day": time_of_day,
            "start_s": start,
            "end_s": end,
            "start_tc": seconds_to_tc(start),
            "end_tc": seconds_to_tc(end),
            "block_ids": [],
        })
        blocks.extend(scene_blocks)

    if not blocks:
        ctx.fail(None, "no blocks found")
    blocks.sort(key=lambda b: (b["start_s"], b["index"]))
    by_scene: dict[str, list[str]] = {}
    for b in blocks:
        by_scene.setdefault(b["scene_id"], []).append(b["block_id"])
    for sc in scenes:
        sc["block_ids"] = by_scene.get(sc["scene_id"], [])
    return scenes, blocks


def _make_block(rb: dict, tag: str, index: int, scene_id: str, ctx: _Context) -> dict:
    try:
        kind, speaker = parse_label(rb["label"])
    except ValueError as exc:
        ctx.fail(rb["line"], str(exc))
    try:
        start, end = tc_to_seconds(rb["s"]), tc_to_seconds(rb["e"])
    except TimecodeError as exc:
        ctx.fail(rb["timing_line"], str(exc))
    if not (is_canonical_tc(rb["s"]) and is_canonical_tc(rb["e"])):
        ctx.warn("TIMECODE_FORMAT", f"timecodes {rb['s']!r} / {rb['e']!r} normalised to MM:SS.cc", rb["timing_line"])
    if end < start:
        ctx.fail(rb["timing_line"], f"block ends ({rb['e']}) before it starts ({rb['s']})")

    body = rb["body"]
    if not body:
        ctx.warn("EMPTY_BODY", f"block '{rb['label']}' has no text", rb["line"])
    elif len(body) > 1:
        ctx.warn("MULTILINE_BODY", f"block '{rb['label']}' text spans {len(body)} lines; joined", rb["line"])
    text = " ".join(body)
    if kind == "DIALOGUE" and body:
        text, quoted = _strip_quotes(text)
        if not quoted:
            ctx.warn("UNQUOTED_DIALOGUE", f"dialogue for '{rb['label']}' is not in quotes; kept as written", rb["line"])

    return {
        "block_id": f"{tag}-B{index:03d}",
        "index": index,
        "scene_id": scene_id,
        "kind": kind,
        "start_s": start,
        "end_s": end,
        "start_tc": seconds_to_tc(start),
        "end_tc": seconds_to_tc(end),
        "text": text,
        "speaker": speaker,
        "raw_label": rb["label"],
        "source_line": rb["line"],
    }


def _scene_time(rs: dict, key: str, fallback: float, pos: int, ctx: _Context) -> float:
    if key in rs["fields"]:
        value, line = rs["fields"][key]
        try:
            return tc_to_seconds(value)
        except TimecodeError as exc:
            ctx.fail(line, f"{key}: {exc}")
    ctx.warn("MISSING_SCENE_FIELD", f"scene {pos} has no '{key}'; taken from its blocks", rs["line"])
    return fallback


def _check_episode(scenes: list[dict], duration: float, ctx: _Context) -> None:
    if abs(scenes[0]["start_s"]) > EPS:
        ctx.warn("FIRST_SCENE_NOT_AT_ZERO", f"first scene starts at {scenes[0]['start_tc']}")
    for a, b in zip(scenes, scenes[1:]):
        gap = b["start_s"] - a["end_s"]
        if gap > EPS:
            ctx.warn("SCENE_GAP", f"{gap:.2f}s gap between scene {a['index']} and scene {b['index']}")
        elif gap < -EPS:
            ctx.warn("SCENE_OVERLAP", f"scene {b['index']} starts {-gap:.2f}s before scene {a['index']} ends")
    if abs(scenes[-1]["end_s"] - duration) > EPS:
        ctx.warn("DURATION_MISMATCH",
                 f"last scene ends at {scenes[-1]['end_tc']} but the last block ends at {seconds_to_tc(duration)}")
