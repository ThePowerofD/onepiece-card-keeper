# Phase 1 — Collection & Decks (CLI)

> **Goal:** Track what you physically own, where it is, and what's committed to
> which deck — plus build decks for cards you *don't* own yet and see what's missing.
> **Deliverable:** CLI commands to import Limitless decklists, place loose cards
> in storage, and see what's free to build with.
> **Still no UI.** Phase 3 replaces the CLI with windows; the logic written here
> is what the UI will call.

Depends on Phase 0 (`cards` populated — 4338 printings / 2648 gameplay cards).

| Task | Status |
|---|---|
| 1.1 Schema additions | Not started |
| 1.2 Limitless decklist parser | Not started |
| 1.3 Card resolution (id → printing) | Not started |
| 1.4 Collection commands | Not started |
| 1.5 Storage location commands | Not started |
| 1.6 Deck import | Not started |
| 1.7 Deck management commands | Not started |
| 1.8 CLI entry point | Not started |

---

## Workflow this phase is built around

The user's stated input order: **decks first, then loose cards.**

> "the way it's easier for me inputting info at first is by decks and then
> loose cards/stored cards."

So `deck import` is the primary bulk-entry path — it creates the deck *and*
credits the collection in one transaction. Loose/stored cards are entered
afterwards. Expect 5000+ physical cards, so bulk paths matter far more than
one-card-at-a-time entry.

**Two kinds of deck**, both from the same import command:

| `is_physical` | Meaning | Effect |
|---|---|---|
| `TRUE` | A real sleeved deck you own | Locks its cards — subtracted from what's free elsewhere |
| `FALSE` | A deck you want to build ("non-existent") | Locks nothing; `deck show` reports what you're missing |

---

## Core model reminders (DESIGN.md §3)

- **Collection is printing-level** (`card_image_id`). "3x OP05-097" and
  "2x OP05-097_p1" are separate rows.
- **Decks are gameplay-level** (`base_card_id`). A deck says "4x OP05-097" and
  does not care which printing is sleeved.
- **Availability is computed, never stored:**
  `available = quantity − SUM(deck_cards.quantity WHERE decks.is_physical)`
- **Zero-quantity rows are kept**, never deleted (Option A).
- **No 4-copy enforcement** — warn, never block.

---

## Task 1.1 — Schema additions

**Goal:** Add what Phase 1 needs but Phase 0 never built.

### 1.1a `collection.foil_quantity`

`ALTER TABLE collection ADD COLUMN foil_quantity INTEGER NOT NULL DEFAULT 0`

Stays 0 in v1. Added now while `collection` is empty so it costs nothing later.
See `notes/foil_and_printing_variants.md` — most foils already have
their own `card_image_id`, so this column is a reserve for the rarer case where
the *same* printing exists as both foil and non-foil.

### 1.1b Storage locations

A card's copies can be split across places (2 in a binder, 1 in a trade box), so
this needs two tables, not a column:

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

**How the numbers relate.** `collection.quantity` stays the single source of
truth for how many you own. Placements only describe where the *loose* ones are:

```
owned          = collection.quantity
in decks       = SUM(deck_cards.quantity) for physical decks
loose          = owned − in decks
placed         = SUM(collection_placements.quantity)
unplaced       = loose − placed        ← "I own these but haven't said where"
```

`placed` must never exceed `loose`. If it does, **warn — don't block** (log
don't crash). Placement is bookkeeping you'll do gradually across 5000+ cards;
it must never be a precondition for recording that you own something.

### 1.1c `available_cards` view

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

> **Known subtlety:** commitment is gameplay-level but ownership is
> printing-level, so a deck needing 4x OP05-097 can't say *which* printing is
> sleeved. This view therefore over-subtracts when you own several printings of
> the same card. Aggregate per `base_card_id` when answering "can I build
> this?", not per printing.

**Done when:** `foil_quantity` exists, both storage tables exist,
`SELECT * FROM available_cards` runs, and `db_setup.py --reset` recreates it all.

---

## Task 1.2 — Limitless decklist parser

**Goal:** Pure function, text in → structured decklist out. No DB access.

File: `src/deck_parser.py`

```
Leader: 1 Monkey D. Luffy OP05-001
DON!!: x10
Character:
4 Monkey D. Luffy OP05-097
Event:
2 Gum-Gum Giant OP05-060
Stage:
1 Thousand Sunny OP05-080
```

Rules:
- Strip `Leader:` / `DON!!:` / `Character:` / `Event:` / `Stage:` headers.
- **Ignore `DON!!` lines entirely** — DON is out of scope.
- Each card line is `{quantity} {card_name} {card_id}`; the id is the last token.
- The leader line carries its own quantity — capture `leader_id` separately.
- Return `(leader_id, [(base_card_id, quantity, name), ...], warnings)`.
- Tolerate blank lines, stray whitespace, and `x4` / `4x` spellings.
- **Never raise on a bad line** — collect it into `warnings`.

**Done when:** tests cover a full decklist, a headerless list, DON lines,
malformed lines, and an empty string.

---

## Task 1.3 — Card resolution

**Goal:** Turn a decklist `base_card_id` into real rows, and pick which printing
a bulk collection credit lands on.

File: `src/resolve.py`

- `resolve_base_id(conn, base_card_id)` → all printings, or `[]` if unknown.
- `default_printing(conn, base_card_id)` → prefer the variant-free printing
  (`printing_variant IS NULL`); fall back to the lowest-sorting printing when
  none exists (8 promo cards have no variant-free printing).
- `unknown_ids(conn, ids)` → ids absent from `cards`, for import warnings.

**Done when:** correct for a card with one printing, a card with many (`P-029`
has 7), and an id that doesn't exist.

---

## Task 1.4 — Collection commands

File: `src/collection.py`

- `add <card_image_id> <qty>` — increment, creating the row if absent.
- `set <card_image_id> <qty>` — absolute set.
- `remove <card_image_id> <qty>` — decrement, floor at 0, **never delete the row**.
- `show <base_card_id>` — every printing with owned / in-decks / placed / loose.
- `list --set OP05 --color Red --owned-only` — browse.

Guard: if a change would make `available` negative, warn and refuse the save.

**Done when:** quantities round-trip, zero rows persist, negative guard triggers.

---

## Task 1.5 — Storage location commands

File: `src/storage.py`

- `location add <name> [--notes]` / `location list` / `location rename` / `location delete`
- `place <card_image_id> <location> <qty>` — record where copies are.
- `unplace <card_image_id> <location> [qty]` — remove a placement.
- `collection show <base_card_id>` gains a location breakdown:

```
collection show OP05-097

  owned        7
  Red Luffy    4   (physical deck)
  Binder A     2
  Trade box    1
  unplaced     0
```

Deleting a location cascades its placements but **never** changes
`collection.quantity` — you still own the cards, you just haven't said where.

**Done when:** a card's copies can be split across locations, and `unplaced`
is computed correctly.

---

## Task 1.6 — Deck import

**Goal:** The bulk-entry path. One transaction, both tables.

File: `src/decks.py`

`import_deck(path, name, is_physical)`:
1. Parse the file (1.2).
2. Resolve every id (1.3); collect unknowns as warnings.
3. **If a deck with this name already exists, stop and ask** whether to replace
   it or create a second deck. Never silently double the collection.
4. Create the `decks` row (`leader_id`, `is_physical`, `notes`).
5. Insert `deck_cards` at gameplay level.
6. Credit `collection` at the **default (variant-free) printing** from 1.3 —
   only for physical decks. A wishlist deck (`is_physical = FALSE`) records
   what you want and must **never** touch the collection.
7. Warn on any card exceeding 4 copies; never block.
8. Print a summary: deck created, cards added, collection credited, warnings.

**Atomic:** one transaction, roll back on any failure — a partial deck never lands.

**Reassigning a printing later:** the import credits the base printing, which
will sometimes be wrong (you sleeved an alt art). Fix with
`collection set OP05-097 0` + `collection set OP05-097_p1 4`. This is expected
and normal — accuracy at import time would mean a prompt per card.

**Done when:** a real decklist creates deck + collection together, a wishlist
import leaves the collection untouched, and a name clash prompts.

---

## Task 1.7 — Deck management

File: `src/decks.py`

- `list` — all decks with card counts and physical/wishlist flag.
- `show <deck>` — full list, owned vs missing per card.
- `delete <deck>` — cascades `deck_cards`; does **not** touch `collection`.
- `set-physical <deck> --on/--off` — toggles whether it locks cards.

**Done when:** toggling `is_physical` visibly changes availability elsewhere,
and `show` on a wishlist deck lists exactly what you're missing.

---

## Task 1.8 — CLI entry point

File: `src/cli.py` — `argparse` subcommands over the modules above.

```
python -m src.cli deck import mydeck.txt --name "Red Luffy" --physical
python -m src.cli deck import wanted.txt --name "Purple Doffy" --wishlist
python -m src.cli deck list
python -m src.cli deck show "Red Luffy"
python -m src.cli collection add OP05-097_p1 3
python -m src.cli collection show OP05-097
python -m src.cli location add "Binder A"
python -m src.cli place OP05-097 "Binder A" 2
```

**Done when:** every command runs from one entry point with `--help` text.

---

## Decisions made for this phase

Recorded 2026-08-11. Full reasoning in `notes/DECISIONS.md`.

| Question | Decision |
|---|---|
| Which printing does a deck import credit? | The variant-free base printing; correct it afterwards if you sleeved an alt art |
| Re-importing the same decklist? | Stop and ask whether to replace or create a second deck |
| `foil_quantity`? | Add it now, keep it 0 in v1 |
| Storage locations? | In scope for Phase 1 — reverses the earlier Phase 6+ deferral |

---

## Phase 1 Complete When

- [ ] A real Limitless decklist imports cleanly, deck + collection together
- [ ] A wishlist deck imports without touching the collection
- [ ] `collection` reflects owned quantities at printing level
- [ ] Loose cards can be placed across multiple storage locations
- [ ] `available_cards` correctly shows cards locked in physical decks
- [ ] Toggling a deck's `is_physical` moves cards between locked and free
- [ ] `deck show` on a wishlist deck lists what's missing
- [ ] Unknown card ids warn without blocking the import
- [ ] All commands reachable from `python -m src.cli`
- [ ] Tests cover the parser, resolution, and the availability math
- [ ] All code committed to git

Ready for Phase 2.
