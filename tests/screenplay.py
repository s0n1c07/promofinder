"""A small synthetic screenplay for testing the candidate pipeline (not from the input pack).

Timeline (70 s, one scene):
  0-10   quiet garden; maid talks about roses            (no hook)
  10.2   ACTION Lady Vera slaps Mira                       (hook)
  15-27  insults and a threat: "hang before dawn"
  31-34  Mira: "Who told you my secret?"                   (open question)
  41-44  Vera answers                                      (the payoff a promo should withhold)
  45-50  the women embrace in relief                        (resolution: bad place to end)
"""

from pathlib import Path

from promofinder.parse import parse_episode


def _block(label: str, start: str, end: str, text: str) -> str:
    return f"**{label}:**\nStart: ({start}) | End: ({end})\n{text}\n"


BLOCKS = [
    ("CAMERA", "00:00.00", "00:10.00", "Wide shot of a quiet garden at noon."),
    ("MAID (Soft)", "00:02.00", "00:04.00", '"The roses look lovely today."'),
    ("CAMERA", "00:10.00", "00:14.00", "Close-up on Lady Vera."),
    ("ACTION", "00:10.20", "00:12.00", "Lady Vera slaps Mira across the face."),
    ("MIRA (Shocked, Trembling)", "00:12.20", "00:14.00", '"You struck me!"'),
    ("CAMERA", "00:14.00", "00:30.00", "Medium two-shot of the women facing each other."),
    ("LADY VERA (Cold, Cruel)", "00:15.00", "00:18.00", '"You are nothing but a servant."'),
    ("MIRA (Defiant)", "00:19.00", "00:22.00", '"I will not kneel to you."'),
    ("LADY VERA (Menacing)", "00:23.00", "00:27.00", '"Then you will hang before dawn."'),
    ("CAMERA", "00:30.00", "00:40.00", "Close-up on Mira, pale."),
    ("MIRA (Desperate)", "00:31.00", "00:34.00", '"Who told you my secret?"'),
    ("CAMERA", "00:40.00", "00:55.00", "Wide shot of the two women."),
    ("LADY VERA (Smug)", "00:41.00", "00:44.00", '"Your own sister did."'),
    ("ACTION", "00:45.00", "00:50.00", "The two women embrace in relief, smiling warmly."),
    ("MIRA (Soft)", "00:50.00", "00:53.00", '"Thank you."'),
    ("CAMERA", "00:55.00", "01:10.00", "Wide shot of the garden at dusk."),
    ("SOUND FX", "01:06.00", "01:10.00", "Gentle music fades out."),
]

SCRIPT = (
    "EPISODE NUMBER: Episode 9\n"
    "EPISODE LOGLINE: A maid is slapped and threatened by her mistress.\n"
    "CONTINUITY NOTES: First episode of the test show.\n\n"
    "--- SCRIPT START ---\n\n"
    "SCENE 01\nScene heading: EXT. GARDEN – DAY\nScene start: 00:00.00\nScene end: 01:10.00\n\n"
    "Actions and dialogue:\n\n"
    + "\n".join(_block(*b) for b in BLOCKS)
    + "\n--- SCRIPT END ---\n"
)


def synthetic_episode(tmp_path: Path) -> dict:
    path = tmp_path / "episode-009-Episode 900.txt"
    path.write_text(SCRIPT, encoding="utf-8")
    return parse_episode(path, show_id="test")


def custom_episode(tmp_path: Path, number: int, blocks: list[tuple], end: str) -> dict:
    """A one-scene episode from (label, start, end, text) blocks, parsed like a real screenplay."""
    script = (f"EPISODE NUMBER: Episode {number}\nEPISODE LOGLINE: A test scene.\nCONTINUITY NOTES: None.\n\n"
              f"--- SCRIPT START ---\n\nSCENE 01\nScene heading: INT. HALL – DAY\nScene start: 00:00.00\n"
              f"Scene end: {end}\n\nActions and dialogue:\n\n" + "\n".join(_block(*b) for b in blocks)
              + "\n--- SCRIPT END ---\n")
    path = tmp_path / f"episode-{number:03d}-Episode {number}00.txt"
    path.write_text(script, encoding="utf-8")
    return parse_episode(path, show_id="test")


# A slam (hook), then a question and its answer at 24-29 s, then a threat (cliffhanger) at 33 s.
HALL = [
    ("CAMERA", "00:00.00", "00:06.00", "Wide shot of a great hall."),
    ("ACTION", "00:00.50", "00:02.00", "Lord Brand slams his fist on the table."),
    ("LORD BRAND (Furious)", "00:02.50", "00:05.00", '"You betrayed this house!"'),
    ("CAMERA", "00:06.00", "00:24.00", "Medium two-shot of the pair."),
    ("ANNA (Quiet)", "00:08.00", "00:11.00", '"I only did what I had to."'),
    ("CAMERA", "00:24.00", "00:30.00", "Close-up on Anna."),
    ("ANNA (Pleading)", "00:24.00", "00:26.00", '"Why are you shouting at me?"'),
    ("LORD BRAND (Cold)", "00:26.50", "00:29.00", '"Because you lied to me."'),
    ("CAMERA", "00:30.00", "00:40.00", "Close-up on Lord Brand."),
    ("LORD BRAND (Menacing)", "00:33.00", "00:36.00", '"Then you will hang before dawn."'),
    ("CAMERA", "00:40.00", "01:00.00", "Wide shot of the empty hall."),
]


def synthetic_pair(tmp_path: Path) -> tuple[dict, dict]:
    """Episode 9 and an Episode 10 that picks up directly from it (same scenes), for bridges."""
    ep9 = synthetic_episode(tmp_path)
    script10 = SCRIPT.replace("EPISODE NUMBER: Episode 9", "EPISODE NUMBER: Episode 10").replace(
        "CONTINUITY NOTES: First episode of the test show.",
        "CONTINUITY NOTES: Picks up directly from Episode 9's cliffhanger where Lady Vera answered Mira.")
    path = tmp_path / "episode-010-Episode 1000.txt"
    path.write_text(script10, encoding="utf-8")
    return ep9, parse_episode(path, show_id="test")
