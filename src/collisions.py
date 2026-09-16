"""Resolve card_image_id collisions in the raw API feed (D-029).

OptcgAPI uses `card_image_id` as if it named one printing. It doesn't: across
the three feeds, 118 ids each label two or three physically different cards -
the regular OP01-016 Nami and her alt-art promo both arrive as 'OP01-016'.
`INSERT OR REPLACE` then lets whichever row came last overwrite the others,
which is how the promo's typo'd counter became the regular card's counter.

This module is pure: raw rows in, raw rows out, no DB. Every row keeps its own
name, image, rarity and set but gets a unique id. Gameplay fields are inherited
from the base row, because the same id means the same card.

Rules, cheapest first (counts from the 2026-09 cache):

  0. Exact duplicate rows (same everything but image/date) collapse to one.   4
  1. The base row keeps the id: plain image name, set matches, not a promo.
  2. A duplicate whose image filename carries a real `_p1`/`_r1` id uses it.  9
  3. Otherwise the suffix is a slug of what its name adds to the base name.   63
  4. If that still collides, the set code is appended.                       42
  5. Never reached today; a numeric ordinal exists so it can't crash.
"""

from __future__ import annotations

import re

from src.sanitize import (
    game_set_from_id,
    normalize_card_image_id,
    normalize_null,
    strip_printing_suffix,
)

# Fields that describe the *card*, not the printing. Inherited from the base
# row so a data-entry error on a promo can't rewrite the regular card.
GAMEPLAY_FIELDS = (
    "card_type", "card_cost", "card_power", "counter_amount", "card_color",
    "attribute", "life", "card_text", "sub_types",
)

# Ignored when deciding whether two rows are the same row listed twice.
VOLATILE_FIELDS = frozenset({"card_image", "date_scraped", "_source"})

IMAGE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)$", re.IGNORECASE)
# Django appends _<7 chars> when a second upload wants an existing filename.
STORAGE_TOKEN_RE = re.compile(r"_[A-Za-z0-9]{7}$")
WELL_FORMED_ID_RE = re.compile(r"^(OP|ST|EB|PRB|P)\d*-\d{3}(_(p|r|pr)\d+)?$")

PROMO_WORDS = re.compile(
    r"promo|championship|regional|judge|winner|finalist|participant|treasure cup|"
    r"tournament|store|event|pack 20\d\d|sealed",
    re.IGNORECASE,
)


def filename_id(row: dict) -> str | None:
    """'.../OP07-076_r1.jpg' -> 'OP07-076_r1'; name-based filenames -> None."""
    url = normalize_null(row.get("card_image"))
    if not url:
        return None
    name = str(url).rsplit("/", 1)[-1]
    name = IMAGE_EXT_RE.sub("", name)
    name = STORAGE_TOKEN_RE.sub("", name)
    candidate = normalize_card_image_id(name)
    if candidate and WELL_FORMED_ID_RE.match(candidate):
        return candidate
    return None


def slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.lower())).strip("_")


PARENTHETICAL_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
EMBEDDED_ID_RE = re.compile(r"\s*-\s*(OP|ST|EB|PRB|P)\d*-\d{3}(_\w+)?", re.IGNORECASE)


def core_name(name: str) -> str:
    """'Roronoa Zoro - OP01-001 (Alternate Art)' -> 'Roronoa Zoro'."""
    text = EMBEDDED_ID_RE.sub(" ", name or "")
    text = PARENTHETICAL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_suffix(dup_name: str, base_name: str, card_id: str) -> str:
    """What a duplicate's name adds: 'Nami - OP01-016 (Alternate Art)' -> 'alternate_art'.

    Strips the card id and the base card's core name, so only the printing's
    own description survives. The base row may itself be '(Alternate Art)' -
    stripping its full name would leave the core in place, hence core_name.
    """
    text = dup_name or ""
    for drop in (card_id, strip_printing_suffix(card_id) or "", core_name(base_name)):
        if drop:
            text = re.sub(re.escape(drop), " ", text, flags=re.IGNORECASE)
    return slug(text)


def kind_for(row: dict) -> str:
    """Reprint if the name says so; promo by feed or wording; else parallel."""
    name = row.get("card_name") or ""
    if "reprint" in name.lower():
        return "reprint"
    if row.get("_source") == "promos" or PROMO_WORDS.search(name):
        return "promo"
    return "parallel"


def _content_key(row: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in row.items() if k not in VOLATILE_FIELDS))


def _base_score(row: dict, own_id: str) -> tuple:
    """Lower wins: plain image name, matching set, non-promo feed, shortest name."""
    set_norm = (normalize_null(row.get("set_id")) or "").replace("-", "")
    game_set = game_set_from_id(strip_printing_suffix(own_id)) or ""
    return (
        0 if filename_id(row) == own_id else 1,
        0 if set_norm == game_set else 1,
        0 if row.get("_source") != "promos" else 1,
        len(row.get("card_name") or ""),
        str(row.get("card_image") or ""),
    )


def resolve_collisions(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Return rows with unique ids, plus counts of what was done.

    Non-colliding rows pass through untouched. A re-identified row carries
    `_base_card_id`, `_printing_variant` and `_printing_kind` so the sanitizer
    doesn't try to re-derive them from a synthesised id.
    """
    stats = {
        "collisions": 0, "exact_duplicates": 0,
        "by_filename": 0, "by_name": 0, "by_name_and_set": 0, "by_ordinal": 0,
    }

    groups: dict[str, list[dict]] = {}
    passthrough: list[dict] = []
    for row in rows:
        cid = normalize_card_image_id(row.get("card_image_id"))
        if cid is None:
            passthrough.append(row)  # sync logs these as skipped
            continue
        groups.setdefault(cid, []).append(row)

    taken = set(groups)
    out: list[dict] = list(passthrough)

    for cid, group in groups.items():
        # Rule 0 - the same row listed twice.
        unique: dict[tuple, dict] = {}
        for row in group:
            key = _content_key(row)
            if key in unique:
                stats["exact_duplicates"] += 1
            else:
                unique[key] = row
        group = list(unique.values())

        if len(group) == 1:
            out.append(group[0])
            continue

        stats["collisions"] += 1
        group.sort(key=lambda r: _base_score(r, cid))
        base, dups = group[0], group[1:]
        out.append(base)
        base_card_id = strip_printing_suffix(cid)
        base_name = base.get("card_name") or ""

        for dup in dups:
            new_row = dict(dup)
            for field in GAMEPLAY_FIELDS:
                if field in base:
                    new_row[field] = base[field]

            # Rule 2 - the image already knows the real printing id.
            fid = filename_id(dup)
            if (fid and fid != cid and fid not in taken
                    and strip_printing_suffix(fid) == base_card_id):
                new_row["card_image_id"] = fid
                taken.add(fid)
                stats["by_filename"] += 1
                out.append(new_row)
                continue

            # Rules 3-5 - synthesise a suffix from the name, then set, then ordinal.
            suffix = (name_suffix(dup.get("card_name") or "", base_name, cid)
                      or slug(normalize_null(dup.get("set_id")) or "")
                      or "alt")
            new_id, how = f"{cid}_{suffix}", "by_name"
            if new_id in taken:
                dup_set = slug(normalize_null(dup.get("set_id")) or "") or "x"
                new_id, how = f"{cid}_{suffix}_{dup_set}", "by_name_and_set"
            ordinal = 2
            while new_id in taken:
                new_id, how = f"{cid}_{suffix}_{ordinal}", "by_ordinal"
                ordinal += 1
            taken.add(new_id)
            stats[how] += 1

            new_row["card_image_id"] = new_id
            new_row["_base_card_id"] = base_card_id
            new_row["_printing_variant"] = new_id[len(base_card_id) + 1:]
            new_row["_printing_kind"] = kind_for(dup)
            out.append(new_row)

    return out, stats
