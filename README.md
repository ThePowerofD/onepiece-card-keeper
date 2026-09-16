# OPTCG Manager

Personal offline app for managing a One Piece TCG collection and deck building.
Tracks what you physically own, where it is, what's sleeved into which deck, and
what you're missing to build a deck you want.

Python CLI today. Becomes a local web app — FastAPI + React in a browser tab —
in Phase 2. (Tauri was dropped before the UI started; see D-023.)

---

## Where the project is right now

**Last updated: 2026-09-15 — Phases 0 and 1 complete. Phase 2 re-planned.**

The card database is built, and the full collection/deck CLI works: import
Limitless decklists, track what you own at printing level, record where loose
cards are stored, and see what's missing for a deck you want to build.

**Your own collection is still empty** — 4,338 cards synced, zero owned. That is
the honest state: the backend is finished by tests and has never met a real card.
Loading it is now the first milestone of Phase 2, ahead of any new code.

Phase 2 was re-cut on 2026-09-15 around one sentence — *I open the app, see my
real cards with images, know where each one is, and can check a decklist against
what I own.* The old Phase 2 (CLI-only insights) became Phase 3 and will be built
as UI instead of terminal tables. See **D-024** for the reasoning.

| | |
|---|---|
| Unique printings (`cards`) | 4,338 |
| Unique gameplay cards (`base_card_id`) | 2,648 |
| Sets covered | OP01–OP16, EB01–EB04, ST01–ST30, PRB01–02, promos |
| Bad rows in logs | none |
| Tests | 341 passing |

**Known limitation:** OptcgAPI lags the real game. The game is out to **OP17**;
the API only has **OP16**. Re-run the sync when it catches up — no code change
needed.

**Next up:** [PHASE_2.md](PHASE_2.md) M0 — import your sleeved decks with the CLI
below. No code; it's the step that turns every remaining design guess into a
fact, and it bootstraps the collection for free.

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

...and 341 passing tests. Anything else means something changed upstream —
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
python -m src.cli deck delete "Red Luffy"        # copies return to just-loose

# Collection — you own gameplay cards; the art is optional detail
python -m src.cli collection add OP05-097 4                  # unsorted, into just-loose
python -m src.cli collection add OP05-097 2 --art p1 --to "Mewtwo Binder"
python -m src.cli collection move OP05-097 2 --to "Mewtwo Binder"
python -m src.cli collection move OP05-097 4 --from "Red Luffy" --from-kind deck --to just-loose
python -m src.cli collection set-art OP05-097 1 p1 --in "Mewtwo Binder"
python -m src.cli collection remove OP05-097 1               # sold or traded away
python -m src.cli collection show OP05-097       # arts, containers, totals
python -m src.cli collection list --set OP05 --owned-only
python -m src.cli collection list --search "Luffy"

# Storage — where the copies physically are.
# Every location has a --kind: storage | binder | loose | loan
python -m src.cli location add "OP13 Box" --kind storage
python -m src.cli location add "Mewtwo Binder" --kind binder --notes "alt arts"
python -m src.cli location add "Friend 1" --kind loan
python -m src.cli location list                  # grouped by kind
python -m src.cli location show "Mewtwo Binder"  # what's in it, art by art
python -m src.cli location show                  # defaults to just-loose
python -m src.cli location rename "OP13 Box" "OP13 Storage"
python -m src.cli location delete "OP13 Storage" # copies return to just-loose

# Loans — the card is still yours, it's just somewhere else
python -m src.cli loan OP05-097 1 "Friend 1" --art p1
```

**Names are unique per kind, not globally** — a box and a binder can both be
"Box 1". When a bare name is ambiguous the command refuses rather than guessing;
add `--kind` to say which you meant.

```
$ collection move OP05-097 2 --to "Box 1"
error: 'Box 1' exists as binder and as storage - say which with --kind
```

A deck can share a name with a location too, so `--kind deck` is available
wherever a container is named.

Every command takes `--db PATH` to work against a different database file.

**Every copy is somewhere.** Adding a card with no container puts it in
`just-loose`, the built-in loose pile, and that's also where `remove` and `loan`
take from unless you say otherwise. `just-loose` can't be renamed or deleted.

**The art is optional.** A decklist names `OP05-097` without saying which art is
sleeved, so imported copies stay *unsorted* rather than guessing the plain
printing. Record the art later, where the copies actually are:
`collection set-art OP05-097 1 p1 --in "Mewtwo Binder"`. It splits the row; your
total never moves.

**A physical deck holds real copies** — they stop counting as available
elsewhere. It can also be **incomplete**: take four cards out to build another
deck and `deck show` reports them as not sleeved until you put them back. A
wishlist deck holds no copies and just reports what you're missing.

**Nothing goes negative.** Moving or removing more than a container holds is an
error that writes nothing, and tells you what's actually there.

### First run, in this order

You can't move a card you don't own yet, and you own nothing until a deck import
or an `add` puts copies in.

```bash
python -m src.cli deck import mydeck.txt --name "Red Luffy" --physical
python -m src.cli location add "Binder A" --kind binder
python -m src.cli collection add OP05-097 2                  # into just-loose
python -m src.cli collection move OP05-097 2 --to "Binder A"
python -m src.cli collection show OP05-097   # where they are, and which arts
python -m src.cli deck show "Red Luffy"      # owned, sleeved, missing
```

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
tests/            341 tests
data/             optcg.db and cache/ (both gitignored)
```

---

## Documentation

Five files, each with one job. Nothing is documented in two places.

| File | What it's for | How often it changes |
|---|---|---|
| **README.md** | This file — what it is, where it stands, how to run it | Every session |
| **[DESIGN.md](DESIGN.md)** | What **exists** — model, schema, rules, API | When the system changes |
| **[PHASE_2.md](PHASE_2.md)** | Phase 2 milestones — the MVP, **current** | While working the phase |
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