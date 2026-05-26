# One Piece TCG Collection Manager & Deck Builder — Design Document

> **Status:** Phase 0 complete. Schema locked. Ready for Phase 1.
> **Last updated:** Design conversation, May 2026.

This document is the source of truth for the project. Always load this into Claude Code at the start of every session.

---

## 1. Project Goals

Build a personal-use desktop app to manage a One Piece TCG collection and decks. Specifically:

- Track total card inventory at the printing level (base art, alt arts, reprints all distinct).
- Import decks from Limitless TCG paste format.
- Distinguish cards committed to physical (sleeved) decks from cards available to use.
- Filter and search the collection to build new decks, optionally constrained to owned cards.
- Show deck completion status (owned vs missing) for any deck list.
- Shareable as a downloadable app to friends — not hosted, not multi-user.

**Non-goals (v1):** condition/grading tracking, price-history tracking, public hosting, multi-user accounts, Japanese-language card support.

---

## 2. Tech Stack

| Layer | Choice | Reasoning |
|---|---|---|
| App shell | Tauri | Small binaries (~10 MB vs Electron 100+ MB), native webview, easy to share as installer |
| UI framework | React (or Svelte — decide at Phase 3) | Standard, well-supported |
| Local DB | SQLite | Single-file, fast, perfect for offline desktop app |
| Sync script | Python | Easier for data work; sync runs separately from the UI |
| Card data source | OptcgAPI | Free, community-maintained, covers all sets and promos |

---

## 3. Core Mental Model

**Collection = total physical inventory**, tracked at printing level.
- "I own 3 copies of OP05-097" and "I own 2 copies of OP05-097_p1" are separate rows.
- Collection quantity = all copies you own, regardless of whether they're in a deck.

**Availability is computed, not stored:**
- `available = collection.quantity - SUM(deck_cards.quantity WHERE deck.is_physical = TRUE)`
- A card is "locked" when committed to a physical sleeved deck.

**Decks are tracked at the gameplay card level** (base card ID), not printing level.
- A deck says "4x Monkey D. Luffy OP05-097" — it doesn't care which printing is sleeved.
- The app resolves availability against whichever printings you own.

**Collection lifecycle (Option A):** When quantity drops to zero, keep the row with quantity = 0. Never delete collection rows. This preserves history and makes re-adding easy.

**UI safety rule:** If editing a collection quantity would make available copies go negative (i.e., more committed to decks than you'd own), the UI must warn and block the save.

---

## 4. Deck Import Format (Limitless TCG)

```
Leader: 1 Monkey D. Luffy OP05-001
DON!!: x10
Character:
4 Monkey D. Luffy OP05-097
3 Roronoa Zoro OP05-020
...
Event:
2 Gum-Gum Giant OP05-060
...
Stage:
1 Thousand Sunny OP05-080
```

Parser rules:
- Strip the `Leader:`, `DON!!:`, `Character:`, `Event:`, `Stage:` section headers.
- Ignore `DON!!` line entirely.
- Each card line: `{quantity} {card_name} {card_id}`
- Match by `card_id` (base card ID) against the `cards` table.
- Unmatched cards → warn user, don't block import.

---

## 5. Phases

| Phase | Description |
|---|---|
| 0 | Setup & data foundation — SQLite schema + OptcgAPI sync script |
| 1 | Collection + decks (CLI) — import decks, track collection quantities |
| 2 | Deck completion & insights (CLI) — owned vs missing, availability |
| 3 | Minimal UI (Tauri + React) — replace CLI with windows |
| 4 | Interactive deck builder — build decks inside the app |
| 5 | Images & polish |
| 6+ | Future ideas |

---

## 6. Working Principles

- **Schema first.** Don't write application code until the schema is right.
- **CLI before UI.** Validate logic in Python before building Tauri/React.
- **Log don't crash.** Bad API rows go to log tables, not exceptions.
- **Idempotent sync.** Running the sync twice should not duplicate data.
- **No DON cards.** Do not call `/api/allDonCards/`. DON!! cards excluded from v1.

---

## 7. Database Schema

### `cards` — one row per printing

```sql
CREATE TABLE cards (
    card_image_id      TEXT PRIMARY KEY,           -- e.g. "OP05-097_p1" or "OP05-097"
    base_card_id       TEXT NOT NULL,              -- e.g. "OP05-097" (gameplay identity)
    printing_variant   TEXT,                       -- e.g. "p1", "p2"; NULL for base print
    name               TEXT NOT NULL,
    category           TEXT NOT NULL,              -- "Leader", "Character", "Event", "Stage"
    cost               INTEGER,                    -- NULL for Leaders
    power              INTEGER,
    counter            INTEGER,                    -- raw int for Characters; NULL forced for Leaders/Events/Stages
    color              TEXT,                       -- e.g. "Red", "Blue/Red"
    card_type          TEXT,                       -- raw sub_types string from API
    effect             TEXT,                       -- card text; NULL allowed (vanilla cards)
    has_trigger        BOOLEAN NOT NULL DEFAULT 0,
    has_blocker        BOOLEAN NOT NULL DEFAULT 0,
    has_rush           BOOLEAN NOT NULL DEFAULT 0,
    has_double_attack  BOOLEAN NOT NULL DEFAULT 0,
    has_banish         BOOLEAN NOT NULL DEFAULT 0,
    set_code           TEXT,                       -- e.g. "OP-05"
    rarity             TEXT,
    image_url          TEXT,
    last_synced        TIMESTAMP
);

CREATE INDEX idx_cards_base_card_id ON cards(base_card_id);
CREATE INDEX idx_cards_category ON cards(category);
CREATE INDEX idx_cards_color ON cards(color);
CREATE INDEX idx_cards_set_code ON cards(set_code);
CREATE INDEX idx_cards_trigger ON cards(has_trigger);
CREATE INDEX idx_cards_blocker ON cards(has_blocker);
CREATE INDEX idx_cards_rush ON cards(has_rush);
CREATE INDEX idx_cards_double_attack ON cards(has_double_attack);
CREATE INDEX idx_cards_banish ON cards(has_banish);
```

### `card_types` — normalized sub_types (one row per type per card)

```sql
CREATE TABLE card_types (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    base_card_id TEXT NOT NULL,   -- links to cards.base_card_id (not printing-specific)
    type_name    TEXT NOT NULL,
    UNIQUE(base_card_id, type_name)
);

CREATE INDEX idx_card_types_base_card_id ON card_types(base_card_id);
CREATE INDEX idx_card_types_type_name ON card_types(type_name);
```

### `known_types` — canonical type list for sub_types parsing

```sql
CREATE TABLE known_types (
    type_name   TEXT PRIMARY KEY,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `unknown_type_log` — review queue for unparseable sub_types

```sql
CREATE TABLE unknown_type_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_image_id   TEXT NOT NULL,
    raw_sub_types   TEXT NOT NULL,
    detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved        BOOLEAN NOT NULL DEFAULT 0
);
```

### `skipped_cards_log` — cards skipped during sync

```sql
CREATE TABLE skipped_cards_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_card_data   TEXT,          -- JSON of the raw API row
    reason          TEXT NOT NULL, -- e.g. "missing card_image_id"
    detected_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `collection` — physical card inventory

```sql
CREATE TABLE collection (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    card_image_id  TEXT NOT NULL REFERENCES cards(card_image_id),
    quantity       INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(card_image_id)
);
```

### `decks` — deck metadata

```sql
CREATE TABLE decks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    leader_id    TEXT,                                 -- base_card_id of the leader (no FK — base_card_id is non-unique by design)
    is_physical  BOOLEAN NOT NULL DEFAULT 0,  -- TRUE = sleeved, locks cards
    notes        TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### `deck_cards` — cards in a deck (gameplay level)

```sql
CREATE TABLE deck_cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id      INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    base_card_id TEXT NOT NULL,   -- gameplay identity, not printing-specific
    quantity     INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
    UNIQUE(deck_id, base_card_id)
);
```

### `app_settings` — key/value config store

```sql
CREATE TABLE app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
```

---

## 8. Sanitization Rules

Applied by the sync script to every raw API row before inserting into `cards`:

1. **Null normalization.** Convert `"NULL"` (string), `"?"`, `""`, whitespace-only → actual SQL NULL.
2. **Integer conversion.** `cost`, `power`, `counter` — safe string-to-int; null-likes → NULL.
3. **Counter normalization.** For Leaders, Events, Stages: force `counter = NULL` regardless of API value. For Characters: preserve the integer (including `0` for no-counter Characters).
4. **Color normalization.** Replace space separator with `/`. `"Blue Red"` → `"Blue/Red"`.
5. **Attribute normalization.** Strip spaces around `/`. `"Slash / Special"` → `"Slash/Special"`.
6. **Sub_types parsing.** Longest-match against `known_types`. Matches → rows in `card_types` (deduplicated by `base_card_id`). Failed parses → entry in `unknown_type_log`.
7. **Printing variant extraction.** Parse from `card_image_id` suffix. `"OP05-097_p1"` → `printing_variant = "p1"`. `"OP05-097"` → NULL.
8. **Keyword detection.** Scan `effect` for `[Trigger]`, `[Blocker]`, `[Rush]`, `[Double Attack]`, `[Banish]`. Set corresponding `has_*` boolean. Simple substring match — false positives on cards that reference a keyword without having it are accepted as a v1 limitation.
9. **Image URL.** Store full URL exactly as returned by API.
10. **Last synced.** Stamp every row with current timestamp on each sync.
11. **Empty effect handling.** Vanilla cards are valid. API often returns `"NULL"` string — rule #1 converts to SQL NULL. Do NOT enforce NOT NULL on `effect`.
12. **Skip cards with NULL `card_image_id`.** Log to `skipped_cards_log` with reason `"missing card_image_id"`. Do not insert.
13. **DON cards excluded.** Do not call `/api/allDonCards/`. Excluded from v1.
14. **Promo cards included.** Call `/api/allPromoCards/` in addition to main card endpoint.

---

## 9. API Response Structure (OptcgAPI)

Three endpoints provide complete card data (no single `/api/allCards/` exists):

| Endpoint | What it returns |
|---|---|
| `/api/allSetCards/` | Booster set cards (bare JSON array) |
| `/api/allSTCards/` | Starter deck cards (bare JSON array) |
| `/api/promos/filtered/?rarity=PR` | Promo cards (`/api/allPromoCards/` is 404) |
| `/api/sets/card/{card_id}/` | Single card lookup |

Key fields returned per card:

| API field | Maps to |
|---|---|
| `card_image_id` | `cards.card_image_id` (PRIMARY KEY) |
| `card_set_id` | base of `base_card_id` |
| `card_name` | `cards.name` |
| `card_type` | `cards.category` |
| `card_cost` | `cards.cost` |
| `card_power` | `cards.power` |
| `counter_amount` | `cards.counter` |
| `card_color` | `cards.color` |
| `sub_types` | parsed into `card_types` |
| `card_text` | `cards.effect` |
| `set_id` | `cards.set_code` |
| `rarity` | `cards.rarity` |
| `card_image` | `cards.image_url` |

---

## 10. Open Items

- [x] ~~Confirm whether promo cards appear in main `/api/allCards/` or need a separate endpoint call.~~ Resolved: no `/api/allCards/` exists; use three endpoints (set, ST, promo). Promos need `/api/promos/filtered/?rarity=PR`.
- [x] ~~Bootstrap initial `known_types` list.~~ Resolved: 243 seed types from live API data. 3 remaining unknowns are numeric data errors.
- [ ] Decide React vs Svelte at Phase 3 start.

---

## 11. Deferred / Out of Scope (v1)

- DON!! cards
- Card condition / grading
- Price tracking
- Japanese cards
- Promo printings with no API `card_image_id` (logged, revisit when API catches up)
- Multi-user / cloud sync

---

*End of design document.*
