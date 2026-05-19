"""
taxonomy.py
-----------
Industrial Taxonomy Normalization Layer.

Provides a deterministic, offline abbreviation dictionary and normalization
utilities for industrial terminology found in manufacturing Excel datasets.
Runs zero external dependencies — pure Python stdlib only.
"""

import re
import unicodedata
from typing import Optional

# ---------------------------------------------------------------------------
# Abbreviation Dictionary — extend this as new plant terms are discovered
# ---------------------------------------------------------------------------
ABBREVIATION_MAP: dict[str, str] = {
    # Directional
    "rh": "right hand",
    "lh": "left hand",
    "rhs": "right hand side",
    "lhs": "left hand side",
    "f/r": "front rear",
    "fr": "front",
    "rr": "rear",

    # Assembly / Process
    "assy": "assembly",
    "asm": "assembly",
    "sub-assy": "sub assembly",
    "sub assy": "sub assembly",
    "inst": "installation",
    "install": "installation",
    "proc": "process",
    "op": "operation",
    "ops": "operations",
    "mfg": "manufacturing",
    "prod": "production",
    "insp": "inspection",
    "qc": "quality control",
    "qa": "quality assurance",

    # Plant / Department
    "tcf": "trim chassis final",
    "tcf1": "trim chassis final 1",
    "tcf2": "trim chassis final 2",
    "bf": "body framing",
    "pd": "paint department",
    "ga": "general assembly",
    "trim": "trim department",
    "chassis": "chassis department",

    # Workstation / Equipment
    "w/s": "workstation",
    "ws": "workstation",
    "stn": "station",
    "eqp": "equipment",
    "equip": "equipment",
    "tl": "tool",
    "torq": "torque",
    "torque wrench": "torque wrench",
    "pnl": "panel",
    "assy jig": "assembly jig",
    "jig": "assembly jig",
    "fixture": "fixture",

    # Components
    "hdlr": "handler",
    "brkt": "bracket",
    "frt": "front",
    "dsh": "dashboard",
    "ip": "instrument panel",
    "cnsl": "console",
    "dr": "door",
    "wdw": "window",
    "glss": "glass",
    "wndsld": "windshield",
    "wh": "wheel",
    "stg": "steering",
    "susp": "suspension",
    "brk": "brake",
    "exh": "exhaust",
    "eng": "engine",
    "trns": "transmission",
    "elec": "electrical",
    "hvac": "heating ventilation air conditioning",
    "ac": "air conditioning",

    # Academic / Theory
    "sem": "semester",
    "dip": "diploma",
    "mod": "module",
    "th": "theory",
    "prac": "practical",
    "lab": "laboratory",
    "proj": "project",
    "subj": "subject",
    "ref": "reference",
    "std": "standard",
}

# Common stopwords to remove during keyword extraction
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "not", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "it", "its", "as",
    "be", "was", "are", "were", "this", "that", "these", "those",
    "into", "onto", "also", "such", "each", "both", "all", "any",
    "per", "via", "vs", "etc", "no", "yes",
})


class TaxonomyNormalizer:
    """
    Stateless normalizer — all methods are classmethods so it can be used
    without instantiation anywhere in the codebase.
    """

    @classmethod
    def normalize(cls, text: Optional[str]) -> str:
        """
        Full normalization pipeline for a raw industrial text string.

        Steps:
          1. Unicode NFKC normalization (handles fancy quotes, em-dashes, etc.)
          2. Lowercase
          3. Strip punctuation except hyphens and slashes (used in abbreviations)
          4. Expand abbreviations
          5. Strip remaining non-alphanumeric punctuation
          6. Collapse whitespace
          7. Strip leading/trailing whitespace
        """
        if not text or not isinstance(text, str):
            return ""

        # 1. Unicode normalization
        text = unicodedata.normalize("NFKC", text)

        # 2. Lowercase
        text = text.lower().strip()

        # 3. Expand abbreviations (word-boundary aware)
        text = cls.expand_abbreviations(text)

        # 4. Remove punctuation (keep alphanumeric and spaces)
        text = re.sub(r"[^\w\s]", " ", text)

        # 5. Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text

    @classmethod
    def expand_abbreviations(cls, text: str) -> str:
        """
        Replace known abbreviations with their full forms.
        Uses word-boundary matching so 'rh' inside 'rhino' is not replaced.
        """
        for abbr, full in ABBREVIATION_MAP.items():
            # Escape special regex chars in abbreviation key
            pattern = r"(?<![a-z])" + re.escape(abbr) + r"(?![a-z])"
            text = re.sub(pattern, full, text)
        return text

    @classmethod
    def extract_keywords(cls, text: Optional[str]) -> list[str]:
        """
        Normalize text, then return non-stopword tokens of length > 1.
        Useful for building search index content and keyword overlap scoring.
        """
        normalized = cls.normalize(text)
        tokens = [
            t for t in normalized.split()
            if t not in STOPWORDS and len(t) > 1
        ]
        return tokens

    @classmethod
    def normalize_code(cls, code: Optional[str]) -> str:
        """
        Normalize entity codes like station codes, skill codes.
        Uppercases, strips spaces, removes special chars except hyphens and underscores.
        """
        if not code or not isinstance(code, str):
            return ""
        code = unicodedata.normalize("NFKC", code).strip().upper()
        code = re.sub(r"[^\w\-]", "_", code)
        code = re.sub(r"_+", "_", code).strip("_")
        return code

    @classmethod
    def is_duplicate(cls, a: Optional[str], b: Optional[str]) -> bool:
        """
        Returns True if two strings are semantically the same after normalization.
        Used for duplicate detection during ingestion.
        """
        return cls.normalize(a) == cls.normalize(b)
