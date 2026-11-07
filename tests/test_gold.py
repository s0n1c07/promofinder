"""Structure checks for the hand-labelled gold set (eval/gold_moments.toml).

The gold set is evaluation data only. These tests check that every entry is a legal promo
under the brief (20-60 s, hook within 3 s, no cut inside a spoken line), not its content.
"""

import tomllib
from pathlib import Path

import pytest

from promofinder.parse import episode_files, parse_episode
from promofinder.timecode import tc_to_seconds

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold_moments.toml"
INPUT = ROOT / "Input"
pytestmark = pytest.mark.skipif(not (GOLD.exists() and any(INPUT.glob("*.txt"))), reason="gold set or input pack missing")

TROPES = {"betrayal", "reveal", "rescue", "cruelty", "danger", "romance", "revenge", "scheme"}
TEXT_FIELDS = ("hook", "cliffhanger", "why")


@pytest.fixture(scope="module")
def gold():
    return tomllib.loads(GOLD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def episodes():
    return {d["episode_number"]: d for d in (parse_episode(f, "input") for f in episode_files(INPUT))}


def _inside_line(ep, t):
    return any(b["start_s"] + 0.05 < t < b["end_s"] - 0.05 for b in ep["blocks"] if b["kind"] == "DIALOGUE")


def _segment_problems(tag, ep, start, end):
    problems = []
    bounds = {b["start_s"] for b in ep["blocks"]} | {b["end_s"] for b in ep["blocks"]}
    if start not in bounds or end not in bounds:
        problems.append(f"{tag}: start/end must be block boundaries")
    if _inside_line(ep, start) or _inside_line(ep, end):
        problems.append(f"{tag}: cuts inside a spoken line")
    if not start < end <= ep["duration_s"]:
        problems.append(f"{tag}: bad window {start}-{end}")
    return problems


def _block(episodes, block_id):
    ep = episodes.get(int(block_id[1:4]))
    return None if ep is None else next((b for b in ep["blocks"] if b["block_id"] == block_id), None)


def test_gold_header(gold):
    assert gold.get("status") in {"draft", "reviewed"}
    moments = gold["moment"]
    assert 12 <= len(moments) <= 25
    ids = [m["id"] for m in moments] + [m["id"] for m in gold.get("montage", [])]
    assert len(ids) == len(set(ids))
    per_episode = {}
    for m in moments:
        per_episode[m["episode"]] = per_episode.get(m["episode"], 0) + 1
    assert max(per_episode.values()) <= 2


def test_single_window_moments_follow_the_brief(gold, episodes):
    problems = []
    for m in gold["moment"]:
        tag = m["id"]
        ep = episodes.get(m["episode"])
        if ep is None:
            problems.append(f"{tag}: no episode {m['episode']}")
            continue
        start, end = tc_to_seconds(m["start"]), tc_to_seconds(m["end"])
        problems += _segment_problems(tag, ep, start, end)
        if not 20 <= end - start <= 60:
            problems.append(f"{tag}: duration {end - start:.1f}s is outside 20-60 s")
        hook, cliff = _block(episodes, m["hook_block"]), _block(episodes, m["cliff_block"])
        if hook is None or not m["hook_block"].startswith(f"E{m['episode']:03d}-") or not start <= hook["start_s"] <= start + 3.0:
            problems.append(f"{tag}: hook block must start within 3 s of the start")
        if cliff is None or not m["cliff_block"].startswith(f"E{m['episode']:03d}-") or not start <= cliff["start_s"] < end:
            problems.append(f"{tag}: cliffhanger block must start inside the window")
        if m.get("trope") not in TROPES or m.get("priority") not in {"must", "should"}:
            problems.append(f"{tag}: trope/priority not in the allowed values")
        if any(not str(m.get(f, "")).strip() for f in TEXT_FIELDS):
            problems.append(f"{tag}: hook, cliffhanger and why must be filled in")
    assert problems == []


def test_montages_follow_the_brief(gold, episodes):
    problems = []
    for m in gold.get("montage", []):
        tag, segs = m["id"], m["segments"]
        if not 2 <= len(segs) <= 3:
            problems.append(f"{tag}: needs 2-3 segments")
            continue
        spans, total = [], 0.0
        for s in segs:
            ep = episodes.get(s["episode"])
            if ep is None:
                problems.append(f"{tag}: no episode {s['episode']}")
                continue
            start, end = tc_to_seconds(s["start"]), tc_to_seconds(s["end"])
            problems += _segment_problems(tag, ep, start, end)
            if end - start < 3.0:
                problems.append(f"{tag}: segment shorter than 3 s")
            spans.append((s["episode"], start, end))
            total += end - start
        if spans != sorted(spans) or any(a[0] == b[0] and b[1] < a[2] for a, b in zip(spans, spans[1:])):
            problems.append(f"{tag}: segments must be in story order without overlaps")
        if not 20 <= total <= 60:
            problems.append(f"{tag}: total {total:.1f}s is outside 20-60 s")
        hook, cliff = _block(episodes, m["hook_block"]), _block(episodes, m["cliff_block"])
        first, last = spans[0], spans[-1]
        if hook is None or int(m["hook_block"][1:4]) != first[0] or not first[1] <= hook["start_s"] <= first[1] + 3.0:
            problems.append(f"{tag}: hook block must start within 3 s of the first segment")
        if cliff is None or int(m["cliff_block"][1:4]) != last[0] or not last[1] <= cliff["start_s"] < last[2]:
            problems.append(f"{tag}: cliffhanger block must be in the last segment")
        if m.get("trope") not in TROPES or any(not str(m.get(f, "")).strip() for f in TEXT_FIELDS):
            problems.append(f"{tag}: trope or text fields invalid")
    assert problems == []
