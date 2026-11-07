"""M1 exit checks on the real input pack.

The expected numbers come from the independent survey recorded in schema/EPISODE_FORMAT.md,
not from the parser's own output. Two survey figures were wrong and were corrected after
recomputing them with a separate script: total runtime is 6162.62 s (the survey added
durations already rounded to 0.1 s), and 39 continuity notes name an earlier episode
(Episodes 34 and 49 mention only their own number).
"""

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from promofinder.parse import episode_files, parse_episode
from promofinder.schema import validate_episode

INPUT = Path(__file__).resolve().parents[1] / "Input"
pytestmark = pytest.mark.skipif(not any(INPUT.glob("*.txt")), reason="input pack not present")


def _digest(text: str) -> str:
    """The pack is confidential, so expected lines and labels are kept as short hashes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@pytest.fixture(scope="module")
def episodes():
    return [parse_episode(f, show_id="input") for f in episode_files(INPUT)]


def test_all_50_episodes_parse_in_order(episodes):
    assert [d["episode_number"] for d in episodes] == list(range(1, 51))


def test_every_episode_is_schema_valid(episodes):
    problems = {d["episode_number"]: validate_episode(d) for d in episodes}
    assert {ep: p for ep, p in problems.items() if p} == {}


def test_only_the_two_known_warnings(episodes):
    found = sorted((d["episode_number"], w["code"]) for d in episodes for w in d["warnings"])
    assert found == [(6, "UPPERCASE_SCENE_KEYS"), (22, "BLOCK_OUTSIDE_SCENE")]


def test_totals_match_the_survey(episodes):
    kinds = Counter(b["kind"] for d in episodes for b in d["blocks"])
    assert kinds == {"CAMERA": 1754, "DIALOGUE": 1487, "ACTION": 372, "SOUND_FX": 114}
    assert sum(len(d["scenes"]) for d in episodes) == 100
    assert round(sum(d["duration_s"] for d in episodes), 2) == 6162.62
    assert min(d["duration_s"] for d in episodes) == 70.5
    assert max(d["duration_s"] for d in episodes) == 200.75
    labels = {b["speaker"]["label"] for d in episodes for b in d["blocks"] if b["speaker"]}
    assert len(labels) == 64


def test_a_known_line_is_parsed_exactly(episodes):
    [block] = [b for b in episodes[0]["blocks"] if b["kind"] == "DIALOGUE" and b["start_tc"] == "00:39.10"]
    assert block["end_tc"] == "00:41.20"
    assert _digest(block["text"]) == "9d84337c76e1"
    assert _digest(block["speaker"]["label"]) == "42da6d43818d"
    assert len(block["speaker"]["delivery"]) == 2


def test_alias_hints_inside_labels(episodes):
    hints = Counter(
        b["speaker"]["alias_hint"] for d in episodes for b in d["blocks"] if b["speaker"] and b["speaker"]["alias_hint"]
    )
    assert sorted(hints.values()) == [4]  # one person is named inside a label, 4 times


def test_continuity_labels(episodes):
    assert sum(d["header"]["follows_episode"] is not None for d in episodes) == 39
    assert episodes[33]["header"]["follows_episode"] is None  # Episode 34 names only itself
    assert episodes[48]["header"]["follows_episode"] is None  # Episode 49 names only itself
    notes = {d["episode_number"]: d["header"]["prev_cliffhanger_note"] for d in episodes}
    labelled = sorted(ep for ep, note in notes.items() if note)
    assert labelled == [2, 4, 12, 14, 16, 21, 23, 24, 27, 32, 44, 46]
    assert _digest(notes[14]) == "36e435c5e3a1"
    for d in episodes:
        if d["header"]["prev_cliffhanger_note"]:
            assert d["header"]["follows_episode"] == d["episode_number"] - 1


def test_every_block_traces_back_to_its_source_lines(episodes):
    """Round trip: label, timing and text of all 3,727 blocks match the raw file at source_line."""
    mismatches = []
    for d in episodes:
        raw = (INPUT / d["source"]["file"]).read_text(encoding="utf-8").splitlines()
        for b in d["blocks"]:
            i = b["source_line"] - 1
            label_line, timing_line, body_line = raw[i].strip(), raw[i + 1].strip(), raw[i + 2].strip()
            expected_body = f'"{b["text"]}"' if b["kind"] == "DIALOGUE" else b["text"]
            if (
                label_line != f"**{b['raw_label']}:**"
                or timing_line != f"Start: ({b['start_tc']}) | End: ({b['end_tc']})"
                or body_line != expected_body
            ):
                mismatches.append(b["block_id"])
    assert mismatches == []


def test_out_of_order_blocks_are_sorted(episodes):
    for d in episodes:
        starts = [b["start_s"] for b in d["blocks"]]
        assert starts == sorted(starts)
    ep4 = episodes[3]
    file_order = sorted(ep4["blocks"], key=lambda b: b["index"])
    steps_back = sum(b["start_s"] < a["start_s"] for a, b in zip(file_order, file_order[1:]))
    assert steps_back == 1
