"""Pure functions that clean raw OptcgAPI rows before insertion.

Each function maps to one rule in DESIGN.md §8.
"""

from __future__ import annotations

import re

NULL_LIKES = {"NULL", "null", "?", "", "-", "--"}

NO_COUNTER_CATEGORIES = {"Leader", "Event", "Stage"}

# Only Leaders have Life. Verified against all 4,459 API rows: 297/297 Leaders
# carry one, and no Character, Event or Stage does.
LIFE_CATEGORIES = {"Leader"}

# A closed set in this game, unlike sub-types which grow with each set. Tokens
# outside it are dropped - see normalize_attributes.
KNOWN_ATTRIBUTES = ("Slash", "Strike", "Ranged", "Special", "Wisdom")
_ATTRIBUTE_BY_FOLD = {a.casefold(): a for a in KNOWN_ATTRIBUTES}

ATTRIBUTE_SPLIT_RE = re.compile(r"[/\s]+")

# What a printing's variant suffix means. Checked against all 4,338 rows: every
# suffix in the feed starts with one of these letters and nothing else.
#   p*  parallel / alternate art   (Box Topper, Manga, SP, Gold-Stamped)
#   r*  reprint                    (premium boosters - PRB-01, PRB-02)
#   pr* promo                      (regionals, film tie-ins, sealed kits)
# Order matters: 'pr' must be tested before 'p', or every promo reads parallel.
PRINTING_KIND_PREFIXES = (("pr", "promo"), ("p", "parallel"), ("r", "reprint"))

BASE_PRINTING_KIND = "base"

SET_PREFIX_RE = re.compile(r"^([A-Za-z]+\d*)-")

KEYWORD_PATTERNS = {
    "has_trigger": re.compile(r"\[Trigger\]", re.IGNORECASE),
    "has_blocker": re.compile(r"\[Blocker\]", re.IGNORECASE),
    "has_rush": re.compile(r"\[Rush\]", re.IGNORECASE),
    "has_double_attack": re.compile(r"\[Double Attack\]", re.IGNORECASE),
    "has_banish": re.compile(r"\[Banish\]", re.IGNORECASE),
}

PRINTING_VARIANT_RE = re.compile(r"[_-]([a-zA-Z]+\d+)$")

FILE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)$", re.IGNORECASE)


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
    """Rule 5: 'Slash / Special' and 'Slash Special' → 'Slash/Special'.

    The API separates dual attributes inconsistently - most use a space
    ('Slash Strike'), a few a slash ('Slash / Special') - so both split the
    same way, mirroring normalize_colors.

    Tokens outside KNOWN_ATTRIBUTES are dropped, which is what clears the
    three junk values the API actually serves: '?' (handled by NULL_LIKES),
    'N/A' on two reprint stages, and 'Straw Hat Crew ODYSSEY' on OP09-033 -
    a trait string sitting in the attribute slot. A row whose every token is
    unrecognised returns None rather than inventing an attribute.
    """
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    kept = []
    for part in ATTRIBUTE_SPLIT_RE.split(cleaned):
        canonical = _ATTRIBUTE_BY_FOLD.get(part.strip().casefold())
        if canonical and canonical not in kept:
            kept.append(canonical)
    return "/".join(kept) if kept else None


def normalize_life(category, raw_value):
    """Rule 7: int for Leaders; NULL for everything else.

    Mirrors normalize_counter. Guarding on category means a shifted row that
    puts a number in the life slot of a Character cannot invent a life total.
    """
    cat = normalize_null(category)
    if cat not in LIFE_CATEGORIES:
        return None
    return to_int(raw_value)


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


def normalize_card_image_id(value):
    """Rule 10: canonicalise the printing key.

    `card_image_id` is the primary key for a printing and the column the
    collection references, so it has to be stable. A few API rows carry the
    image filename instead ('EB02-052_p2.jpg') or separate the variant with a
    hyphen ('OP09-078-r1'). Both fold to the canonical '<base>_<variant>' form.
    """
    cleaned = normalize_null(value)
    if cleaned is None:
        return None
    cleaned = FILE_EXT_RE.sub("", str(cleaned))
    m = PRINTING_VARIANT_RE.search(cleaned)
    if m:
        # Lowercase the variant: one row ships 'OP04-016_P1' where the other
        # 691 parallels use 'p1'. Folding it keeps one printing from splitting
        # into two primary keys. Verified collision-free across all 4,338 ids.
        cleaned = f"{cleaned[:m.start()]}_{m.group(1).lower()}"
    return cleaned


def strip_printing_suffix(card_id):
    """Rule 11: 'P-029_r1' → 'P-029'; 'OP05-097' → 'OP05-097'.

    `base_card_id` is the gameplay identity — every printing of a card must
    share it, or deck lookups silently miss the printings you actually own.
    """
    cleaned = normalize_null(card_id)
    if cleaned is None:
        return None
    return PRINTING_VARIANT_RE.sub("", str(cleaned))


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


def game_set_from_id(base_card_id):
    """Rule 13: 'OP05-006' → 'OP05' — the set a card belongs to.

    Distinct from the `set_code` the API sends, which names the *product* a
    printing came in. Both are real and both are kept (D-028): a reprint of an
    OP05 card inside the PRB-01 booster is `game_set='OP05'`,
    `set_code='PRB-01'`.

    Derived from `base_card_id` rather than `set_code` because `set_code` can't
    answer it - the feed has no 'OP-14' at all (those cards sit under
    'OP14-EB04'), and spells promo sets without the hyphen. `base_card_id` is
    already canonical for all 4,338 rows: 53 clean prefixes, none unparseable.
    """
    cleaned = normalize_null(base_card_id)
    if cleaned is None:
        return None
    m = SET_PREFIX_RE.match(str(cleaned))
    return m.group(1).upper() if m else None


def printing_kind(printing_variant):
    """Rule 14: 'r1' → 'reprint'; 'p2' → 'parallel'; None → 'base'.

    Makes the reprint/alt-art distinction queryable instead of leaving it
    encoded in a suffix. D-016 credits the base printing on deck import, and
    this is what lets a picker show you the other three kinds (D-028).

    An unrecognised suffix returns None rather than guessing a kind - the
    printing is still perfectly usable, it just isn't classified.
    """
    cleaned = normalize_null(printing_variant)
    if cleaned is None:
        return BASE_PRINTING_KIND
    lowered = str(cleaned).strip().lower()
    for prefix, kind in PRINTING_KIND_PREFIXES:
        if lowered.startswith(prefix):
            return kind
    return None
