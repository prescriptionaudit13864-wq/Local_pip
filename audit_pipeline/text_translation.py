"""Central text-normalization/translation hints for non-English prescription text.

This module is deliberately separate from audit rules. Rules should consume the
English semantic hints produced here instead of embedding Bengali phrases in
many different places.
"""

from __future__ import annotations

import re
from typing import Any

_PHRASE_TRANSLATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:আহারের|খাবারের|খাওয়ার|খাওয়ার)(?:\s+\d+\s*(?:মিনিট|ঘণ্টা))?\s*(?:পর|পরে)"), "after food"),
    (re.compile(r"(?:আহারের|খাবারের|খাওয়ার|খাওয়ার)(?:\s+\d+\s*(?:মিনিট|ঘণ্টা))?\s*(?:পূর্বে|আগে)"), "before food"),
    (re.compile(r"খাবারের\s*সাথে|আহারের\s*সাথে"), "with food"),
    (re.compile(r"খালি\s*পেটে"), "empty stomach"),
    (re.compile(r"খাবেন|মুখে|পানি\s+দিয়ে\s+গিলে|পানি\s+দিয়ে\s+গিলে"), "take by mouth"),
    (re.compile(r"চিবিয়ে|চিবিয়ে"), "chew by mouth"),
    (re.compile(r"জিহ্বার\s+নিচে"), "sublingual route"),
    (re.compile(r"পানিতে\s+গুলে"), "dissolve in water and take by mouth"),
    (re.compile(r"বোতল\s+ঝাঁকিয়ে|বোতল\s+ঝাঁকিয়ে"), "shake bottle before use"),
    (re.compile(r"সকালে"), "in the morning"),
    (re.compile(r"দুপুরে"), "at noon"),
    (re.compile(r"রাতে"), "at night"),
    (re.compile(r"ঘুমানোর\s+আগে|শোবার\s+আগে"), "at bedtime"),
    (re.compile(r"চোখে|প্রতি\s+চোখে|বাম\s+চোখে|ডান\s+চোখে"), "instill in eye"),
    (re.compile(r"কানে|প্রতি\s+কানে|বাম\s+কানে|ডান\s+কানে"), "instill in ear"),
    (re.compile(r"নাসারন্ধ্রে|নাকে"), "instill in nostril; nasal route"),
    (re.compile(r"মুখ\s+দিয়ে\s+শ্বাস|মুখ\s+দিয়ে\s+শ্বাস"), "inhale by mouth"),
    (re.compile(r"মুখ\s+কুলি"), "rinse mouth after use"),
    (re.compile(r"আক্রান্ত\s+স্থানে|বাহ্যিক\s+ব্যবহারের"), "apply topically; external use"),
    (re.compile(r"যোনিপথে"), "vaginal route"),
    (re.compile(r"পায়ুপথে|পায়ুপথে"), "rectal route"),
    (re.compile(r"শিরায়|শিরায়"), "intravenous route"),
    (re.compile(r"পেশিতে"), "intramuscular route"),
    (re.compile(r"চামড়ার\s+নিচে|চামড়ার\s+নিচে"), "subcutaneous route"),
    (re.compile(r"জ্বর\s*কমেছে"), "fever improved"),
    (re.compile(r"কমেছে"), "improved"),
    (re.compile(r"ভাল|ভালো"), "better"),
    (re.compile(r"সেরে\s*গেছে"), "resolved"),
    (re.compile(r"রিপোর্ট\s*(?:দেখাতে|দেখানো|নিয়ে|নিয়ে)"), "report showing"),
    (re.compile(r"ফলো\s*আপ"), "follow up"),
    (re.compile(r"বুকে\s*চাপ"), "chest pressure"),
    (re.compile(r"শরীর\s*ব্যথা"), "body ache"),
    (re.compile(r"\bpregnanacy\b", re.IGNORECASE), "pregnancy"),
)


def translated_text(value: Any) -> str:
    """Return original text plus compact English hints for deterministic parsing.

    The original text is preserved first so reports can still cite the source.
    Hints are appended only when a known phrase is present.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    hints: list[str] = []
    for pattern, translation in _PHRASE_TRANSLATIONS:
        if pattern.search(text) and translation not in hints:
            hints.append(translation)
    if not hints:
        return text
    return f"{text} [{' ; '.join(hints)}]"


__all__ = ["translated_text"]
