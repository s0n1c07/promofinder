"""Parser behaviour on small synthetic screenplays, one quirk per test."""

import pytest

from promofinder.parse import ParseError, parse_continuity, parse_episode, parse_heading, parse_label
from promofinder.schema import validate_episode

HEADER = (
    "EPISODE NUMBER: Episode 7\n"
    "EPISODE LOGLINE: A test logline.\n"
    "CONTINUITY NOTES: Picks up directly from Episode 6's cliffhanger where the door swung open.\n\n"
)
KEYS = ("Scene heading", "Scene start", "Scene end", "Actions and dialogue")


def block(label, start, end, *text):
    return f"**{label}:**\nStart: ({start}) | End: ({end})\n" + "".join(t + "\n" for t in text)


def scene(num, start, end, *blocks, heading="INT. TEST ROOM – NIGHT", keys=KEYS):
    head = f"SCENE {num:02d}\n{keys[0]}: {heading}\n{keys[1]}: {start}\n{keys[2]}: {end}\n\n{keys[3]}:\n\n"
    return head + "\n".join(blocks)


def script(*scenes, header=HEADER):
    return header + "--- SCRIPT START ---\n\n" + "\n".join(scenes) + "\n--- SCRIPT END ---\n"


def parse_text(tmp_path, text, name="episode-007-Episode 123.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return parse_episode(path, show_id="test")


def codes(doc):
    return [w["code"] for w in doc["warnings"]]


def test_minimal_episode(tmp_path):
    doc = parse_text(tmp_path, script(scene(
        1, "00:00.00", "00:10.00",
        block("CAMERA", "00:00.00", "00:04.00", "Wide shot of the room."),
        block("ALICE (O.S.) (Cold, Commanding)", "00:01.00", "00:02.50", '"Turn around."'),
        block("SOUND FX", "00:08.00", "00:10.00", "A sharp sting."),
    )))
    assert validate_episode(doc) == []
    assert doc["warnings"] == []
    assert doc["episode_number"] == 7
    assert doc["platform_episode_id"] == "123"
    assert doc["duration_s"] == 10.0
    assert [b["block_id"] for b in doc["blocks"]] == ["E007-B001", "E007-B002", "E007-B003"]
    assert [b["kind"] for b in doc["blocks"]] == ["CAMERA", "DIALOGUE", "SOUND_FX"]
    line = doc["blocks"][1]
    assert line["text"] == "Turn around."
    assert line["speaker"] == {
        "label": "ALICE", "character_id": None, "alias_hint": None, "voice": "O.S.", "delivery": ["Cold", "Commanding"],
    }
    assert (line["start_s"], line["end_s"], line["start_tc"]) == (1.0, 2.5, "00:01.00")
    assert doc["scenes"][0]["block_ids"] == ["E007-B001", "E007-B002", "E007-B003"]
    assert doc["header"]["follows_episode"] == 6
    assert doc["header"]["prev_cliffhanger_note"] == "the door swung open."


@pytest.mark.parametrize(
    "label, kind, speaker",
    [
        ("CAMERA", "CAMERA", None),
        ("ACTION", "ACTION", None),
        ("SOUND FX", "SOUND_FX", None),
        ("LADY VERA (Confused, Breathless)", "DIALOGUE", ("LADY VERA", None, "ON", ["Confused", "Breathless"])),
        ("LANDLADY (O.S.) (Firm)", "DIALOGUE", ("LANDLADY", None, "O.S.", ["Firm"])),
        ("THE OLD TEACHER (Off-screen) (Dismissive)", "DIALOGUE", ("THE OLD TEACHER", None, "O.S.", ["Dismissive"])),
        ("NORA (V.O.) (Low, Resonant)", "DIALOGUE", ("NORA", None, "V.O.", ["Low", "Resonant"])),
        ("STATION MANAGER (Hollis) (Cold, Calculating)", "DIALOGUE",
         ("STATION MANAGER", "Hollis", "ON", ["Cold", "Calculating"])),
        ("THE OLD TEACHER (Reverent toward Maya)", "DIALOGUE",
         ("THE OLD TEACHER", None, "ON", ["Reverent toward Maya"])),
        ("MAYA", "DIALOGUE", ("MAYA", None, "ON", [])),
    ],
)
def test_labels(label, kind, speaker):
    got_kind, got = parse_label(label)
    assert got_kind == kind
    if speaker is None:
        assert got is None
    else:
        assert (got["label"], got["alias_hint"], got["voice"], got["delivery"]) == speaker


def test_empty_speaker_label_is_rejected():
    with pytest.raises(ValueError):
        parse_label("(O.S.) (Firm)")


@pytest.mark.parametrize(
    "heading, expected",
    [
        ("INT. RADIO STATION – DAY", ("INT", "RADIO STATION", "DAY")),
        ("EXT. CITY PARK - ROSE GARDEN – DUSK", ("EXT", "CITY PARK - ROSE GARDEN", "DUSK")),
        ("INT. DINER - KITCHEN / EXT. DINER - CONTINUOUS", ("INT/EXT", "DINER - KITCHEN / EXT. DINER", "CONTINUOUS")),
        ("EXT. TRAIN STATION – PRE-DAWN", ("EXT", "TRAIN STATION", "PRE-DAWN")),
        ("INT. GARAGE", ("INT", "GARAGE", None)),
    ],
)
def test_headings(heading, expected):
    assert parse_heading(heading) == expected


@pytest.mark.parametrize(
    "notes, episode, expected",
    [
        ("Picks up directly from Episode 71's cliffhanger. Nora has hung up on the caller.", 72,
         (71, "Nora has hung up on the caller.")),
        ("Starts immediately after the Episode 62 cliffhanger inside the station lobby, where Maya waits.", 63,
         (62, "inside the station lobby, where Maya waits.")),
        ("Directly follows Episode 59, where Dev confessed.", 60, (59, None)),
        ("Picks up from the previous episode's cliffhanger where the door opened.", 9, (8, "the door opened.")),
        ("First episode of the series.", 1, (None, None)),
        ("Sets up events that pay off in Episode 12.", 5, (None, None)),
    ],
)
def test_continuity_notes(notes, episode, expected):
    assert parse_continuity(notes, episode) == expected


def test_uppercase_scene_keys_warn_once(tmp_path):
    upper = tuple(k.upper() for k in KEYS)
    doc = parse_text(tmp_path, script(
        scene(1, "00:00.00", "00:05.00", block("CAMERA", "00:00.00", "00:05.00", "Shot one."), keys=upper),
        scene(2, "00:05.00", "00:09.00", block("CAMERA", "00:05.00", "00:09.00", "Shot two."), keys=upper),
    ))
    assert codes(doc) == ["UPPERCASE_SCENE_KEYS"]
    assert "scene(s) 1, 2" in doc["warnings"][0]["message"]
    assert validate_episode(doc) == []


def test_sound_bridge_past_scene_end_is_kept_with_warning(tmp_path):
    doc = parse_text(tmp_path, script(
        scene(1, "00:00.00", "00:05.00",
              block("CAMERA", "00:00.00", "00:05.00", "Shot one."),
              block("SOUND FX", "00:04.50", "00:05.30", "A bell rings over the cut.")),
        scene(2, "00:05.00", "00:09.00", block("CAMERA", "00:05.00", "00:09.00", "Shot two.")),
    ))
    assert codes(doc) == ["BLOCK_OUTSIDE_SCENE"]
    assert "0.30s" in doc["warnings"][0]["message"]
    assert validate_episode(doc) == []


def test_small_out_of_order_step_is_sorted_silently(tmp_path):
    doc = parse_text(tmp_path, script(scene(
        1, "00:00.00", "00:08.00",
        block("MAYA (Soft)", "00:05.00", "00:06.00", '"Later line."'),
        block("CAMERA", "00:04.50", "00:08.00", "Close-up."),
    )))
    assert doc["warnings"] == []
    assert [b["block_id"] for b in doc["blocks"]] == ["E007-B002", "E007-B001"]  # ids keep file order
    assert validate_episode(doc) == []


def test_large_out_of_order_step_warns(tmp_path):
    doc = parse_text(tmp_path, script(scene(
        1, "00:00.00", "00:12.00",
        block("CAMERA", "00:10.00", "00:12.00", "Late shot."),
        block("CAMERA", "00:05.00", "00:06.00", "Earlier shot listed late."),
    )))
    assert codes(doc) == ["START_OUT_OF_ORDER"]


def test_multiline_body_is_joined_with_warning(tmp_path):
    doc = parse_text(tmp_path, script(scene(
        1, "00:00.00", "00:04.00",
        block("ACTION", "00:00.00", "00:04.00", "She turns.", "She runs."),
    )))
    assert doc["blocks"][0]["text"] == "She turns. She runs."
    assert codes(doc) == ["MULTILINE_BODY"]


def test_missing_timing_line_fails_with_file_and_line(tmp_path):
    text = script(scene(1, "00:00.00", "00:04.00", "**CAMERA:**\nA shot with no timing.\n"))
    with pytest.raises(ParseError) as err:
        parse_text(tmp_path, text)
    assert err.value.file == "episode-007-Episode 123.txt"
    assert err.value.line == 14


def test_block_ending_before_it_starts_fails(tmp_path):
    with pytest.raises(ParseError, match="ends"):
        parse_text(tmp_path, script(scene(
            1, "00:00.00", "00:04.00", block("CAMERA", "00:03.00", "00:02.00", "Backwards."),
        )))


def test_header_number_wins_over_file_name(tmp_path):
    doc = parse_text(
        tmp_path,
        script(scene(1, "00:00.00", "00:04.00", block("CAMERA", "00:00.00", "00:04.00", "Shot."))),
        name="episode-008-Episode 999.txt",
    )
    assert doc["episode_number"] == 7
    assert codes(doc) == ["EPISODE_NUMBER_MISMATCH"]


def test_missing_script_start_fails(tmp_path):
    with pytest.raises(ParseError, match="SCRIPT START"):
        parse_text(tmp_path, HEADER + "SCENE 01\n")
