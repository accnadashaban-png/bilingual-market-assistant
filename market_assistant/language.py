"""Language, dialect and Arabic text normalization."""
from __future__ import annotations

import re

ARABIC_CHARS = re.compile(r"[؀-ۿ]")
DIACRITICS = re.compile(r"[ً-ْٰـ]")  # harakat, dagger alef, tatweel

# Words that are typical of each dialect (after normalization).
DIALECT_MARKERS = {
    "egyptian": {"ايه", "ازاي", "بكام", "عايز", "عايزه", "النهارده", "ده", "دي", "امتي", "فين", "اوي", "مش",
                 "كده", "بتاع", "بتاعه", "دلوقتي", "ليه", "اللي", "طب", "حاجه", "قد", "اد", "ازيك"},
    "gulf": {"وش", "شلون", "ابي", "ابغي", "ودي", "الحين", "زين", "وايد", "شنو", "اليوم؟", "هالسهم", "كم سعر"},
    "levantine": {"شو", "هلق", "بدي", "منيح", "كتير", "هيك", "ليش", "قديش", "شلون"},
}


def is_arabic(text: str) -> bool:
    return bool(ARABIC_CHARS.search(text or ""))


def detect_language(text: str) -> str:
    """'ar' if the message contains Arabic letters, otherwise 'en'."""
    return "ar" if is_arabic(text) else "en"


def normalize_arabic(text: str) -> str:
    """Lowercase, drop diacritics and unify letters that people write differently."""
    text = DIACRITICS.sub("", (text or "").lower())
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    text = text.replace("؟", "?").replace("،", ",")
    return re.sub(r"\s+", " ", text).strip()


def detect_dialect(text: str) -> str:
    """Best guess of the Arabic dialect: egyptian, gulf, levantine or msa."""
    if not is_arabic(text):
        return "en"
    words = set(re.findall(r"[؀-ۿ]+", normalize_arabic(text)))
    scores = {d: len(words & markers) for d, markers in DIALECT_MARKERS.items()}
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "msa"
