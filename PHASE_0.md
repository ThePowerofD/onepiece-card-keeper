# Phase 0 — Setup & Data Foundation

> **Goal:** A local SQLite database populated with all OPTCG card data from OptcgAPI.
> **Deliverable:** A working sync script and a `.db` file you can query.
> **No UI, no collection, no decks yet.** Just card data.

Complete tasks in order — later tasks depend on earlier ones.

| Task | Status |
|---|---|
| 0.1 Project setup | Done |
| 0.2 Python environment | Done |
| 0.3 Folder structure | Done |
| 0.4 DB schema setup | Done |
| 0.5 OptcgAPI client | Done |
| 0.6 Sanitization functions | Done |
| 0.7 Known types seed | Done |
| 0.8 Main sync script | Done |

---

## Task 0.1 — Project setup

**Goal:** Clean project folder with git initialized.

Steps:
1. Create folder: `mkdir optcg-manager && cd optcg-manager`
2. Initialize git: `git init`
3. Copy `DESIGN.md` and `PHASE_0.md` into the folder.
4. Ask Claude Code to generate a `.gitignore` for Python + SQLite.
5. First commit: `git add . && git commit -m "Initial design docs"`

**Done when:** `git log` shows your first commit.

---

## Task 0.2 — Python environment

**Goal:** Python 3.10+ with venv ready.

Steps:
1. Confirm Python: `python --version` or `python3 --version`
2. Create venv: `python -m venv venv`
3. Activate: `source venv/bin/activate` (Mac/Linux) or `venv\Scripts\activate` (Windows)
4. Create `requirements.txt` with: `requests`
5. Install: `pip install -r requirements.txt`
6. Add `venv/` to `.gitignore`
7. Commit.

**Done when:** `import requests` works in a Python shell inside the venv.

---

## Task 0.3 — Folder structure

**Goal:** Clean structure that scales.

```
optcg-manager/
├── DESIGN.md
├── PHASE_0.md
├── README.md
├── .gitignore
├── requirements.txt
├── venv/                  ← gitignored
├── data/
│   └── .gitkeep           ← placeholder so git tracks the folder
├── src/
│   ├── __init__.py
│   ├── db_setup.py        ← Task 0.4
│   ├── api_client.py      ← Task 0.5
│   ├── sanitize.py        ← Task 0.6
│   ├── known_types.py     ← Task 0.7
│   └── sync.py            ← Task 0.8
└── tests/
```

**Done when:** All folders and empty placeholder files exist. Commit.

---

## Task 0.4 — Database schema setup script

**Goal:** Script that creates the SQLite DB with all tables from DESIGN.md §7.

File: `src/db_setup.py`

Requirements:
- Connects to (or creates) `data/optcg.db`
- Creates all 8 tables: `cards`, `card_types`, `known_types`, `unknown_type_log`, `skipped_cards_log`, `collection`, `decks`, `deck_cards`, `app_settings`
- Creates all indexes from DESIGN.md §7
- Idempotent: safe to run twice (`CREATE TABLE IF NOT EXISTS`)
- CLI: `python -m src.db_setup` runs it; `--reset` flag drops and recreates (useful in dev)

**Done when:** `data/optcg.db` exists with correct schema, verified in DB Browser for SQLite.

---

## Task 0.5 — OptcgAPI client

**Goal:** Reusable functions for fetching card data.

File: `src/api_client.py`

Functions:
- `fetch_all_cards()` → list of card dicts from main endpoint
- `fetch_all_promos()` → list of promo cards
- `fetch_card_by_id(card_id)` → single card for debugging

Considerations:
- Handle pagination if the API uses it
- Retry once on network error, then raise
- Optional `--use-cache` flag to save/load raw JSON locally (avoids hammering API during dev)
- Do NOT call `/api/allDonCards/`

**Done when:** Script prints card count and one sample card from the API.

---

## Task 0.6 — Sanitization functions

**Goal:** Pure functions that clean raw API rows before DB insert.

File: `src/sanitize.py`

Functions (one per rule from DESIGN.md §8):
- `normalize_null(value)` → `"NULL"`, `"?"`, `""`, whitespace → `None`
- `to_int(value)` → safe string-to-int, returns `None` for null-likes
- `normalize_counter(category, raw_value)` → NULL for non-Characters; int for Characters
- `normalize_colors(value)` → `"Blue Red"` → `"Blue/Red"`
- `normalize_attributes(value)` → `"Slash / Special"` → `"Slash/Special"`
- `parse_subtypes(raw_string, known_types_list)` → `(matched_types, leftover)`
- `extract_printing_variant(card_image_id)` → `"p1"` or `None`
- `detect_keywords(effect_text)` → `{has_trigger, has_blocker, has_rush, has_double_attack, has_banish}`

**Done when:** Unit tests pass for each function with edge-case inputs.

---

## Task 0.7 — Known types seed

**Goal:** Populate `known_types` table with initial type list.

File: `src/known_types.py`

- Start with a hardcoded list of all known OPTCG sub_types (e.g. "Straw Hat Crew", "Marine", "Worst Generation", etc.)
- Script inserts them into `known_types` table on first run (idempotent)

**Done when:** `known_types` table has rows and `parse_subtypes` can match common types.

---

## Task 0.8 — Main sync script

**Goal:** Orchestrate the full sync: fetch → sanitize → insert.

File: `src/sync.py`

Steps the script performs:
1. Call `fetch_all_cards()` and `fetch_all_promos()`
2. For each card: apply all sanitization rules
3. Skip cards with NULL `card_image_id` → log to `skipped_cards_log`
4. Upsert into `cards` (INSERT OR REPLACE)
5. Upsert into `card_types`
6. Log unknown sub_types to `unknown_type_log`
7. Print summary: total inserted, skipped, unknown types found

CLI: `python -m src.sync`

**Done when:** Running the script populates `data/optcg.db` with all cards. Verify with:
```sql
SELECT COUNT(*) FROM cards;
SELECT * FROM cards WHERE base_card_id = 'OP05-097';
SELECT * FROM skipped_cards_log;
SELECT * FROM unknown_type_log;
```

---

## Phase 0 Complete When

- [ ] `data/optcg.db` exists and has cards
- [ ] `SELECT COUNT(*) FROM cards` returns a realistic number (5000+)
- [ ] At least one card with a printing variant exists (e.g. `_p1`)
- [ ] `skipped_cards_log` and `unknown_type_log` reviewed
- [ ] All code committed to git

Ready for Phase 1.
