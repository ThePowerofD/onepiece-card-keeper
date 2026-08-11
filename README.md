# OPTCG Manager

Personal desktop app for managing a One Piece TCG collection and deck building.
Tracks what you physically own, where it is, what's sleeved into which deck, and
what you're missing to build a deck you want.

Python CLI today. Becomes a Tauri + React desktop app in Phase 3.

---

## Where the project is right now

**Last updated: 2026-08-11 — Phases 0 and 1 complete.**

The card database is built, and the full collection/deck CLI works: import
Limitless decklists, track what you own at printing level, record where loose
cards are stored, and see what's missing for a deck you want to build.

Your own collection is still empty — start by importing the decks you've
already sleeved (see *Using it* below), which populates the collection for free.

| | |
|---|---|
| Unique printings (`cards`) | 4,338 |
| Unique gameplay cards (`base_card_id`) | 2,648 |
| Sets covered | OP01–OP16, EB01–EB04, ST01–ST30, PRB01–02, promos |
| Bad rows in logs | none |
| Tests | 246 passing |

**Known limitation:** OptcgAPI lags the real game. The game is out to **OP17**;
the API only has **OP16**. Re-run the sync when it catches up — no code change
needed.

**Next up:** Phase 2 — deck completion & insights. `deck show` already reports
owned vs missing per card; Phase 2 adds cross-deck views, "what can I build",
and richer filtering.

---

## Quickstart

From a fresh clone to a working database:

```bash
venv\Scripts\activate                 # Windows; source venv/bin/activate elsewhere
pip install -r requirements.txt -r requirements-dev.txt

python -m src.db_setup --reset        # create the schema (drops any existing DB)
python -m src.known_types             # seed the card-type vocabulary
python -m src.sync                    # fetch all cards from OptcgAPI

python -m pytest tests/ -v
python -m pytest tests/ --cov=src --cov-report=term-missing   # coverage (99%)
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

...and 246 passing tests. Anything else means something changed upstream —
`Unknown sub_types` above 0 usually means a new set introduced a card type;
add it to `SEED_TYPES` in `src/known_types.py` and re-sync.

Add `--use-cache` to `python -m src.sync` to reuse the saved JSON in
`data/cache/` instead of hitting the API. Handy while developing; delete the
cache files to force a fresh fetch.

`data/optcg.db` is gitignored and always regenerable by the three commands above.

---

## Using it

Everything runs through one entry point. `--help` works at every level.

```bash
# Decks — importing a sleeved deck also credits your collection
python -m src.cli deck import mydeck.txt --name "Red Luffy" --physical
python -m src.cli deck import wanted.txt --name "Purple Doffy" --wishlist
python -m src.cli deck list
python -m src.cli deck show "Red Luffy"          # owned vs missing per card
python -m src.cli deck set-physical "Purple Doffy"
python -m src.cli deck delete "Red Luffy"        # collection untouched

# Collection — quantities are per printing (OP05-097 vs OP05-097_p1)
python -m src.cli collection add OP05-097_p1 3
python -m src.cli collection set OP05-097 4
python -m src.cli collection remove OP05-097 1
python -m src.cli collection show OP05-097       # all printings, decks, locations
python -m src.cli collection list --set OP-05 --owned-only

# Storage — where the loose cards physically are
python -m src.cli location add "Binder A" --notes "red decks"
python -m src.cli location list
python -m src.cli location rename "Binder A" "Binder One"
python -m src.cli location delete "Binder One"   # you still own the cards
python -m src.cli place OP05-097 "Binder A" 2
python -m src.cli unplace OP05-097 "Binder A" 1  # omit the number to remove all
```

Every command takes `--db PATH` to work against a different database file.

**A physical deck locks its cards** — they stop counting as available elsewhere.
A wishlist deck locks nothing and reports what you're missing. Toggle with
`deck set-physical`.

**Import credits the base printing.** A decklist says `OP05-097` without saying
which art is sleeved, so import assumes the plain printing. If you actually
sleeved an alt art, fix it after: `collection set OP05-097 0` then
`collection set OP05-097_p1 4`.

---

## Layout

```
src/
  db_setup.py     schema creation; connect() is the shared connection factory
  api_client.py   OptcgAPI HTTP client (three endpoints)
  sanitize.py     pure cleaning functions, one per rule — no DB, no side effects
  known_types.py  seeds the card-type vocabulary
  sync.py         orchestrator: fetch → sanitize → upsert
  deck_parser.py  Limitless decklist format — pure, never raises
  resolve.py      gameplay id → printings; picks the printing an import credits
  collection.py   what you own, with the availability guard
  storage.py      storage locations and placements
  decks.py        deck import and management
  cli.py          argparse entry point over all of the above
tests/            246 tests
data/             optcg.db and cache/ (both gitignored)
```

---

## Documentation

Five files, each with one job. Nothing is documented in two places.

| File | What it's for | How often it changes |
|---|---|---|
| **README.md** | This file — what it is, where it stands, how to run it | Every session |
| **[DESIGN.md](DESIGN.md)** | What **exists** — model, schema, rules, API | When the system changes |
| **[PHASE_1.md](PHASE_1.md)** | Phase 1 task list — **complete**, delete when Phase 2 starts | While working the phase |
| **[DECISIONS.md](DECISIONS.md)** | *Why* things are the way they are | Append-only, never edited |
| **[CLAUDE.md](CLAUDE.md)** | Operating instructions for Claude Code | Rarely |

**If you're picking this up cold:** read this file, then `DECISIONS.md`.
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
