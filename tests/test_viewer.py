"""viewer.html: one self-contained page per run with every promo, its transcript and the posting plan."""

import json
import re
from pathlib import Path

import pytest

from promofinder.pipeline import run

INPUT = Path(__file__).resolve().parents[1] / "Input"


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    if not any(INPUT.glob("*.txt")):
        pytest.skip("input pack not present")
    out = tmp_path_factory.mktemp("viewer") / "run"
    run(INPUT, out, judge_spec="offline", episodes_only={1, 2})
    return out


def _data(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_every_promo_is_in_the_viewer_with_its_transcript(small_run):
    html = (small_run / "viewer.html").read_text(encoding="utf-8")
    promos = json.loads((small_run / "promos.json").read_text(encoding="utf-8"))
    data = _data(html)
    assert [p["rank"] for p in data["promos"]] == [p["rank"] for p in promos]
    first = data["promos"][0]
    lines = [line for part in first["parts"] for line in part["lines"]]
    assert lines and any(line["mark"] == "hook" for line in lines) and any(line["mark"] == "cliffhanger" for line in lines)
    assert first["hook"] == promos[0]["hook"]["text"] and first["key"].startswith("Ep ")
    assert data["plan"] and all(isinstance(r, int) for t in data["plan"] for r in t["ranks"])


def test_the_viewer_needs_no_network_and_cannot_be_broken_by_a_line(small_run):
    html = (small_run / "viewer.html").read_text(encoding="utf-8")
    assert not re.search(r'(src|href)="https?:', html)            # no external script, style or font
    data_block = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
    assert "</" not in data_block                                  # a "</script>" inside a line can't end the block
