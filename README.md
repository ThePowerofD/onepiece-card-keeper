# OPTCG Manager

Personal desktop app for managing a One Piece TCG collection and deck building.
Tracks what you physically own, where it is, what's sleeved into which deck, and
what you're missing to build a deck you want.

Python CLI today. Becomes a Tauri + React desktop app in Phase 3.

---

## Where the project is right now

**Last updated: 2026-08-11 — Phase 0 complete, Phase 1 planned but not started.**

The card database is built and populated. Nothing about a personal collection is
entered yet — `collection`, `decks` and `deck_cards` are empty. That's Phase 1.

| | |
|---|---|
| Unique printings (`cards`) | 4,338 |
| Unique gameplay cards (`base_card_id`) | 2,648 |
| Sets covered | OP01–OP16, EB01–EB04, ST01–ST30, PRB01–02, promos |
| Bad rows in logs | none |
| Tests | 53 passing |

**Known limitation:** OptcgAPI lags the real game. The game is out to **OP17**;
the API only has **OP16**. Re-run the sync when it catches up — no code change
needed.

**Next up:** Phase 1, tasks 1.1 → 1.8 in [PHASE_1.md](PHASE_1.md), in order.
Start with 1.1 (schema additions). The design questions are already answered.

---

## Quickstart

From a fresh clone to a working database:

```bash
venv\Scripts\activate                 # Windows; source venv/bin/activate elsewhere
pip install -r requirements.txt

python -m src.db_setup --reset        # create the schema (drops any existing DB)
python -m src.known_types             # seed the card-type vocabulary
python -m src.sync                    # fetch all cards from OptcgAPI

python -m pytest tests/ -v
```

**Verify it worked.** You should see:

```
=== Sync summary ===
  Total rows fetched: 4459
  Inserted/updated:   4459
  Skipped (no id):    0
  Unknown sub_types:  0
  Field-shift repairs:3
  cards table count:  4338
```

...and 53 passing tests. Anything else means something changed upstream —
`Unknown sub_types` above 0 usually means a new set introduced a card type;
add it to `SEED_TYPES` in `src/known_types.py` and re-sync.

Add `--use-cache` to `python -m src.sync` to reuse the saved JSON in
`data/cache/` instead of hitting the API. Handy while developing; delete the
cache files to force a fresh fetch.

`data/optcg.db` is gitignored and always regenerable by the three commands above.

---

## Layout

```
src/
  db_setup.py     schema creation; connect() is the shared connection factory
  api_client.py   OptcgAPI HTTP client (three endpoints)
  sanitize.py     pure cleaning functions, one per rule — no DB, no side effects
  known_types.py  seeds the card-type vocabulary
  sync.py         orchestrator: fetch → sanitize → upsert
tests/
data/             optcg.db and cache/ (both gitignored)
```

---

## Documentation

Four files, each with one job. Nothing is documented in two places.

| File | What it's for | How often it changes |
|---|---|---|
| **README.md** | This file — what it is, where it stands, how to run it | Every session |
| **[DESIGN.md](DESIGN.md)** | Schema, data model, sanitization rules, API reference | When the system changes |
| **[PHASE_1.md](PHASE_1.md)** | Current phase task list | While working the phase |
| **[notes/DECISIONS.md](notes/DECISIONS.md)** | *Why* things are the way they are | Append-only, never edited |

Plus [notes/foil_and_printing_variants.md](notes/foil_and_printing_variants.md)
— what the printing suffixes and foil treatments in the card data actually mean.

**If you're picking this up cold:** read this file, then `notes/DECISIONS.md`.
The decision log answers "why is this weird?", which is the question that costs
the most time and the one the code can't answer.

### Keeping the docs honest

- **Change the doc in the same commit as the behavior it describes.** This one
  practice matters more than any structure.
- **Decisions are append-only.** Changed your mind? Add a new entry marking the
  old one superseded. Never edit history.
- **Update "where the project is" above at the end of a session.** It's short on
  purpose — if it grows past a screen it becomes a chore, you'll stop updating
  it, and it'll start lying to you.
