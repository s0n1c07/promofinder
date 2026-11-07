"""Run comparison: rank correlation, window matching, and the review queue."""

from pathlib import Path

import pytest

from promofinder.cli import main
from promofinder.compare import compare_runs, same_window, spearman
from promofinder.config import load_config
from promofinder.judges.llm import LLMJudge
from promofinder.pipeline import run

from .fake_llm import FakeClient, default_reply

INPUT = Path(__file__).resolve().parents[1] / "Input"


def test_spearman_handles_order_and_ties():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert spearman([1, 1, 2, 3], [1, 1, 2, 3]) == 1.0
    assert spearman([1, 2], [1, 2]) is None


def test_windows_match_by_overlap():
    seg = lambda e, a, b: {"episode": e, "start_s": a, "end_s": b, "duration_s": b - a}  # noqa: E731
    p = {"segments": [seg(3, 10, 40)]}
    assert same_window(p, {"segments": [seg(3, 20, 45)]})          # 20 of 25 s shared
    assert not same_window(p, {"segments": [seg(3, 35, 60)]})      # 5 of 25 s shared
    assert not same_window(p, {"segments": [seg(4, 10, 40)]})      # other episode


@pytest.mark.skipif(not any(INPUT.glob("*.txt")), reason="input pack not present")
def test_two_scripted_models_that_disagree(tmp_path):
    cfg = load_config()

    def harsh(user, schema):
        reply = default_reply(user, schema)
        for v in reply.get("clips", {}).values():
            v["hook"], v["cliff"] = 1, 2
        return reply

    run(INPUT, tmp_path / "a", judge=LLMJudge(FakeClient(), cfg), episodes_only={1, 2, 3})
    run(INPUT, tmp_path / "b", judge=LLMJudge(FakeClient(harsh), cfg), episodes_only={1, 2, 3})
    r = compare_runs(tmp_path / "a", tmp_path / "b")
    assert r["scores"]["hook"]["exact_agreement"] == 0.0 and r["scores"]["hook"]["model_pairs"] > 0
    assert r["review_queue"] and r["review_queue"][0]["gap"] == 4   # hook 5 vs 1
    out = tmp_path / "compare.md"
    assert main(["compare", str(tmp_path / "a"), str(tmp_path / "b"), "--out", str(out)]) == 0
    assert "Review queue" in out.read_text(encoding="utf-8")


@pytest.mark.skipif(not any(INPUT.glob("*.txt")), reason="input pack not present")
def test_rules_only_against_a_scripted_model(tmp_path):
    run(INPUT, tmp_path / "rules", judge_spec="offline", episodes_only={1, 2, 3})
    run(INPUT, tmp_path / "model", judge=LLMJudge(FakeClient(), load_config()), episodes_only={1, 2, 3})
    r = compare_runs(tmp_path / "rules", tmp_path / "model")
    assert r["scores"]["cliffhanger"]["candidates"] > 0
    assert r["scores"]["cliffhanger"]["model_pairs"] == 0      # only one side has model scores
    assert r["review_queue"] == []


def test_only_the_same_clip_is_compared(tmp_path):
    """A model proposal's ID (E001-L01) names a different window in each run; it must not be paired."""
    import json

    def write(folder, segs):
        folder.mkdir()
        cand = {"candidate_id": "E001-L01", "segments": segs,
                "judgement": {"hook_score": 0.5, "cliff_score": 0.5, "standalone_score": 0.5, "why_it_works": "",
                              "detail": {"model_scores": {"hook": 5, "cliff": 5, "standalone": 3}}}}
        (folder / "candidates.json").write_text(json.dumps([cand]), encoding="utf-8")
        (folder / "promos.json").write_text("[]", encoding="utf-8")
        (folder / "run_meta.json").write_text(json.dumps({"judge": folder.name}), encoding="utf-8")

    write(tmp_path / "a", [{"episode": 1, "start_s": 0.0, "end_s": 30.0, "duration_s": 30.0}])
    write(tmp_path / "b", [{"episode": 1, "start_s": 50.0, "end_s": 80.0, "duration_s": 30.0}])
    r = compare_runs(tmp_path / "a", tmp_path / "b")
    assert r["scores"]["hook"]["candidates"] == 0 and r["review_queue"] == []
