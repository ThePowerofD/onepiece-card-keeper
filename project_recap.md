# One Piece TCG Collection Manager — Planning Recap & Handoff

> **Purpose of this document:** Comprehensive recap of all planning decisions for the `optcg-manager` project, written to hand off to Claude Code. Captures user-driven decisions, compromises, deferred features, and current implementation status.
>
> **Authoritative source files in repo:** `DESIGN.md` and `PHASE_0.md`. This recap summarizes both plus context that may not have made it into those files.

---

## 1. Project Overview

A **personal-use desktop app** to manage a One Piece TCG collection and decks. Built primarily for the user, shareable as a downloadable installer to friends.

- **Not hosted, not multi-user, no auth.**
- User already owns 4500+ cards.
- Limitless TCG paste format is the primary deck input.
- Workflow assumption: import physical decks first, then loose cards.

### Core Goals
- Track total card inventory at **printing level** (base art, alt arts, reprints all distinct).
- Import decks from Limitless TCG paste format.
- Distinguish cards committed to physical (sleeved) decks from cards available to use.
- Filter and search the collection to build new decks.
- Show deck completion status (owned vs missing) for any deck list.

---

## 2. 🔑 Things the User Specifically Asked For

These decisions came directly from explicit user requests or preferences. Each one is honored in the schema and design.

1. **"Freed up vs. in a deck" distinction must be visible.**
   → Solved via the `available_cards` SQL view (no separate table). Math: `available = total_owned − committed to physical decks`.

2. **Keep a `foil_quantity` column even though it stays empty in v1.**
   → User reasoning: "easier removing it than adding it later." Confirmed correct trade-off. Column reserved, always `0` in v1.

3. **`DEFAULT 0` on quantity columns.**
   → User said: "Default 0 for security measure and not crashing is ok." Kept.

4. **Do NOT enforce the 4-copy rule.**
   → User reasoning: "sometimes broken by the rules itself, special cases for some cards." App shows a *warning*, never blocks saving. Warning is suppressible via `app_settings`.

5. **Skip DON!! cards entirely in v1.**
   → User: "we can skip it for the moment as this will strictly go as collection not part of decks." `/api/allDonCards/` is not called.

6. **Limitless format is the primary deck input method.**
   → Confirmed core import workflow. Parser must strip section headers (`Leader:`, `DON!!:`, `Character:`, `Event:`, `Stage:`), ignore `DON!!` lines, match by `card_id`.

7. **Tech stack guidance.**
   → Resulted in Tauri + React (or Svelte) + SQLite. Small binaries, easy sharing.

8. **Workflow ordering: decks first, then loose cards.**
   → User: "the way it's easier for me inputting info at first is by decks and then loose cards/stored cards." Phase 1 reflects this: decks import command increments collection AND creates the deck atomically.

9. **English-only data.**
   → JP-exclusive cards out of scope. OptcgAPI is English-focused anyway.

10. **Willing to let go of tracking alt-arts inside decks for simplicity.**
    → User: "I am willing to let go the tracking of alt arts in decks if it will simplify things ahead." Decks track gameplay-level.

---

## 3. 🤝 Compromises & Trade-offs the User Accepted

| Compromise | What was given up | What was gained |
|---|---|---|
| **Decks track gameplay-level (`base_card_id`), not printing-level** | Can't know if the alt-art Zoro vs base-art Zoro is sleeved in Deck A | Much simpler schema; matches Limitless import format; deck legality checks are trivial |
| **No foil tracking in v1** | No foil-vs-non-foil distinction for the same printing | Cleaner data; column reserved so easy to activate later without migration |
| **`card_types` has no foreign key to `cards`** | Slightly less DB-level integrity | Cleaner gameplay-level model; no per-printing type duplication. Integrity enforced at sync-script level |
| **Keyword abilities are substring matches** (`[Blocker]`, `[Rush]`, `[Double Attack]`, `[Banish]`, `[Trigger]`) | False positives possible on cards that mention a keyword without having it | Simple, no parser needed for v1 |
| **No 4-copy rule enforcement** | DB doesn't guarantee deck legality | Handles special cards that legitimately break the rule |
| **Counter stored as raw integer for Characters; `NULL` forced for Leaders/Events/Stages** | Slightly more complex than collapsing everything to NULL | Distinguishes "Character with 0 counter" from "card type that can't have a counter." Future counter values (e.g. +500, +3000) need no schema change |
| **English-only card data** | No JP-exclusive cards | Simpler sync logic; OptcgAPI is English-focused |
| **Trigger text not stored as separate column** | Have to parse from `effect` if ever needed | Just `has_trigger` boolean; cleaner schema |
| **Zero-quantity collection rows kept (Option A)** | Slightly larger collection table over time | Preserves history, notes, and metadata when you sell/trade away |
| **`leader_id` in `decks` is plain TEXT (no FK)** | No DB-level guarantee the leader exists in `cards` | Required fix: SQLite needs UNIQUE/PK for FK targets, and `base_card_id` is non-unique by design (alt arts share it) |
| **Strings for colors with `LIKE` queries (instead of normalized table)** | Slightly less elegant than full normalization | Simpler queries; works cleanly because no OPTCG color name is a substring of another |
| **Separate junction table for `card_types`** (after reversing earlier advice) | One extra table; two queries to render a card | Clean filtering by type without `LIKE '%type%'` false matches; indexed lookups |

---

## 4. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| App shell | **Tauri** | ~10 MB binaries vs Electron's 100+ MB |
| UI framework | **React or Svelte** | Decision deferred to Phase 3 start |
| Local DB | **SQLite** | Single file, offline-first, backup-friendly |
| Backend lang | **Python** | Used in Phase 0–2 CLI work |
| Data source | **OptcgAPI** | `https://optcgapi.com`, configurable via `OPTCG_API_BASE` env var |

---

## 5. Schema Summary

### Tables
- `cards` — one row per **printing** (PK `card_image_id`)
- `card_types` — junction, references `base_card_id` (no FK, composite PK)
- `collection` — what the user owns (PK `card_image_id`)
- `decks` — deck metadata (PK `deck_id`, `is_physical` flag)
- `deck_cards` — deck contents at gameplay level (PK `deck_id + base_card_id`)
- `available_cards` — **VIEW**, computes `owned − committed` on demand
- `known_types` — canonical sub-type list (PK `type_name`, has `approved` flag)
- `unknown_type_log` — sub-types not in `known_types` during sync (don't crash, just log)
- `skipped_cards_log` — cards with NULL `card_image_id` (don't crash, just log)
- `app_settings` — generic key-value table (e.g., `suppress_4_copy_warning`, `last_card_sync`)

### Key Design Choices Recap
- **Model A** confirmed: collection stores total inventory; "available" is computed via SQL view.
- **Printing-level** identity (`card_image_id`) — alt arts and reprints are distinct rows.
- **Gameplay-level** for things shared across printings (`card_types`, `deck_cards`).
- **`is_physical` flag** distinguishes sleeved-up decks from wishlists.
- **`notes` column on collection** = escape hatch for one-off info.
- **`local_image_path` column on cards** = placeholder for Phase 5, NULL until then.

---

## 6. Sanitization Rules (14 total)

1. API `"NULL"` strings → SQL `NULL`.
2. Empty `effect` is valid (vanilla cards). Don't enforce NOT NULL.
3. Skip cards with NULL `card_image_id` → log to `skipped_cards_log`.
4. Scan `effect` for `[Trigger]` / `[Blocker]` / `[Rush]` / `[Double Attack]` / `[Banish]` → set `has_*` booleans. Substring match; false positives accepted as v1 limitation.
5. Stamp every row with `last_synced` on each sync.
6. Force `counter = NULL` for Leaders / Events / Stages (regardless of what API returns).
7. Normalize colors: API `"Blue Red"` → consistent `"Blue/Red"` format.
8. Extract `printing_variant` suffix from `card_image_id` (e.g., `_p1`, `_r1`).
9. DON cards skipped entirely (don't call `/api/allDonCards/`).
10. Promos pulled from `/api/allPromoCards/` (separate endpoint confirmed by IDs like `P-102`).
11. Deduplicate `card_types` rows when multiple printings share a `base_card_id`.
12. Unknown sub-types → log to `unknown_type_log`; don't crash sync.
13. Cast numeric fields (`cost`, `power`, `counter`, etc.) from string to integer.
14. Image URLs stored exactly as the API returns them.

### Sub-type parsing gotcha
Sub-types use **space delimiter** and type names themselves contain spaces (e.g., `"Straw Hat Crew Supernovas"`). Must use **longest-match against `known_types`**, not naive split. This is why `known_types` exists as a curated canonical list.

---

## 7. "Available to Build With" Math

Three variants used in different contexts:

### Version 1: "What's free right now?"
For collection browser, building new decks:
```
available = total_owned − sum(quantity in all PHYSICAL decks)
```
Wishlist decks (`is_physical = FALSE`) do not subtract.

### Version 2: "Can I build this specific deck?"
For deck completion view. **Critical subtlety:** the deck's own cards count as available to itself:
```
available_to_X = total_owned − sum(quantity in OTHER physical decks)
missing = max(0, needed − available_to_X)
```

### Version 3: Cannibalization (Phase 6+)
"If I broke down Deck B, could I build Deck X?":
```
available_to_X = total_owned − sum(quantity in OTHER physical decks EXCEPT Deck B)
```

---

## 8. Phase Roadmap

| Phase | Description | Status |
|---|---|---|
| **0** | Setup & data foundation — schema + OptcgAPI sync | 🟡 In progress (0.1–0.4 done) |
| **1** | Collection + decks (CLI) — Limitless import, deck/loose commands | ⏳ Future |
| **2** | Deck completion & insights (CLI) — owned/missing, filters | ⏳ Future |
| **3** | Minimal UI (Tauri + React/Svelte) — Collection/Decks/Detail screens | ⏳ Future |
| **4** | Interactive deck builder — filterable browser, live legality, Limitless export | ⏳ Future |
| **5** | Images & polish — local image cache, hover previews, settings page | ⏳ Future |
| **6+** | Future ideas (see section 10) | ⏳ Future |

---

## 9. Phase 0 Status (Current)

| Task | Description | Status |
|---|---|---|
| 0.1 | Project setup + git init | ✅ Done |
| 0.2 | Python venv (`requirements.txt`: `requests>=2.31.0`) | ✅ Done |
| 0.3 | Folder structure + placeholders for 0.5–0.8 | ✅ Done |
| 0.4 | DB schema setup (`src/db_setup.py`) — 9 tables, 11 indexes, idempotent, `--reset` flag | ✅ Done |
| **0.5** | **OptcgAPI client (`src/api_client.py`)** | ⏳ **NEXT** |
| 0.6 | Sanitization functions (`src/sanitize.py`) + unit tests in `tests/test_sanitize.py` | ⏳ Pending |
| 0.7 | Known types seed (bootstrap `known_types`) | ⏳ Pending |
| 0.8 | Main sync script (`src/sync.py`) — orchestrates client + sanitizer + DB | ⏳ Pending |

### Status Notes for Claude Code
- **Nothing has been committed to git yet** — do this on the home machine.
- **Schema correction already applied**: `decks.leader_id REFERENCES cards(base_card_id)` was invalid (SQLite requires UNIQUE/PK FK target). Now plain `TEXT`. DESIGN.md should reflect this.
- **API base URL is configurable**: defaults to `https://optcgapi.com`, overridable via `OPTCG_API_BASE` env var.
- **Live API verification was blocked in sandbox** — endpoints `/api/allCards/`, `/api/allPromoCards/` exist per DESIGN.md but exact shapes (bare list vs `{"data": [...]}`) are unverified. **Task 0.5 must probe the live API first and print raw response structure before assuming field names.**
- **Phase 0 done-when**: `SELECT COUNT(*) FROM cards` returns 5000+.

---

## 10. 💭 Deferred Ideas (Considered but Postponed)

These were discussed during planning and explicitly punted. Documented here so they don't get lost.

### Things explicitly deferred (column or hook exists; not active in v1)
- **Foil-vs-non-foil tracking** — `foil_quantity` column exists, always 0 in v1.
- **Trigger text parsing** — only the `has_trigger` boolean for now. Parsing the actual trigger text into a separate column/table is Phase 6+.
- **Image local cache** — `local_image_path` column exists, populated in Phase 5.
- **Auto-detection of new sub-types** — `known_types.approved` flag exists, but v1 uses manual workflow only (review `unknown_type_log`, add manually).
- **Promo printings without API IDs** — skipped at sync time, logged to `skipped_cards_log`. Track ownership at `base_card_id` level until API catches up. Re-sync periodically.

### Phase 6+ future ideas (not committed, no schema support yet)
- **Storage location tracking** (binders, boxes, drawers). Would need a new `storage_locations` table.
- **Cannibalization analysis** ("if I broke down Deck B, could I build Deck X?"). Version 3 of the availability math.
- **"Decks I could build with N more cards" suggestions.**
- **Trade tracking** — log of trades in/out.
- **Tournament meta tracking** — what decks are winning right now.
- **Win/loss records per deck.**
- **Last played date per deck.**
- **Deck tags / archetype** ("aggro", "control", color combos).
- **Format flag** (Standard vs other) — currently no formats exist in OPTCG, but reserved for future.
- **DON!! cards** — separate table or extend `cards` with `Don` category. Would call `/api/allDonCards/`.
- **JP-exclusive cards** — multi-language support.
- **Price history / time-series** — OptcgAPI exposes current `inventory_price` and `market_price`; long-term tracking is a rabbit hole.
- **Foil-vs-non-foil distinction usage** (activate the dormant `foil_quantity` column).
- **Trigger-text parsing for filterable trigger search.**
- **Wishlist binders / acquisition tracking** beyond the current `is_physical = FALSE` deck mechanism.
- **Multiple physical owners** (shared collections with a partner, etc.).

### Things explicitly out of scope (will not be added)
- Hosting / multi-user / auth.
- Condition / grading tracking.
- Deck playtesting / simulation.

---

## 11. Open Items

- [ ] Confirm whether promos appear in main `/api/allCards/` or require separate `/api/allPromoCards/` call. (Promo IDs like `P-102` suggest separate. Verify at Task 0.5.)
- [ ] Bootstrap initial `known_types` list — start empty + accumulate from `unknown_type_log`, or seed from community list. Task 0.7.
- [ ] Decide React vs Svelte at Phase 3 start.
- [ ] Verify exact OptcgAPI response shape (bare list vs `{"data": [...]}` wrapped) at Task 0.5.
- [ ] First commit to git (on home machine).

---

## 12. Working Principles for Claude Code

These are the operating rules the user has chosen for this project. Claude Code should honor them.

1. **Schema first.** Don't write application code until the schema is right.
2. **CLI before UI.** Validate logic in Python before building Tauri/React.
3. **Log don't crash.** Bad API rows go to log tables (`skipped_cards_log`, `unknown_type_log`), not exceptions.
4. **Idempotent sync.** Running the sync twice should not duplicate data. Use UPSERTs.
5. **No DON cards.** Do not call `/api/allDonCards/`.
6. **One task per session.** Don't run multiple Phase 0 tasks in one Claude Code session. Open → do task → commit → close → repeat.
7. **Commit between tasks.** Each task is a clean rollback checkpoint.
8. **Read what Claude Code writes.** Especially edge cases — the user is learning.
9. **DESIGN.md is authoritative.** If Claude Code suggests a design change, update DESIGN.md *first*, then change code. Don't drift silently.
10. **When stuck, reduce scope.** If Task 0.8 (sync) feels too big, break it down: "first just fetch + print, no DB writes yet."
11. **Be the architect; let Claude Code be the typist.** Claude Code should not redesign without checking back with the user.

---

## 13. Data Quality Gotchas (Important for Claude Code)

- The OptcgAPI returns inconsistent NULLs: `null`, `"NULL"` (string), `"?"`, and `""`. Sanitization rule #1 handles this.
- Numeric fields arrive as **strings**. Cast to integers.
- The same gameplay card may appear in multiple product sets (e.g., Mary Geoise in OP-05 and PRB-02). `base_card_id` stays constant; `set_code` reflects the product origin per printing.
- `sub_types` uses **space delimiter** and type names themselves contain spaces. Cannot be split naively — must use longest-match against `known_types`.
- The 4-copy rule has exceptions. Show a warning, don't enforce. Make warning suppressible via `app_settings`.
- Saint Charlos-style cards: single type, simple. Five Elders-style cards: multiple types, multiple attributes. Both shapes must be handled.
- Dual-color cards: API returns `"Blue Red"` (space-separated). Normalize at sync time.

---

## 14. Ready-to-Use Prompt for Claude Code (Task 0.5)

> Read `DESIGN.md` and `PHASE_0.md`. Tasks 0.1–0.4 are complete. Implement Task 0.5: `src/api_client.py`. The API base URL should default to `https://optcgapi.com` but be overridable via the `OPTCG_API_BASE` env var. The exact endpoint shapes are unverified — start by hitting the API and printing the raw response structure before assuming field names. Done when: script prints total card count and one sample card. Stop there and show me the output before moving to Task 0.6.

---

## 15. Reference: OptcgAPI Sample Response

```json
{
    "inventory_price": 0.25,
    "market_price": 0.32,
    "card_name": "Lucy",
    "set_name": "Adventure on Kami's Island",
    "card_text": "[When Attacking]/[On Your Opponent's Attack] You may trash any number of Event or Stage cards from your hand. ...",
    "set_id": "OP15-EB04",
    "rarity": "L",
    "card_set_id": "OP15-002",
    "card_color": "Blue Red",
    "card_type": "Leader",
    "life": "4",
    "card_cost": null,
    "card_power": "5000",
    "sub_types": "Revolutionary Army Dressrosa",
    "counter_amount": 0,
    "attribute": "Strike",
    "date_scraped": "2026-04-04",
    "card_image_id": "OP15-002",
    "card_image": "https://www.optcgapi.com/media/static/Card_Images/OP15-002_9JJSMVX.jpg"
}
```

### API field → Schema column mapping

| API field | Schema column | Notes |
|---|---|---|
| `card_image_id` | `cards.card_image_id` (PK) | |
| `card_set_id` | `cards.base_card_id` | |
| `card_name` | `cards.name` | |
| `rarity` | `cards.rarity` | |
| `card_type` | `cards.category` | |
| `card_color` | `cards.colors` | normalize space → `/` |
| `card_cost` | `cards.cost` | string → integer |
| `card_power` | `cards.power` | string → integer |
| `counter_amount` | `cards.counter` | force NULL for non-Characters |
| `life` | `cards.life` | string → integer |
| `attribute` | `cards.attributes` | strip spaces around `/` |
| `card_text` | `cards.effect` | also scan for `[Trigger]`, `[Blocker]`, etc. → boolean cols |
| `set_id` | `cards.set_code` | |
| `set_name` | `cards.set_name` | |
| `card_image` | `cards.image_url` | |
| `inventory_price` | `cards.inventory_price` | |
| `market_price` | `cards.market_price` | |
| `sub_types` | parsed → `card_types` rows | longest-match against `known_types` |
| (suffix of `card_image_id`) | `cards.printing_variant` | parse `_p1`, `_r1`, etc. |
| (current time) | `cards.last_synced` | |

---

## 16. Counter Semantics Reference

| Category | Can have counter? | API value seen | Our normalized value |
|---|---|---|---|
| Leader | No | `null` or `0` (inconsistent) | `NULL` |
| Character | Yes (sometimes 0) | `0`, `1000`, `2000`, ... | preserve as-is |
| Event | No | `null` | `NULL` |
| Stage | No | `0` (inconsistent with Events) | `NULL` |

Resulting queries:
- "Any card usable as a counter" → `WHERE counter > 0`
- "Characters with no counter" → `WHERE category = 'Character' AND counter = 0`

---

## 17. Limitless Deck Format Reference

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
- Strip section headers (`Leader:`, `DON!!:`, `Character:`, `Event:`, `Stage:`).
- **Ignore `DON!!` line entirely.**
- Each card line: `{quantity} {card_name} {card_id}`.
- Match by `card_id` (base card ID) against `cards.base_card_id`.
- Unmatched cards → warn, don't block import.

---

*End of recap. Hand this off to Claude Code along with the existing `DESIGN.md` and `PHASE_0.md` files in the project root. This document is supplementary context; if there's any conflict, `DESIGN.md` wins as the source of truth.*