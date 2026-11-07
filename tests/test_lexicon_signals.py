import pytest

from promofinder.config import load_config, resolve
from promofinder.lexicon import Lexicon
from promofinder.signals import block_signals, delivery_levels


@pytest.fixture(scope="module")
def lex():
    return Lexicon.load(resolve(load_config()["lexicon"]["path"]))


def dialogue(text, *delivery):
    return {"block_id": "E001-B001", "kind": "DIALOGUE", "text": text,
            "speaker": {"label": "X", "voice": "ON", "delivery": list(delivery)}}


def other(kind, text):
    return {"block_id": "E001-B002", "kind": kind, "text": text, "speaker": None}


def test_matching_rules():
    lex = Lexicon({"g": {"weight": 1.0, "terms": ["slap*", "riding crop", "what?"]}})
    assert lex.find("g", "She SLAPPED him with a riding crop.") == ["slapped", "riding crop"]
    assert lex.find("g", "an unslappable crop") == []  # whole words only
    assert lex.find("g", "What? No!") == ["what?"]


def test_kind_limits():
    lex = Lexicon({"g": {"weight": 1.0, "kinds": ["DIALOGUE"], "terms": ["guards"]}})
    assert lex.find("g", "Guards!", "DIALOGUE") == ["guards"]
    assert lex.find("g", "Royal guards line the hall.", "CAMERA") == []


def test_every_group_is_well_formed(lex):
    for group, terms in lex.terms.items():
        assert terms and 0 < lex.weights[group] <= 1, group


def test_delivery_levels(lex):
    assert delivery_levels(["Screaming"], lex) == (1.0, 0.0)
    assert delivery_levels(["Gloating", "Smug"], lex) == (0.6, 0.0)
    assert delivery_levels(["Gentle", "Reassuring"], lex) == (0.0, 1.0)
    assert delivery_levels(["Viciously Calm"], lex) == (1.0, 0.0)  # calm but vicious is not soft


def test_shocking_line_is_a_hook(lex):
    assert block_signals(dialogue("It was you who sold the house.", "Gloating", "Smug"), lex).hook >= 0.6
    assert block_signals(dialogue("The herbs did their work.", "Gentle"), lex).hook < 0.35


def test_physical_action_is_a_hook(lex):
    s = block_signals(other("ACTION", "Lady Vera strikes Mira with a riding crop."), lex)
    assert s.hook >= 0.6 and s.hook_group == "physical"


def test_setting_words_are_not_drama(lex):
    """Precision fixes from the M2 lexicon audit."""
    assert block_signals(other("CAMERA", "Wide shot of the altar where royal guards stand."), lex).hook < 0.35
    assert block_signals(other("SOUND_FX", "Crackling flames in the open fireplace."), lex).hook < 0.35
    assert block_signals(other("CAMERA", "Mira stands framed in the light."), lex).hook < 0.35


def test_cliffhanger_and_resolution_signals(lex):
    assert block_signals(other("SOUND_FX", "Ominous drone swells to a cliffhanger peak."), lex).cliff >= 0.8
    question = block_signals(dialogue("Who told you my secret?", "Desperate"), lex)
    assert question.question and question.cliff >= 0.5
    assert block_signals(other("ACTION", "They embrace in relief."), lex).resolution >= 0.7


def test_context_debt_counts_spoken_lines_only(lex):
    assert block_signals(dialogue("You promised me the ring.", "Cold"), lex).debt == 1.0  # two references
    assert block_signals(dialogue("Bring it here.", "Cold"), lex).debt == 0.0
    assert block_signals(other("CAMERA", "Close-up of the ring on her finger."), lex).debt == 0.0


def test_graphic_flag(lex):
    assert block_signals(other("CAMERA", "The red-hot branding iron glows."), lex).graphic
