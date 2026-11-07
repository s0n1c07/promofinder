"""Quality checks: the blind viewer's view of a promo, the naive baseline's rule checks, the report."""

import json
import re
from pathlib import Path

import pytest

from promofinder.cli import main
from promofinder.config import load_config
from promofinder.pipeline import run
from promofinder.qc import (CUT_LINE, blind_selection, blind_verdict, check_naive, clip_transcript, qc_markdown,
                            run_blind_test)

from .fake_llm import FakeClient
from .screenplay import synthetic_episode

INPUT = Path(__file__).resolve().parents[1] / "Input"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture()
def ep(tmp_path):
    return synthetic_episode(tmp_path)


def _seg(start, end, episode=9):
    return {"episode": episode, "start_s": start, "end_s": end}


def test_the_blind_viewer_sees_only_what_the_promo_shows_and_says(ep):
    text = clip_transcript({"segments": [_seg(10.0, 34.0)]}, {9: ep})
    assert text.splitlines()[:3] == ["[camera] Close-up on Lady Vera.", "[action] Lady Vera slaps Mira across the face.",
                                     "MIRA (Shocked, Trembling): You struck me!"]
    assert "B00" not in text and "10.2" not in text and "Your own sister did." not in text   # no IDs, times or payoff
    joined = clip_transcript({"segments": [_seg(10.0, 14.0), _seg(30.0, 34.0)]}, {9: ep})
    assert CUT_LINE in joined.splitlines() and joined.splitlines()[-1] == "MIRA (Desperate): Who told you my secret?"


def test_blind_verdicts():
    reply = {"conflict": "x", "open_question": "y", "confusing": ["the ring"], "follow": "yes", "tap": "no"}
    assert blind_verdict(reply | {"standalone": 4}) == "clear"      # a listed gap alone doesn't fail a promo
    assert blind_verdict(reply | {"standalone": 3}) == "gaps"
    assert blind_verdict(reply | {"standalone": 5, "follow": "no"}) == "lost"


def test_the_blind_and_naive_prompts_list_their_fields_in_schema_order(cfg):
    from promofinder.config import resolve
    from promofinder.qc import BLIND_SCHEMA, NAIVE_SCHEMA

    for key, fields in (("blind_prompt", list(BLIND_SCHEMA["properties"])),
                        ("naive_prompt", list(NAIVE_SCHEMA["properties"]["promos"]["items"]["properties"]))):
        text = resolve(cfg["qc"][key]).read_text(encoding="utf-8")
        assert [f for f in re.findall(r"^- (\w+)", text, re.M) if f in fields] == fields, key


def test_blind_selection_takes_the_top_every_joined_promo_and_a_seeded_control():
    promos = [{"promo_id": f"P{i:03d}", "rank": i, "kind": "montage" if i in (2, 7) else "single"} for i in range(1, 21)]
    picked = blind_selection(promos, top=3, control=4, seed=7)
    groups = {p["promo_id"]: g for p, g in picked}
    assert [k for k, g in groups.items() if g == "top"] == ["P001", "P002", "P003"]
    assert groups["P007"] == "joined" and sum(g == "control" for g in groups.values()) == 4
    assert picked == blind_selection(promos, top=3, control=4, seed=7)          # same seed, same sample
    assert [p["rank"] for p, _ in picked] == sorted(p["rank"] for p, _ in picked)


@pytest.mark.parametrize("promo, problems", [
    ({"segments": [{"episode": 9, "start": "00:10.00", "end": "00:34.00"}],
      "hook_quote": "Lady Vera slaps Mira across the face.", "cliffhanger_quote": "MIRA: \"Who told you my secret?\""}, []),
    ({"segments": [{"episode": 9, "start": "00:16.00", "end": "00:40.00"}],
      "hook_quote": "You are nothing but a servant.", "cliffhanger_quote": "Who told you my secret?"},
     ["cuts inside a spoken line", "hook not in the first 3 s"]),
    ({"segments": [{"episode": 9, "start": "00:10.00", "end": "00:14.00"}],
      "hook_quote": "Lady Vera slaps Mira across the face.", "cliffhanger_quote": "You struck me!"}, ["not 20–60 s"]),
    ({"segments": [{"episode": 9, "start": "00:10.00", "end": "00:34.00"}],
      "hook_quote": "She burns the letter in the fire.", "cliffhanger_quote": "Who told you my secret?"},
     ["hook not in the script"]),
    ({"segments": [{"episode": 9, "start": "ten seconds", "end": "00:34.00"}],
      "hook_quote": "x", "cliffhanger_quote": "y"}, ["unreadable time"]),
])
def test_naive_promos_are_checked_against_the_brief(ep, cfg, promo, problems):
    assert check_naive(promo, {9: ep}, cfg)["problems"] == problems


def test_quotes_are_found_when_short_or_missing_an_aside(ep):
    from promofinder.qc import quote_block

    assert quote_block("Thank you.", ep["blocks"])["text"] == "Thank you."            # a short line, said exactly
    # M5 naive run: the model dropped an aside ("—The Bearded Stranger—") from a camera line
    assert quote_block("The two women embrace, smiling warmly.", ep["blocks"])["block_id"] == "E009-B014"
    assert quote_block("She burns the letter in the fire.", ep["blocks"]) is None
    assert quote_block("Yes.", ep["blocks"]) is None


def test_naive_times_that_match_no_block_edge_are_counted(ep, cfg):
    promo = {"segments": [{"episode": 9, "start": "00:16.00", "end": "00:40.00"}], "hook_quote": "x", "cliffhanger_quote": "y"}
    assert check_naive(promo, {9: ep}, cfg)["times_matching_no_block_edge"] == 1     # 16.0 is mid-shot; 40.0 a shot edge


def test_a_join_that_opens_on_a_reply_to_a_cut_question_is_found():
    from promofinder.qc import reply_to_a_cut_line

    def line(start, end, who, text):
        return {"kind": "DIALOGUE", "start_s": start, "end_s": end, "text": text, "speaker": {"label": who}}

    ep = {"blocks": [line(5.0, 8.0, "MIRA", "The tea was cold."), line(20.0, 21.5, "MIRA", "Why are you shouting at me?"),
                     line(22.0, 25.0, "ALEX", "Because you never told me."), line(26.0, 28.0, "MIRA", "I was afraid.")]}
    promo = {"segments": [_seg(4.0, 12.0), _seg(22.0, 34.0)]}            # Episode 25's #7 in miniature
    q, a = reply_to_a_cut_line(promo, {9: ep})
    assert (q["text"], a["text"]) == ("Why are you shouting at me?", "Because you never told me.")
    assert reply_to_a_cut_line({"segments": [_seg(4.0, 12.0), _seg(19.5, 34.0)]}, {9: ep}) is None   # question kept
    assert reply_to_a_cut_line({"segments": [_seg(4.0, 12.0), _seg(25.5, 34.0)]}, {9: ep}) is None   # not a reply


@pytest.fixture(scope="module")
def small_run(tmp_path_factory, cfg):
    if not any(INPUT.glob("*.txt")):
        pytest.skip("input pack not present")
    out = tmp_path_factory.mktemp("qc") / "run"
    run(INPUT, out, judge_spec="offline", episodes_only={1, 2})
    return out


def test_the_blind_test_writes_one_verdict_per_promo(small_run, cfg):
    cfg = {**cfg, "qc": {**cfg["qc"], "blind_top": 3, "blind_control": 2}}

    def viewer(user, schema):
        assert list(schema["properties"]) == ["conflict", "open_question", "confusing", "standalone", "follow", "tap"]
        return {"conflict": "A bride against her family.", "open_question": "Will she fight back?",
                "confusing": ["the ledger"] if "ledger" in user.lower() else [],
                "standalone": 3 if "ledger" in user.lower() else 4, "follow": "yes", "tap": "yes"}

    client = FakeClient(viewer)
    out = run_blind_test(small_run, client, cfg)
    saved = json.loads((small_run / "qc" / "blind.json").read_text(encoding="utf-8"))
    assert saved["results"] == out["results"] and len(out["results"]) == len(client.calls) >= 5
    assert {r["verdict"] for r in out["results"]} <= {"clear", "gaps"}
    assert not any("E001-B" in user or "E002-B" in user for _, user, _ in client.calls)
    report = qc_markdown(small_run, cfg)
    assert "## 6. Blind-viewer test" in report and "| Top 10 |" in report


def test_the_naive_baseline_sends_raw_screenplays_and_checks_every_promo(small_run, cfg):
    from promofinder.qc import run_naive_baseline

    def naive(user, schema):
        assert user.startswith("=== episode-001") and "=== episode-002" in user and "=== episode-003" not in user
        return {"promos": [
            {"segments": [{"episode": 1, "start": "00:00.00", "end": "00:30.00"}], "hook_quote": "nothing like this",
             "cliffhanger_quote": "nor this", "why": "x"},
            {"segments": [{"episode": 2, "start": "9:99", "end": "00:30.00"}], "hook_quote": "a", "cliffhanger_quote": "b",
             "why": "y"}]}

    out = run_naive_baseline(small_run, INPUT, FakeClient(naive), cfg, "1-2")
    assert out["episodes"] == [1, 2] and out["error"] is None and len(out["promos"]) == 2
    assert "hook not in the script" in out["promos"][0]["problems"] and out["promos"][1]["problems"] == ["unreadable time"]
    assert json.loads((small_run / "qc" / "naive.json").read_text(encoding="utf-8"))["promos"] == out["promos"]
    assert "Naive one-prompt baseline" in qc_markdown(small_run, cfg)


def test_the_report_has_every_check_and_the_cli_writes_it(small_run, cfg):
    report = qc_markdown(small_run, cfg, gold_path=Path(__file__).resolve().parents[1] / "eval" / "gold_moments.toml")
    for heading in ("## 1. Hard checks", "## 3. Gold set", "## 4. Silver labels", "## 5. Would I post this?",
                    "## 9. Failure gallery"):
        assert heading in report
    assert "0 ranked promos fail" in report and "not reviewed yet" in report
    assert main(["qc", str(small_run), "--review", str(small_run / "none.toml")]) == 0
    assert (small_run / "qc_report.md").read_text(encoding="utf-8").startswith("# Quality report")
