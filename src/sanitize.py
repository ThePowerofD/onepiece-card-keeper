"""Pure functions that clean raw OptcgAPI rows before insertion.

Each function maps to one rule in DESIGN.md §8.
"""

from __future__ import annotations

import re

NULL_LIKES = {"NULL", "null", "?", "", "-", "--"}

NO_COUNTER_CATEGORIES = {"Leader", "Event", "Stage"}

KEYWORD_PATTERNS = {
    "has_trigger": re.compile(r"\[Trigger\]", re.IGNORECASE),
    "has_blocker": re.compile(r"\[Blocker\]", re.IGNORECASE),
    "has_rush": re.compile(r"\[Rush\]", re.IGNORECASE),
    "has_double_attack": re.compile(r"\[Double Attack\]", re.IGNORECASE),
    "has_banish": re.compile(r"\[Banish\]", re.IGNORECASE),
}

PRINTING_VARIANT_RE = re.compile(r"_([a-zA-Z]+\d+)$")


def normalize_null(value):
    """Rule 1: convert API null-likes to None."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in NULL_LIKES or stripped == "":
            return None
        return stripped
    return value


def to_int(value):
    """Rule 2: safe string→int. Null-likes → None."""
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    if isinstance(cleaned, int):
        return cleaned
    try:
        return int(str(cleaned).strip())
    except (ValueError, TypeError):
        return None


def normalize_counter(category, raw_value):
    """Rule 3: int for Characters; NULL for everything else."""
    cat = normalize_null(category)
    if cat in NO_COUNTER_CATEGORIES:
        return None
    return to_int(raw_value)


def normalize_colors(value):
    """Rule 4: 'Blue Red' → 'Blue/Red'."""
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    if "/" in cleaned:
        parts = [p.strip() for p in cleaned.split("/") if p.strip()]
        return "/".join(parts)
    parts = cleaned.split()
    return "/".join(parts) if parts else None


def normalize_attributes(value):
    """Rule 5: 'Slash / Special' → 'Slash/Special'."""
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    parts = [p.strip() for p in cleaned.split("/") if p.strip()]
    return "/".join(parts) if parts else None


def parse_subtypes(raw_string, known_types_list):
    """Rule 6: longest-match against known types.

    Returns (matched_types, leftover).
    """
    cleaned = normalize_null(raw_string)
    if cleaned is None:
        return [], ""

    sorted_known = sorted(set(known_types_list), key=len, reverse=True)
    known_lower = [(k, k.lower()) for k in sorted_known]

    remainder = re.sub(r"\s+", " ", cleaned.replace(",", " ").replace("/", " ")).strip()
    matched: list[str] = []
    unmatched_tokens: list[str] = []

    while remainder:
        remainder_lower = remainder.lower()
        hit = None
        for original, lower in known_lower:
            if remainder_lower.startswith(lower):
                end = len(lower)
                if end == len(remainder_lower) or remainder_lower[end] == " ":
                    hit = (original, end)
                    break
        if hit:
            matched.append(hit[0])
            remainder = remainder[hit[1]:].lstrip()
        else:
            head, _, rest = remainder.partition(" ")
            unmatched_tokens.append(head)
            remainder = rest.lstrip()

    leftover = " ".join(unmatched_tokens)
    seen = set()
    deduped = []
    for m in matched:
        if m not in seen:
            seen.add(m)
            deduped.append(m)
    return deduped, leftover


def _is_bare_number(value):
    return bool(re.fullmatch(r"\d+", str(value).strip()))


def repair_field_shift(raw_power, raw_sub_types):
    """Rule 9: repair upstream rows whose power and sub_types are misaligned.

    A handful of OptcgAPI rows have fields shifted out of position, which shows
    up as a bare number sitting in sub_types (a real sub_type is never numeric):

        swap  — power holds the type text, sub_types holds the power
                ('Sky Island', '1000') -> (1000, 'Sky Island')
        shift — power holds an unrelated value and the real sub_types are gone
                ('4', '5000')          -> (5000, None)

    Returns (power, sub_types, was_repaired). Rows that look sane are returned
    untouched, so healthy data never takes this path.
    """
    power = normalize_null(raw_power)
    sub_types = normalize_null(raw_sub_types)

    if sub_types is None or not _is_bare_number(sub_types):
        return raw_power, raw_sub_types, False

    recovered_power = to_int(sub_types)
    if power is not None and to_int(power) is None:
        return recovered_power, power, True
    return recovered_power, None, True


def extract_printing_variant(card_image_id):
    """Rule 7: 'OP05-097_p1' → 'p1'; 'OP05-097' → None."""
    cleaned = normalize_null(card_image_id)
    if cleaned is None:
        return None
    m = PRINTING_VARIANT_RE.search(cleaned)
    return m.group(1) if m else None


def detect_keywords(effect_text):
    """Rule 8: substring match on bracketed keywords."""
    cleaned = normalize_null(effect_text)
    if cleaned is None:
        return {k: False for k in KEYWORD_PATTERNS}
    return {key: bool(pat.search(cleaned)) for key, pat in KEYWORD_PATTERNS.items()}
