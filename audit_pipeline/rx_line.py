"""The parts of a medicine line written by the PrescribeX app.

app_adapter.medication_line writes `<name> <strength> (<amount and how often>) - (<duration>) [<instruction>]`
and leaves out a part the doctor did not fill in, so "Ace 500 mg" is a medicine with no schedule and no duration.
Lines in other formats (free text such as "Napa 500mg 1+0+1 5 days") are not split: their parts are unknown.
"""

from __future__ import annotations

import re

_APP_LINE = re.compile(
    r"^(?P<name>[^(\[]+?)\s*"
    r"(?:\((?P<freq>(?:[^()]|\([^()]*\))*)\))?\s*"  # one inner bracket is allowed: "5 day(s)"
    r"(?:-\s*\((?P<dur>(?:[^()]|\([^()]*\))*)\))?\s*"
    r"(?:\[(?P<note>[^\]]*)\])?\s*$"
)
# Words that belong to a schedule or a duration. A name part holding one of them is a free-text line whose schedule
# was not put in brackets, not an app line with the schedule left out.
_SCHEDULE_WORDS = re.compile(
    r"\d\s*\+\s*\d|[০-৯]\s*\+\s*[০-৯]|\b(?:x\s*\d|\d+\s*x|days?|weeks?|months?|tabs?|tablets?|caps?|tsp|spoons?|"
    r"puffs?|drops?|daily|times|once|twice|thrice|bd|bid|tds|tid|qds|qid|od|hs|sos|prn|stat|apply|continue)\b"
    r"|দিন|বার|চামচ",
    re.IGNORECASE,
)


def split_app_line(line: str) -> dict[str, str] | None:
    """{"name", "freq", "dur", "note"} for an app medicine line ("" for a part not written), or None when the line is
    not in the app format."""
    match = _APP_LINE.match(str(line or "").strip())
    if not match or _SCHEDULE_WORDS.search(match.group("name")):
        return None
    return {key: (match.group(key) or "").strip() for key in ("name", "freq", "dur", "note")}


__all__ = ["split_app_line"]
