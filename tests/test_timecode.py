import pytest

from promofinder.timecode import TimecodeError, is_canonical_tc, seconds_to_tc, tc_to_seconds


@pytest.mark.parametrize(
    "tc, seconds",
    [
        ("00:00.00", 0.0),
        ("00:04.80", 4.8),
        ("01:00.00", 60.0),
        ("03:20.80", 200.8),
        ("0:04.8", 4.8),
        ("00:04", 4.0),
        ("1:02:03.50", 3723.5),
    ],
)
def test_tc_to_seconds(tc, seconds):
    assert tc_to_seconds(tc) == seconds


@pytest.mark.parametrize("bad", ["", "abc", "00:60.00", "4.8", "00-04.80", "1:60:00.00"])
def test_rejects_non_timecodes(bad):
    with pytest.raises(TimecodeError):
        tc_to_seconds(bad)


@pytest.mark.parametrize("tc", ["00:00.00", "00:04.80", "00:59.99", "01:17.80", "03:20.80"])
def test_round_trip(tc):
    assert seconds_to_tc(tc_to_seconds(tc)) == tc


def test_canonical_form():
    assert is_canonical_tc("01:17.80")
    assert not is_canonical_tc("1:17.8")
