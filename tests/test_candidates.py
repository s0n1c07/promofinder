"""Cut points, window scoring, hard checks and ranking on a synthetic episode."""

import pytest

from promofinder.config import load_config, resolve
from promofinder.judges import Judgement
from promofinder.judges.offline import OfflineJudge
from promofinder.lexicon import Lexicon
from promofinder.rank import rank_promos
from promofinder.signals import episode_signals
from promofinder.timeline import cut_points, inside_line
from promofinder.validate import block_index, check_promo
from promofinder.windows import Candidate, Segment, episode_candidates, overlap

from .screenplay import synthetic_episode


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def lex(cfg):
    return Lexicon.load(resolve(cfg["lexicon"]["path"]))


@pytest.fixture()
def ep(tmp_path):
    return synthetic_episode(tmp_path)


def test_cut_points_never_fall_inside_a_line(ep):
    cuts = cut_points(ep)
    times = [c.t for c in cuts]
    assert 0.0 in times and ep["duration_s"] in times
    assert 32.0 not in times  # not a boundary at all
    assert all(not inside_line(ep, t) for t in times)
    by_t = {c.t: c for c in cuts}
    assert by_t[10.0].shot_boundary and not by_t[10.2].shot_boundary
    assert 13.0 not in times and 12.2 in times  # 12.2 is where a line starts: a boundary, so safe


def test_best_window_opens_on_the_slap_and_withholds_the_answer(ep, lex, cfg):
    cands, scored = episode_candidates(ep, episode_signals(ep, lex), cut_points(ep), lex, cfg)
    assert scored > len(cands) > 0
    best = cands[0].segments[0]
    assert 7.2 <= best.start_s <= 10.2   # the slap (10.2 s) is on screen, not cut off
    assert 27.0 <= best.end_s <= 41.0    # after the threat, before the answer at 41 s
    assert not any(c.segments[0].start_s > 10.2 and c.segments[0].start_s < 13.0 for c in cands[:1])


def test_starting_on_the_reaction_is_penalised(ep, lex, cfg):
    """A cut at 12.0 s opens on "You struck me!" but skips the slap at 10.2 s."""
    sig = episode_signals(ep, lex)
    cands, _ = episode_candidates(ep, sig, cut_points(ep), lex, cfg)
    late = [c for c in cands if 10.2 < c.segments[0].start_s <= 13.0]
    assert all(c.features["skipped_hook"] >= 0.6 for c in late)
    assert all(c.rule_scores["total"] < cands[0].rule_scores["total"] for c in late)


def test_every_candidate_passes_the_hard_checks(ep, lex, cfg):
    episodes = {ep["episode_number"]: ep}
    cands, _ = episode_candidates(ep, episode_signals(ep, lex), cut_points(ep), lex, cfg)
    for c in cands:
        assert check_promo(c.segments, c.hook_block, c.cliff_block, episodes, block_index(episodes), cfg) == []
        assert 20 <= c.duration_s <= 60
    best = [c for c in cands if not c.features["extra_anchor"]]
    for a in best:
        for b in best:
            if a is not b:
                assert overlap(a.segments[0], b.segments[0]) <= cfg["candidates"]["max_overlap"]


def test_every_strong_opening_gets_its_own_window(ep, lex, cfg):
    """Windows kept only for their opening may overlap others, but each opens on a strong hook
    that no other window shows in its first 3 s."""
    sig = episode_signals(ep, lex)
    cands, _ = episode_candidates(ep, sig, cut_points(ep), lex, cfg)
    extra = [c for c in cands if c.features["extra_anchor"]]
    assert extra and len(extra) <= cfg["candidates"]["max_extra_anchors"]
    for c in extra:
        assert sig[c.hook_block].hook >= cfg["candidates"]["anchor_min_hook"]
        others = [o for o in cands if o is not c]
        hook_start = next(b["start_s"] for b in ep["blocks"] if b["block_id"] == c.hook_block)
        assert not any(o.segments[0].start_s <= hook_start <= o.segments[0].start_s + 3.0 for o in others)


@pytest.mark.parametrize(
    "segments, hook, cliff, broken",
    [
        ([Segment(9, 10.0, 40.0)], "E009-B004", "E009-B011", []),
        ([Segment(9, 10.0, 22.0)], "E009-B004", "E009-B008", ["duration"]),
        ([Segment(9, 10.0, 25.0)], "E009-B004", "E009-B009", ["duration", "safe_cuts"]),
        ([Segment(9, 13.0, 40.0)], "E009-B004", "E009-B011", ["hook_onset", "safe_cuts"]),
        ([Segment(9, 10.0, 32.0)], "E009-B004", "E009-B009", ["safe_cuts"]),
        ([Segment(9, 30.0, 55.0), Segment(9, 10.0, 14.0)], "E009-B010", "E009-B004", ["story_order"]),
        ([Segment(9, 10.0, 40.0)], "E009-B004", "E009-B999", ["ids_exist"]),
        ([Segment(9, 10.0, 40.0)], "E009-B004", "E009-B013", ["cliff_inside"]),
        ([Segment(9, 10.0, 12.0), Segment(9, 14.0, 40.0)], "E009-B004", "E009-B011", ["segment_bounds"]),
    ],
)
def test_hard_rules(ep, cfg, segments, hook, cliff, broken):
    episodes = {9: ep}
    assert check_promo(segments, hook, cliff, episodes, block_index(episodes), cfg) == broken


def _item(cid, start, end, final, trope="betrayal", episode=1):
    c = Candidate(cid, "single", [Segment(episode, start, end)], "x", "y", {"pacing": 0.5, "length": 1.0})
    j = Judgement(cid, "test", "x", 0.0, "y", 0.0, 0.0, "why", trope)
    return {"candidate": c, "judgement": j,
            "scores": {"hook": final, "cliffhanger": final, "standalone": final, "pacing": final, "length": final}}


def test_ranking_folds_duplicates_and_spreads_episodes(cfg):
    cfg = {**cfg, "rank": {**cfg["rank"], "funnel_bonus": 0.0}}
    items = [
        _item("A", 0, 30, 0.90, episode=1),
        _item("A-twin", 5, 35, 0.89, episode=1),   # overlaps A by 83% -> duplicate
        _item("B", 40, 70, 0.88, episode=1),       # same episode, no overlap
        _item("C", 0, 30, 0.86, trope="reveal", episode=2),
    ]
    ranked, dupes = rank_promos(items, n_episodes=2, cfg=cfg)
    assert [d["candidate"].candidate_id for d in dupes] == ["A-twin"]
    assert dupes[0]["duplicate_of"] == "A"
    # C beats B: B is penalised for a second slot in episode 1 and for a repeated trope
    assert [r["candidate"].candidate_id for r in ranked] == ["A", "C", "B"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]


def test_flagged_promos_pay_their_penalty(cfg):
    cfg = {**cfg, "rank": {**cfg["rank"], "funnel_bonus": 0.0}}
    items = [_item("A", 0, 30, 0.90, episode=1), _item("B", 0, 30, 0.90, trope="reveal", episode=2)]
    items[0]["judgement"].flags = ["graphic", "spoiler", "context_needed"]   # context_needed costs nothing
    ranked, _ = rank_promos(items, n_episodes=2, cfg=cfg)
    assert [r["candidate"].candidate_id for r in ranked] == ["B", "A"]
    assert ranked[1]["scores"]["penalty"] == 0.1 and ranked[1]["scores"]["final"] == 0.8
    assert "0.10 taken off for graphic content (ad-policy check) and a spoiler" in ranked[1]["rank_reason"]


def test_posting_plan_puts_the_premise_first_and_spoilers_last(cfg):
    from promofinder.rank import posting_plan

    def promo(rank, episode, flags=()):
        return {"rank": rank, "segments": [{"episode": episode}], "flags": list(flags)}

    promos = [promo(1, 41), promo(2, 3, ["spoiler"]), promo(3, 12), promo(4, 1), promo(5, 2), promo(6, 4)]
    plan = posting_plan(promos, 50, cfg)
    assert [[p["rank"] for p in tier] for _, tier in plan] == [[4, 5, 6], [3], [1, 2]]
    assert [name.split(":")[0] for name, _ in plan] == ["Cold audiences", "Escalation (Episodes 6–35, no spoilers)",
                                                        "Re-engage viewers who started"]


def test_offline_judge_reports_rule_scores(ep, lex, cfg):
    sig = episode_signals(ep, lex)
    cands, _ = episode_candidates(ep, sig, cut_points(ep), lex, cfg)
    j = OfflineJudge().judge_episode(ep, cands, sig).judgements[0]
    assert j.hook_block == cands[0].hook_block and j.hook_score == cands[0].rule_scores["hook"]
    assert j.trope == "cruelty" and j.why_it_works.startswith("Opens on a physical shock")
