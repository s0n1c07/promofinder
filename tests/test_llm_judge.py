"""The LLM judge with a scripted model: prompt, allowed IDs, blending, grounding, proposals, fallback, cache."""

import json
import re
from pathlib import Path

import pytest

from promofinder.config import load_config, resolve
from promofinder.judges.llm import LLMJudge, build_prompt, clean_why, normalise, unverified_quotes
from promofinder.lexicon import Lexicon
from promofinder.llm.cache import CachedClient
from promofinder.pipeline import run
from promofinder.signals import episode_signals
from promofinder.timeline import cut_points
from promofinder.validate import block_index, check_promo
from promofinder.windows import build_window, episode_candidates

from .fake_llm import FakeClient, default_reply
from .screenplay import synthetic_episode

INPUT = Path(__file__).resolve().parents[1] / "Input"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def lex(cfg):
    return Lexicon.load(resolve(cfg["lexicon"]["path"]))


@pytest.fixture()
def episode(tmp_path, lex, cfg):
    ep = synthetic_episode(tmp_path)
    sig = episode_signals(ep, lex)
    cuts = cut_points(ep)
    cands, _ = episode_candidates(ep, sig, cuts, lex, cfg)
    return ep, sig, cuts, cands


def test_prompt_shows_every_block_and_only_legal_choices(episode, cfg):
    ep, _, _, cands = episode
    user, schema, clips = build_prompt(ep, cands, cfg)
    assert "B004 10.2-12 [action] Lady Vera slaps Mira across the face." in user
    assert "B005 12.2-14 MIRA (Shocked, Trembling): You struck me!" in user
    assert user.count("\nB0") == len(ep["blocks"])
    for cid, cand in clips.items():
        seg = cand.segments[0]
        props = schema["properties"]["clips"]["properties"][cid]["properties"]
        starts = {b["block_id"][5:]: b for b in ep["blocks"]}
        assert all(0 <= starts[b]["start_s"] - seg.start_s <= 3.05 for b in props["hook_block"]["enum"])
        assert all(seg.start_s <= starts[b]["start_s"] < seg.end_s and starts[b]["end_s"] >= seg.end_s - 6.05
                   for b in props["cliff_block"]["enum"])


def test_reply_maps_back_to_script_ids_and_blends_scores(episode, cfg):
    ep, sig, _, cands = episode
    verdict = LLMJudge(FakeClient(), cfg).judge_episode(ep, cands, sig)
    assert len(verdict.judgements) == len(cands) and not verdict.errors
    index = block_index({9: ep})
    for j, cand in zip(verdict.judgements, cands):
        assert j.hook_block.startswith("E009-B") and j.cliff_block.startswith("E009-B")
        assert j.hook_score == round(0.7 * 1.0 + 0.3 * cand.rule_scores["hook"], 3)
        assert j.cliff_score == round(0.7 * 0.75 + 0.3 * cand.rule_scores["cliffhanger"], 3)
        assert "spoiler" in j.flags and j.detail["model_scores"] == {"hook": 5, "cliff": 4, "standalone": 3}
        assert check_promo(cand.segments, j.hook_block, j.cliff_block, {9: ep}, index, cfg) == []
    assert verdict.calls[0]["ok"] and verdict.raw["response_text"]


def test_quotes_in_the_why_line_are_checked_against_the_script(episode, cfg):
    ep, sig, _, cands = episode
    text = normalise(" | ".join(b["text"] for b in ep["blocks"]))
    assert unverified_quotes('Opens on "You struck me!" in two seconds.', text) == []
    assert unverified_quotes('Ends on "I will burn your house down."', text) == ["I will burn your house down."]
    # single quotes are checked too; apostrophes inside words are not quote marks
    assert unverified_quotes("Lady Vera's 'You are nothing but a servant' lands hard.", text) == []
    assert unverified_quotes("Mira's 'I can't breathe' is invented.", text) == ["I can't breathe"]
    assert unverified_quotes("Vera’s ‘Then you will hang before dawn’ chills.", text) == []

    def invented(user, schema):
        reply = default_reply(user, schema)
        for v in reply["clips"].values():
            v["why"] = 'She screams "I will burn your house down." before the cut.'
        return reply

    j = LLMJudge(FakeClient(invented), cfg).judge_episode(ep, cands, sig).judgements[0]
    assert j.detail["unverified_quotes"] == ["I will burn your house down."]


def test_quotes_from_another_part_of_the_episode_are_flagged(episode, cfg):
    ep, sig, _, cands = episode

    def elsewhere(user, schema):
        reply = default_reply(user, schema)
        for v in reply["clips"].values():
            v["why"] = "Ends on 'Thank you.' as they embrace."   # spoken at 50 s, after the clips end
        return reply

    for j, cand in zip(LLMJudge(FakeClient(elsewhere), cfg).judge_episode(ep, cands, sig).judgements, cands):
        inside = cand.segments[0].start_s <= 50.0 < cand.segments[0].end_s
        assert j.detail["unverified_quotes"] == []
        assert j.detail["quotes_outside_clip"] == ([] if inside else ["Thank you."])


def test_long_why_lines_are_cut():
    text, cut = clean_why(" ".join(["word"] * 30), 25)
    assert cut and text.endswith("…") and len(text.split()) == 25


@pytest.mark.parametrize("junk", [
    ",",                                                                    # M4 run, first try
    '{"cliff": 5, "standalone": 4, "trope": "danger", "why": "Duchess demands the',  # M4 run, second try
    "Her secret",
])
def test_a_reply_that_is_not_a_question_is_left_out(episode, cfg, junk):
    ep, sig, _, cands = episode

    def reply(user, schema):
        data = default_reply(user, schema)
        for v in data["clips"].values():
            v["question"] = junk
        return data

    j = LLMJudge(FakeClient(reply), cfg).judge_episode(ep, cands, sig).judgements[0]
    assert j.open_question is None and j.detail["question_dropped"]
    ok = LLMJudge(FakeClient(), cfg).judge_episode(ep, cands, sig).judgements[0]
    assert ok.open_question == "Who told her?" and not ok.detail["question_dropped"]


def test_each_prompt_lists_the_reply_fields_in_schema_order(episode, cfg):
    """montage_v1 described the fields in another order than the schema forces, and the model wrote
    the rest of its reply into the question string."""
    from promofinder.judges.llm import build_montage_prompt

    ep, _, _, cands = episode
    _, schema, clips = build_prompt(ep, cands, cfg)
    clip_fields = list(schema["properties"]["clips"]["properties"][next(iter(clips))]["properties"])
    menu = {9: [{"block_id": "E009-B004", "opens": 5, "ends": 0}, {"block_id": "E009-B011", "opens": 0, "ends": 4}]}
    _, mschema = build_montage_prompt(menu, {9: ep}, max_montages=3)
    montage_fields = list(mschema["properties"]["montages"]["items"]["properties"])
    for key, fields in (("prompt", clip_fields), ("montage_prompt", montage_fields)):
        text = resolve(cfg["judge"][key]).read_text(encoding="utf-8")
        assert [f for f in re.findall(r"^- (\w+)", text, re.M) if f in fields] == fields, key


def test_a_proposal_becomes_a_legal_window(episode, lex, cfg):
    ep, sig, cuts, _ = episode
    cand, why_not = build_window(ep, sig, cuts, lex, cfg, "E009-B004", "E009-B011", "E009-L01")
    assert why_not == "" and cand.source == "llm"
    seg = cand.segments[0]
    assert (seg.start_s, seg.end_s) == (10.0, 34.0)  # clean shot change 0.2 s before the slap; cut after the question
    assert check_promo(cand.segments, "E009-B004", "E009-B011", {9: ep}, block_index({9: ep}), cfg) == []


@pytest.mark.parametrize("hook, cliff, reason", [
    ("E009-B004", "E009-B005", "hook and cliffhanger are too close"),
    ("E009-B011", "E009-B004", "cliffhanger does not come after the hook"),
    ("E009-B002", "E009-B017", "too long"),
    ("E009-B004", "E009-B999", "unknown block"),
])
def test_proposals_that_cannot_work_are_explained(episode, lex, cfg, hook, cliff, reason):
    ep, sig, cuts, _ = episode
    cand, why_not = build_window(ep, sig, cuts, lex, cfg, hook, cliff, "E009-L01")
    assert cand is None and why_not.startswith(reason)


def test_a_failed_call_falls_back_to_the_rules(episode, cfg):
    ep, sig, _, cands = episode
    verdict = LLMJudge(FakeClient(fail="connection reset"), cfg).judge_episode(ep, cands, sig)
    assert verdict.errors == ["connection reset"] and verdict.calls[0]["ok"] is False
    assert all(j.judge.startswith("offline (fallback") for j in verdict.judgements)
    assert [j.hook_score for j in verdict.judgements] == [c.rule_scores["hook"] for c in cands]


def test_a_dead_server_stops_the_run_instead_of_falling_back(episode, cfg):
    from promofinder.llm.base import LLMUnavailable

    class DeadServer(FakeClient):
        def complete_json(self, system, user, schema):
            raise LLMUnavailable("cannot reach Ollama")

    ep, sig, _, cands = episode
    with pytest.raises(LLMUnavailable):
        LLMJudge(DeadServer(), cfg).judge_episode(ep, cands, sig)


def test_a_reply_that_breaks_the_schema_falls_back(episode, cfg):
    ep, sig, _, cands = episode

    def off_scale(user, schema):
        reply = default_reply(user, schema)
        next(iter(reply["clips"].values()))["hook"] = 9
        return reply

    verdict = LLMJudge(FakeClient(off_scale), cfg).judge_episode(ep, cands, sig)
    assert "breaks the schema" in verdict.errors[0]
    assert verdict.raw["response_text"]  # the bad reply is kept for inspection


def test_the_cache_answers_a_repeated_call_once(tmp_path):
    inner = FakeClient(reply=lambda user, schema: {"echo": user})
    cached = CachedClient(inner, tmp_path / "cache")
    first = cached.complete_json("sys", "episode 1", {"type": "object"})
    again = cached.complete_json("sys", "episode 1", {"type": "object"})
    other = cached.complete_json("sys", "episode 2", {"type": "object"})
    assert len(inner.calls) == 2 and (cached.hits, cached.misses) == (1, 2)
    assert again.cached and again.data == first.data and again.compute_seconds == first.compute_seconds
    assert other.data == {"echo": "episode 2"}


@pytest.mark.skipif(not any(INPUT.glob("*.txt")), reason="input pack not present")
def test_pipeline_with_a_scripted_model(tmp_path, cfg):
    out = tmp_path / "run"
    meta = run(INPUT, out, judge=LLMJudge(FakeClient(), cfg), episodes_only={1, 2, 50})
    promos = json.loads((out / "promos.json").read_text(encoding="utf-8"))
    checks = json.loads((out / "hard_checks.json").read_text(encoding="utf-8"))
    assert checks["final_promos_failing_recheck"] == [] and promos
    assert {p["judge"] for p in promos} == {"fake:judge"}
    assert all(p["cliffhanger"]["question_by"] == "model" for p in promos)
    report = (out / "promos_ranked.md").read_text(encoding="utf-8")
    assert "Model said (1–5): hook 5" in report and "the viewer is left asking: Who told her?" in report
    assert (out / "llm_raw" / "E050.json").exists() and (out / "llm_raw" / "system_prompt.md").exists()
    assert meta["cost"]["llm_calls"] == 4 and meta["llm"]["episodes_failed"] == []   # 3 episodes + 1 montage call
    assert (out / "llm_raw" / "montages_1.json").exists() and meta["llm"]["montages"]["proposed"] == 1
    # Episode 50 has no rule candidate: the scripted model proposes one, and code builds or explains it
    log = meta["llm"]["proposals"]["log"]
    assert [p["episode"] for p in log] == [50]
    assert log[0]["status"] == "built" or log[0]["reason"]


def test_a_bridge_clip_shows_the_previous_episode_and_maps_back(tmp_path, lex, cfg):
    from promofinder.multiseg import build_parts, make_candidate
    from promofinder.windows import _Index

    from .screenplay import synthetic_pair

    ep9, ep10 = synthetic_pair(tmp_path)
    eps = {9: ep9, 10: ep10}
    sigs = {**episode_signals(ep9, lex), **episode_signals(ep10, lex)}
    cuts = {n: cut_points(ep) for n, ep in eps.items()}
    parts, _ = build_parts(eps, cuts, ["E009-B013", "E010-B004"], cfg, bridge=True)
    bridge = make_candidate(parts, {n: _Index(ep, sigs, lex) for n, ep in eps.items()}, cfg, "E010-X01",
                            "bridge", "rules")
    user, schema, clips = build_prompt(ep10, [bridge], cfg, prev=ep9)
    assert user.startswith("EARLIER: the end of Episode 9") and "C1, 2 parts: Episode 9 40-70 s, then 0-" in user
    hook_ids = schema["properties"]["clips"]["properties"]["C1"]["properties"]["hook_block"]["enum"]
    assert hook_ids and all(h.startswith("E009-B") for h in hook_ids)     # other episode: full IDs
    j = LLMJudge(FakeClient(), cfg).judge_episode(ep10, [bridge], sigs, prev=ep9).judgements[0]
    assert j.hook_block.startswith("E009-") and j.cliff_block.startswith("E010-")
    assert check_promo(bridge.segments, j.hook_block, j.cliff_block, eps, block_index(eps), cfg) == []


def test_montages_come_from_the_beats_the_model_rated_highest(tmp_path, lex, cfg):
    from promofinder.judges import Judgement
    from promofinder.judges.llm import beat_menu, build_montage_prompt

    from .screenplay import synthetic_pair

    ep9, ep10 = synthetic_pair(tmp_path)

    def judged(cid, hook, cliff, h, c):
        return Judgement(cid, "fake", hook, 0.5, cliff, 0.5, 0.5, "", "danger",
                         detail={"model_scores": {"hook": h, "cliff": c, "standalone": 3}})

    menu = beat_menu([judged("a", "E009-B004", "E009-B011", 5, 4), judged("b", "E010-B007", "E010-B009", 3, 5),
                      judged("c", "E010-B002", "E010-B003", 2, 2)], per_episode=4)
    assert {b["block_id"] for beats in menu.values() for b in beats} == {"E009-B004", "E009-B011", "E010-B009"}
    user, schema = build_montage_prompt(menu, {9: ep9, 10: ep10}, max_montages=3)
    assert "E009-B004 at 0:10.2 (opens 5/5) [action] Lady Vera slaps Mira" in user
    proposals = LLMJudge(FakeClient(), cfg).propose_montages(menu, {9: ep9, 10: ep10}, "test").proposals
    assert [p["beats"] for p in proposals] == [["E009-B004", "E010-B009"]]


def test_a_proposal_too_late_to_build_still_reaches_the_montage_menu():
    from promofinder.judges.llm import beat_menu

    # M4 run: Episode 50's discovery (hook 5, cliffhanger 5) can't open a 20-s window, so the proposal
    # was not built, and its beats never reached the montage call
    late = {"hook_block": "E050-B036", "cliff_block": "E050-B040", "reply": {"hook": 5, "cliff": 5}}
    weak = {"hook_block": "E050-B010", "cliff_block": "E050-B012", "reply": {"hook": 3, "cliff": 2}}
    assert beat_menu([], per_episode=4, proposals=[late, weak]) == {50: [
        {"block_id": "E050-B036", "opens": 5, "ends": 0}, {"block_id": "E050-B040", "opens": 0, "ends": 5}]}


@pytest.mark.skipif(not any(INPUT.glob("*.txt")), reason="input pack not present")
def test_a_moment_too_late_for_one_window_can_end_a_montage(tmp_path, cfg):
    from .fake_llm import _BLOCK

    def late_discovery(user, schema):
        reply = default_reply(user, schema)
        if "clips" in schema["properties"] and not reply["clips"]:      # Episode 50 has no rule candidate
            ids = [b for b, _, _ in _BLOCK.findall(user)]
            reply["missed"] = [reply["missed"][0] | {"hook_block": ids[-2], "cliff_block": ids[-1]}]
        return reply

    out = tmp_path / "run"
    meta = run(INPUT, out, judge=LLMJudge(FakeClient(late_discovery), cfg), episodes_only={49, 50})
    assert [(p["episode"], p["status"]) for p in meta["llm"]["proposals"]["log"]] == [(50, "not built")]
    promos = json.loads((out / "promos.json").read_text(encoding="utf-8"))
    montages = [p for p in promos if p["kind"] == "montage"]
    assert montages and montages[0]["episodes"] == [49, 50] and montages[0]["segments"][-1]["episode"] == 50
