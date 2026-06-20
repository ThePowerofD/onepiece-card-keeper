# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

One Piece TCG collection manager and deck builder. Currently Python-only (CLI sync pipeline); will add Tauri + React UI in later phases. Source of truth: `DESIGN.md` (schema, rules, architecture) and `PHASE_0.md` (current phase tasks). Read both before making changes.

## Commands

```bash
# Environment (Windows)
venv\Scripts\activate
pip install -r requirements.txt

# Full sync pipeline (run in order for a fresh DB)
python -m src.db_setup --reset
python -m src.known_types
python -m src.sync --use-cache

# Tests
python -m pytest tests/ -v
python -m pytest tests/test_sanitize.py -v          # single file
python -m pytest tests/test_sanitize.py::ToIntTests  # single class
```

## Architecture

The sync pipeline runs in sequence: **API fetch → sanitize → upsert into SQLite**.

- `src/db_setup.py` — Schema creation. 9 tables, 11 indexes. `connect()` is the shared DB connection factory.
- `src/api_client.py` — HTTP client for OptcgAPI. Three fetch functions (`fetch_all_set_cards`, `fetch_all_st_cards`, `fetch_all_promos`). `--use-cache` saves raw JSON to `data/cache/` to avoid repeated API calls during dev.
- `src/sanitize.py` — Pure functions, one per sanitization rule (DESIGN.md §8). No DB access, no side effects.
- `src/known_types.py` — Seeds the `known_types` table. `SEED_TYPES` list is the canonical type vocabulary; expand it when `unknown_type_log` shows new types after a sync.
- `src/sync.py` — Orchestrator. `FIELD_ALIASES` maps API field names to internal names (the API uses `card_name`, `card_type`, `card_cost`, etc. — not the names in DESIGN.md §9's original assumptions). `sanitize_row()` applies all rules; `sync()` drives the fetch-sanitize-upsert loop.

**Key data model concept:** `card_image_id` (e.g., `OP05-097_p1`) is the printing-level primary key. `base_card_id` (e.g., `OP05-097`) is the gameplay-level identity. Decks reference `base_card_id`; collection tracks `card_image_id`.

## Working Principles

- Schema first, CLI before UI, log don't crash, idempotent sync, no DON cards.
- Bad API rows go to `skipped_cards_log` or `unknown_type_log`, never exceptions.
- Running sync twice must not duplicate data (INSERT OR REPLACE).
- The DB file (`data/optcg.db`) is gitignored — always regenerable via the sync pipeline.
