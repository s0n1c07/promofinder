"""Timecode helpers.

Source timecodes are MM:SS.cc (minutes, seconds, hundredths), relative to the start of the
episode. Seconds are computed from whole hundredths, so values such as 4.8 come out exactly
as written instead of 4.800000000000001.
"""

from __future__ import annotations

import re

_TC = re.compile(r"^\s*(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})(?:[.,](?P<f>\d{1,3}))?\s*$")
_CANONICAL = re.compile(r"^\d{2}:\d{2}\.\d{2}$")


class TimecodeError(ValueError):
    """Raised for text that is not a valid timecode."""


def tc_to_seconds(tc: str) -> float:
    """'01:02.50' -> 62.5. Also accepts M:SS.c and H:MM:SS.cc."""
    m = _TC.match(tc)
    if not m:
        raise TimecodeError(f"not a timecode: {tc!r} (expected MM:SS.cc)")
    hours = int(m["h"]) if m["h"] else 0
    minutes, secs = int(m["m"]), int(m["s"])
    if secs >= 60 or (m["h"] and minutes >= 60):
        raise TimecodeError(f"timecode out of range: {tc!r}")
    frac = m["f"] or ""
    hundredths = round(int(frac) * 100 / 10 ** len(frac)) if frac else 0
    return (((hours * 60 + minutes) * 60 + secs) * 100 + hundredths) / 100


def seconds_to_tc(seconds: float) -> str:
    """62.5 -> '01:02.50'."""
    if seconds < 0:
        raise ValueError(f"negative time: {seconds}")
    total = round(seconds * 100)
    minutes, rest = divmod(total, 6000)
    secs, hundredths = divmod(rest, 100)
    return f"{minutes:02d}:{secs:02d}.{hundredths:02d}"


def is_canonical_tc(tc: str) -> bool:
    """True if the text is exactly MM:SS.cc."""
    return bool(_CANONICAL.match(tc))
