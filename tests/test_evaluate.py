"""Gold and silver matching rules, on synthetic promos (the real gold set is never used to tune)."""

import json

from promofinder.evaluate import evaluate_run, labelled_beat, lenient_hit, montage_hit, strict_hit

from .screenplay import synthetic_episode


def _seg(ep, a, b):
    return {"episode": ep, "start_s": a, "end_s": b, "duration_s": b - a}


GOLD = {"id": "G01", "episode": 9, "start": "00:10.00", "end": "00:34.00",
        "hook_block": "E009-B004", "cliff_block": "E009-B011", "priority": "must"}


def test_lenient_needs_half_the_gold_window(tmp_path):
    assert lenient_hit({"segments": [_seg(9, 8.0, 24.0)]}, GOLD)        # 14 of 24 s
    assert not lenient_hit({"segments": [_seg(9, 25.0, 50.0)]}, GOLD)   # 9 of 24 s
    assert not lenient_hit({"segments": [_seg(8, 10.0, 34.0)]}, GOLD)   # other episode


def test_strict_needs_the_hook_up_front_and_the_cliffhanger_inside(tmp_path):
    blocks = {b["block_id"]: b for b in synthetic_episode(tmp_path)["blocks"]}
    assert strict_hit({"segments": [_seg(9, 10.0, 34.0)]}, GOLD, blocks)
    assert not strict_hit({"segments": [_seg(9, 6.0, 34.0)]}, GOLD, blocks)   # slap lands 4.2 s in
    assert not strict_hit({"segments": [_seg(9, 10.0, 30.0)]}, GOLD, blocks)  # question cut off


def test_montages_must_cover_each_segment_in_order():
    montage = {"segments": [{"episode": 1, "start": "00:10.00", "end": "00:20.00"},
                            {"episode": 3, "start": "00:50.00", "end": "01:10.00"}]}
    assert montage_hit({"segments": [_seg(1, 9.0, 20.0), _seg(3, 52.0, 70.0)]}, montage)
    assert not montage_hit({"segments": [_seg(3, 52.0, 70.0), _seg(1, 9.0, 20.0)]}, montage)
    assert not montage_hit({"segments": [_seg(1, 9.0, 20.0)]}, montage)


def test_the_described_beat_is_found_by_rare_shared_words_near_the_end(tmp_path):
    ep = synthetic_episode(tmp_path)
    # "thank" is rarer than the names, and the speaker's name counts for a spoken line
    assert labelled_beat(ep, "Mira whispers thank you to Lady Vera.")["block_id"] == "E009-B015"
    # notes describe the cliffhanger, so a beat outside the final third is never chosen
    assert labelled_beat(ep, "Lady Vera slaps Mira across the face.")["start_s"] >= 70 * 2 / 3


def test_a_run_folder_is_scored(tmp_path):
    ep = synthetic_episode(tmp_path)
    run = tmp_path / "run"
    (run / "parsed").mkdir(parents=True)
    (run / "parsed" / "E009.json").write_text(json.dumps(ep), encoding="utf-8")
    (run / "run_meta.json").write_text(json.dumps({"judge": "test"}), encoding="utf-8")
    promos = [{"promo_id": "P001", "rank": 1, "episodes": [9], "segments": [_seg(9, 10.0, 34.0)],
               "cliffhanger": {"block_id": "E009-B011"}}]
    (run / "promos.json").write_text(json.dumps(promos), encoding="utf-8")
    gold = tmp_path / "gold.toml"
    gold.write_text('status = "draft"\n[[moment]]\nid = "G01"\nepisode = 9\nstart = "00:10.00"\nend = "00:34.00"\n'
                    'hook_block = "E009-B004"\ncliff_block = "E009-B011"\npriority = "must"\n', encoding="utf-8")
    r = evaluate_run(run, gold)
    assert r["recall"]["must_strict"] == {"top10": "1/1", "top20": "1/1", "all": "1/1"}
    assert r["moments"][0]["strict_rank"] == 1 and r["silver"]["labelled"] == 0
