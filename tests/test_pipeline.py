"""End-to-end rules-only run on the real input pack."""

import json
from pathlib import Path

import pytest

from promofinder.config import load_config
from promofinder.pipeline import run
from promofinder.timecode import tc_to_seconds
from promofinder.timeline import inside_line

INPUT = Path(__file__).resolve().parents[1] / "Input"
pytestmark = pytest.mark.skipif(not any(INPUT.glob("*.txt")), reason="input pack not present")

FILES = ["promos.json", "promos.csv", "promos_ranked.md", "candidates.json", "signals.json",
         "hard_checks.json", "run_meta.json", "parsed/E001.json", "parsed/parse_report.md"]


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("run_offline")
    run(INPUT, out, judge_spec="offline")
    return out


@pytest.fixture(scope="module")
def promos(run_dir):
    return json.loads((run_dir / "promos.json").read_text(encoding="utf-8"))


def test_run_writes_every_file(run_dir):
    assert [f for f in FILES if not (run_dir / f).exists()] == []


def test_hard_checks_pass_for_every_promo(run_dir, promos):
    checks = json.loads((run_dir / "hard_checks.json").read_text(encoding="utf-8"))
    assert checks["final_promos_failing_recheck"] == []
    assert checks["final_promos"] == len(promos) > 0


def test_promos_obey_the_brief_independently(run_dir, promos):
    """Re-derive the brief's rules from the parsed scripts, without calling the validator."""
    cfg = load_config()
    episodes: dict[int, dict] = {}
    for p in promos:
        total = 0.0
        for s in p["segments"]:
            if s["episode"] not in episodes:
                path = run_dir / "parsed" / f"E{s['episode']:03d}.json"
                episodes[s["episode"]] = json.loads(path.read_text(encoding="utf-8"))
            ep = episodes[s["episode"]]
            assert not inside_line(ep, s["start_s"]) and not inside_line(ep, s["end_s"])
            assert 0 <= s["start_s"] < s["end_s"] <= ep["duration_s"]
            total += s["end_s"] - s["start_s"]
        assert cfg["promo"]["min_s"] <= round(total, 2) <= cfg["promo"]["max_s"]
        assert 0 <= p["hook"]["onset_s"] <= cfg["promo"]["hook_max_onset_s"]
        first = p["segments"][0]
        assert tc_to_seconds(p["hook"]["at"]) - first["start_s"] == pytest.approx(p["hook"]["onset_s"], abs=0.01)


def test_ranks_are_consecutive_and_ids_stable(promos):
    assert [p["rank"] for p in promos] == list(range(1, len(promos) + 1))
    assert [p["promo_id"] for p in promos] == [f"P{r:03d}" for r in range(1, len(promos) + 1)]


def test_runs_are_deterministic(run_dir, tmp_path):
    run(INPUT, tmp_path / "again", judge_spec="offline")
    for name in ("promos.json", "candidates.json", "promos_ranked.md"):
        assert (tmp_path / "again" / name).read_text(encoding="utf-8") == (run_dir / name).read_text(encoding="utf-8")


def test_unknown_judge_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown judge"):
        run(INPUT, tmp_path / "x", judge_spec="anthropic:claude-opus-5-5")
    with pytest.raises(ValueError, match="name the model"):
        run(INPUT, tmp_path / "y", judge_spec="ollama")
