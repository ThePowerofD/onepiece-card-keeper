# Phase 1 — Collection & Decks (CLI)

**Goal:** track what you own, where it is, and what's committed to which deck —
plus build decks for cards you *don't* own and see what's missing.

**Deliverable:** CLI commands to import Limitless decklists, place loose cards in
storage, and see what's free to build with. No UI — Phase 3 replaces the CLI with
windows, calling the same logic.

Depends on Phase 0. Model and schema: [DESIGN.md](DESIGN.md). Rationale:
[DECISIONS.md](DECISIONS.md).

| Task | Status |
|---|---|
| 1.1 Schema additions | **Done** — `db_setup.py`, tests in `tests/test_schema.py` |
| 1.2 Limitless decklist parser | **Done** — `deck_parser.py`, tests in `tests/test_deck_parser.py` |
| 1.3 Card resolution | **Done** — `resolve.py`, tests in `tests/test_resolve.py` |
| 1.4 Collection commands | **Done** — `collection.py`, tests in `tests/test_collection.py` |
| 1.5 Storage location commands | **Done** — `storage.py`, tests in `tests/test_storage.py` |
| 1.6 Deck import | **Done** — `decks.py`, tests in `tests/test_decks.py` |
| 1.7 Deck management | **Done** — `decks.py`, tests in `tests/test_decks.py` |
| 1.8 CLI entry point | **Done** - `cli.py`, tests in `tests/test_cli.py` |

**Decisions already made** (full reasoning in DECISIONS.md): import credits the
variant-free base printing (D-016); a deck-name clash asks before replacing
(D-017); `foil_quantity` is added but stays 0 (D-018); storage locations are in
scope (D-019); bulk entry is decks-first (D-011).

---

## Task 1.1 — Schema additions

Three additions to `src/db_setup.py`, all reflected in `--reset`.

**a. `collection.foil_quantity`** — `INTEGER NOT NULL DEFAULT 0`. Stays 0 in v1.

**b. Storage locations.** A card's copies can be split across places, so this
needs two tables, not a column:

```sql
CREATE TABLE storage_locations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,        -- 'Binder A', 'Trade box', 'Bulk'
    notes      TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE collection_placements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_image_id TEXT NOT NULL REFERENCES cards(card_image_id),
    location_id   INTEGER NOT NULL REFERENCES storage_locations(id) ON DELETE CASCADE,
    quantity      INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(card_image_id, location_id)
);
```

How the numbers relate — `collection.quantity` stays the single source of truth
for how many you own; placements only say where the *loose* ones are:

```
owned    = collection.quantity
in decks = Σ deck_cards.quantity for physical decks
loose    = owned − in decks
placed   = Σ collection_placements.quantity
unplaced = loose − placed
```

`placed` must never exceed `loose`. If it does, **warn, don't block** — placement
is gradual bookkeeping across 5000+ cards and must never gate recording ownership.

**c. `available_cards` view**

```sql
CREATE VIEW available_cards AS
SELECT c.card_image_id,
       c.base_card_id,
       col.quantity                              AS owned,
       COALESCE(committed.qty, 0)                AS committed,
       col.quantity - COALESCE(committed.qty, 0) AS available
FROM cards c
JOIN collection col ON col.card_image_id = c.card_image_id
LEFT JOIN (
    SELECT dc.base_card_id, SUM(dc.quantity) AS qty
    FROM deck_cards dc
    JOIN decks d ON d.id = dc.deck_id
    WHERE d.is_physical = 1
    GROUP BY dc.base_card_id
) committed ON committed.base_card_id = c.base_card_id
```

> **Caveat:** commitment is gameplay-level but ownership is printing-level
> (D-003), so this over-subtracts when you own several printings of one card.
> Aggregate per `base_card_id` when answering "can I build this?".

**Done when:** `--reset` produces all three, and `SELECT * FROM available_cards` runs.

---

## Task 1.2 — Limitless decklist parser

`src/deck_parser.py` — pure functions, no DB.

```
Leader: 1 Monkey D. Luffy OP05-001
DON!!: x10
Character:
4 Monkey D. Luffy OP05-097
Event:
2 Gum-Gum Giant OP05-060
```

- Strip `Leader:` / `DON!!:` / `Character:` / `Event:` / `Stage:` headers.
- **Ignore `DON!!` lines entirely** (D-002).
- Card lines are `{quantity} {card_name} {card_id}`; the id is the last token.
- Capture `leader_id` separately.
- Return `(leader_id, [(base_card_id, quantity, name), ...], warnings)`.
- Tolerate blank lines, stray whitespace, `x4` / `4x` spellings.
- **Never raise on a bad line** — collect into `warnings`.

**Done when:** tests cover a full decklist, a headerless list, DON lines,
malformed lines, and an empty string.

---

## Task 1.3 — Card resolution

`src/resolve.py`

- `resolve_base_id(conn, base_card_id)` → all printings, `[]` if unknown.
- `default_printing(conn, base_card_id)` → the variant-free printing; falls back
  to lowest-sorting where none exists (defensive — after D-014 every card has one).
- `unknown_ids(conn, ids)` → ids absent from `cards`, for import warnings.

**Done when:** correct for a single-printing card, a 7-printing card (`P-029`),
and a nonexistent id.

---

## Task 1.4 — Collection commands

`src/collection.py`

- `add` / `set` / `remove <card_image_id> <qty>` — remove floors at 0 and
  **never deletes the row** (D-005).
- `show <base_card_id>` — every printing with owned / in-decks / placed / loose.
- `list --set OP05 --color Red --owned-only`

Guard: refuse any change that would make `available` negative.

**Done when:** quantities round-trip, zero rows persist, guard triggers.

---

## Task 1.5 — Storage location commands

`src/storage.py`

- `location add|list|rename|delete <name>`
- `place <card_image_id> <location> <qty>` / `unplace <card_image_id> <location> [qty]`

```
collection show OP05-097

  owned        7
  Red Luffy    4   (physical deck)
  Binder A     2
  Trade box    1
  unplaced     0
```

Deleting a location cascades its placements but **never** touches
`collection.quantity` — you still own the cards.

**Done when:** copies split across locations, `unplaced` computes correctly.

---

## Task 1.6 — Deck import

`src/decks.py` — `import_deck(path, name, is_physical)`:

1. Parse (1.2), resolve ids (1.3), collect unknowns as warnings.
2. **If the deck name exists, stop and ask** — replace, or create a second deck (D-017).
3. Create the `decks` row; insert `deck_cards` at gameplay level.
4. Credit `collection` at the default printing (D-016) — **physical decks only**.
   A wishlist deck records what you want and must never touch the collection.
5. Warn above 4 copies; never block (D-004).
6. Print a summary: deck created, cards added, collection credited, warnings.

**One transaction, rolled back on any failure** — a partial deck never lands.

Wrong printing after import is expected: fix with `collection set OP05-097 0`
then `collection set OP05-097_p1 4`.

**Done when:** a real decklist creates deck + collection together, a wishlist
import leaves the collection untouched, and a name clash prompts.

---

## Task 1.7 — Deck management

`src/decks.py` — `list`, `show <deck>` (owned vs missing per card),
`delete <deck>` (cascades `deck_cards`, never touches `collection`),
`set-physical <deck> --on/--off`.

**Done when:** toggling `is_physical` visibly changes availability elsewhere, and
`show` on a wishlist deck lists exactly what's missing.

---

## Task 1.8 — CLI entry point

`src/cli.py` — `argparse` subcommands over the modules above.

```
python -m src.cli deck import mydeck.txt --name "Red Luffy" --physical
python -m src.cli deck import wanted.txt --name "Purple Doffy" --wishlist
python -m src.cli deck show "Red Luffy"
python -m src.cli collection add OP05-097_p1 3
python -m src.cli collection show OP05-097
python -m src.cli location add "Binder A"
python -m src.cli place OP05-097 "Binder A" 2
```

**Done when:** every command runs from one entry point with `--help`.

---

## Phase 1 complete when

- [x] A real decklist imports cleanly — deck + collection together
- [x] A wishlist deck imports without touching the collection
- [x] Loose cards can be placed across multiple storage locations
- [x] `available_cards` correctly shows cards locked in physical decks
- [x] Toggling `is_physical` moves cards between locked and free
- [x] `deck show` on a wishlist deck lists what's missing
- [x] Unknown ids warn without blocking
- [x] All commands reachable from `python -m src.cli`
- [x] Tests cover the parser, resolution, and availability math
- [x] Committed, and `DESIGN.md` updated with the three new tables
