import json

from promofinder.cli import main

GOOD = """EPISODE NUMBER: Episode 1
EPISODE LOGLINE: A good episode.
CONTINUITY NOTES: First episode of the series.

--- SCRIPT START ---

SCENE 01
Scene heading: INT. ROOM – DAY
Scene start: 00:00.00
Scene end: 00:05.00

Actions and dialogue:

**CAMERA:**
Start: (00:00.00) | End: (00:05.00)
A wide shot.

--- SCRIPT END ---
"""

BROKEN = GOOD.replace("EPISODE NUMBER: Episode 1", "EPISODE NUMBER: Episode 2").replace(
    "Start: (00:00.00) | End: (00:05.00)\n", ""
)


def test_parse_writes_files_and_reports_errors(tmp_path):
    inp, out = tmp_path / "Show", tmp_path / "out"
    inp.mkdir()
    (inp / "episode-001-Episode 11.txt").write_text(GOOD, encoding="utf-8")
    (inp / "episode-002-Episode 12.txt").write_text(BROKEN, encoding="utf-8")

    assert main(["parse", "--input", str(inp), "--out", str(out)]) == 1  # one file is broken

    report = json.loads((out / "parse_report.json").read_text(encoding="utf-8"))
    assert report["show_id"] == "show"
    assert (report["files_found"], report["episodes_parsed"], report["episodes_valid"]) == (2, 1, 1)
    assert len(report["errors"]) == 1 and "episode-002" in report["errors"][0]["error"]
    assert (out / "E001.json").exists() and not (out / "E002.json").exists()
    assert (out / "parse_report.md").read_text(encoding="utf-8").startswith("# Parse report")

    assert main(["validate", str(out)]) == 0


def test_episode_ranges_parse():
    from promofinder.cli import episode_set

    assert episode_set("1-3,12, 50") == {1, 2, 3, 12, 50}
    assert episode_set(None) is None
