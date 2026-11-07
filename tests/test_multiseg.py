"""Multi-segment promos: parts built around beats, bridges, rule scores and duplicate folding."""

import pytest

from promofinder.config import load_config, resolve
from promofinder.lexicon import Lexicon
from promofinder.multiseg import (bridge_candidates, build_parts, is_direct_link, make_candidate, score_parts,
                                  tightened_candidates)
from promofinder.rank import same_promo
from promofinder.signals import episode_signals
from promofinder.timeline import cut_points
from promofinder.validate import block_index, check_promo
from promofinder.windows import Segment, _Index, episode_candidates

from .screenplay import synthetic_pair


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def lex(cfg):
    return Lexicon.load(resolve(cfg["lexicon"]["path"]))


@pytest.fixture()
def show(tmp_path, lex):
    ep9, ep10 = synthetic_pair(tmp_path)
    eps = {9: ep9, 10: ep10}
    sigs = {**episode_signals(ep9, lex), **episode_signals(ep10, lex)}
    return eps, sigs, {n: cut_points(ep) for n, ep in eps.items()}


def _valid(parts, hook, cliff, eps, cfg):
    segs = [Segment(n, a.t, b.t) for n, a, b in parts]
    return check_promo(segs, hook, cliff, eps, block_index(eps), cfg)


def test_two_moments_of_one_episode_become_two_parts(show, cfg):
    eps, _, cuts = show
    parts, why = build_parts(eps, cuts, ["E009-B004", "E009-B015"], cfg)   # the slap, then "Thank you."
    assert why == "" and len(parts) == 2
    (_, in1, out1), (_, in2, out2) = parts
    assert in1.t == 10.0                                  # clean shot change 0.2 s before the slap
    assert out1.t < in2.t                                 # the middle is cut out
    assert in2.t <= 50.0 <= out2.t and out2.t >= 53.0    # the ending beat plays in full
    assert 20 <= (out1.t - in1.t) + (out2.t - in2.t) <= 60
    assert _valid(parts, "E009-B004", "E009-B015", eps, cfg) == []


@pytest.mark.parametrize("beats, reason", [
    (["E009-B015", "E009-B004"], "beats are not in story order"),
    (["E009-B004", "E009-B005"], "the beats fit one continuous window"),
    (["E009-B004", "E009-B999"], "unknown block"),
    (["E009-B004"], "needs 2 to 3 beats"),
])
def test_impossible_joins_are_explained(show, cfg, beats, reason):
    eps, _, cuts = show
    parts, why = build_parts(eps, cuts, beats, cfg)
    assert parts is None and why.startswith(reason)


def test_a_bridge_runs_to_the_end_of_one_episode_and_from_the_start_of_the_next(show, cfg):
    eps, _, cuts = show
    assert is_direct_link(eps[9], eps[10], cfg)
    parts, why = build_parts(eps, cuts, ["E009-B013", "E010-B004"], cfg, bridge=True)
    assert why == ""
    (n1, in1, out1), (n2, in2, out2) = parts
    assert (n1, n2) == (9, 10) and out1.t == eps[9]["duration_s"] and in2.t == 0.0
    assert _valid(parts, "E009-B013", "E010-B004", eps, cfg) == []


def test_bridge_candidates_are_legal(show, lex, cfg):
    eps, sigs, cuts = show
    for cand in bridge_candidates(eps[9], eps[10], sigs, cuts, lex, cfg):
        assert cand.kind == "bridge" and [s.episode for s in cand.segments] == [9, 10]
        assert check_promo(cand.segments, cand.hook_block, cand.cliff_block, eps, block_index(eps), cfg) == []


def test_joined_parts_pay_for_the_jump(show, lex, cfg):
    eps, sigs, cuts = show
    parts, _ = build_parts(eps, cuts, ["E009-B004", "E009-B015"], cfg)
    idxs = {9: _Index(eps[9], sigs, lex)}
    r = score_parts(parts, idxs, cfg)
    assert r["features"]["parts"] == 2 and 0 <= r["scores"]["total"] <= 1
    assert r["scores"]["standalone"] <= 1.0 - cfg["multiseg"]["jump_penalty"]
    cand = make_candidate(parts, idxs, cfg, "E009-T01", "tightened", "rules")
    assert cand.duration_s == round(sum(b.t - a.t for _, a, b in parts), 2)


def test_tightened_cuts_are_legal_and_new(show, lex, cfg):
    eps, sigs, cuts = show
    singles, _ = episode_candidates(eps[9], sigs, cuts[9], lex, cfg)
    for cand in tightened_candidates(eps[9], sigs, cuts[9], lex, cfg, singles):
        assert len(cand.segments) == 2
        assert check_promo(cand.segments, cand.hook_block, cand.cliff_block, eps, block_index(eps), cfg) == []
        assert not any(same_promo(cand.segments, s.segments, 0.3) for s in singles)


def test_a_later_part_opens_on_the_question_not_on_its_answer(tmp_path, cfg):
    from promofinder.timeline import reply_opening

    from .screenplay import HALL, custom_episode

    ep = custom_episode(tmp_path, 11, HALL, "01:00.00")
    eps, cuts = {11: ep}, {11: cut_points(ep)}
    slam = next(b["block_id"] for b in ep["blocks"] if b["text"].startswith("Lord Brand slams"))
    threat = next(b["block_id"] for b in ep["blocks"] if b["text"].startswith("Then you will hang"))
    parts, why = build_parts(eps, cuts, [slam, threat], cfg)
    assert why == "" and len(parts) == 2
    start = parts[1][1].t
    assert start <= 24.0 + 0.05 and reply_opening(ep, start) is None       # "Why are you shouting at me?" is kept
    off = {**cfg, "multiseg": {**cfg["multiseg"], "reply_gap_s": -1.0}}   # the fix switched off
    unfixed, _ = build_parts(eps, cuts, [slam, threat], off)
    assert reply_opening(ep, unfixed[1][1].t)["text"] == "Why are you shouting at me?"   # growth alone opens on the answer


def test_a_join_is_dropped_when_the_question_sits_against_the_first_part(tmp_path, cfg):
    from promofinder.multiseg import start_at_questions

    from .screenplay import HALL, custom_episode

    ep = custom_episode(tmp_path, 11, HALL, "01:00.00")
    cuts = cut_points(ep)
    at = {round(c.t, 2): c for c in cuts}
    parts = [[11, at[0.0], at[6.0]], [11, at[26.5], at[36.0]]]
    assert start_at_questions(parts, {11: ep}, {11: cuts}, cfg["multiseg"]) == "" and parts[1][1].t == 24.0
    parts = [[11, at[0.0], at[24.0]], [11, at[26.5], at[36.0]]]      # the question sits in a 2.5 s gap
    assert start_at_questions(parts, {11: ep}, {11: cuts}, cfg["multiseg"]).startswith("a later part would open")


def test_duplicates_must_share_both_the_opening_and_the_ending():
    single = [Segment(1, 10.0, 40.0)]
    tightened = [Segment(1, 10.0, 18.0), Segment(1, 60.0, 75.0)]   # same opening, different ending
    assert not same_promo(single, tightened, 0.3)
    assert same_promo(single, [Segment(1, 12.0, 41.0)], 0.3)
    assert same_promo(tightened, [Segment(1, 10.5, 19.0), Segment(1, 61.0, 76.0)], 0.3)
