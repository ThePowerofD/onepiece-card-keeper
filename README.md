# OPTCG Manager

Personal offline app for managing a One Piece TCG collection and deck building.
Tracks what you physically own, where it is, what's sleeved into which deck, and
what you're missing to build a deck you want.

Python CLI today. Becomes a local web app — FastAPI + React in a browser tab —
in Phase 2. (Tauri was dropped before the UI started; see D-023.)

---

## Getting started

Everything you need to use it. (Developing it? See
[Developer quickstart](#developer-quickstart).)

**You need** Python 3.10 or newer, and internet for the first sync.

**Once**, about a minute — most of it downloading the cards:

```bash
git clone https://github.com/ThePowerofD/onepiece-card-keeper.git
cd onepiece-card-keeper
python -m venv venv
venv\Scripts\activate                # Windows; source venv/bin/activate elsewhere
pip install -e .                     # installs the `optcg` command

optcg setup                          # create the database, download every card
```

**Every time**, activate the venv and type `optcg`. That opens the shell, where
you type commands one after another without any prefix:

```
optcg
optcg> deck show "Reiju"
optcg> exit
```

Every command also works on its own line from the terminal, with `optcg` in
front: `optcg deck show "Reiju"`.

**Help is built in, at three levels**, and every level ends with examples:

```
help                     the guide: how it fits together, and where to start
help collection          every command in a group, one line each
help collection add      one command: what it does, its options, examples
```

In the shell type them as shown; from the terminal add `--help` instead
(`optcg collection add --help`). If you get a command wrong, it shows that
command's examples and where more help is, and a mistyped command name gets a
"did you mean". Errors say what to do next — `add --in "Reiju"` when the cards
you named are in your deck, `optcg setup` when the database isn't set up yet.

**Three ideas:**

- **Card** — the id printed on the card, like `OP06-042`, or part of its name:
  `collection add cosette 2`. Many cards share a name, so a name that matches
  several gives you a numbered list to pick from (or `c` to cancel).
- **Art** — which printing you have (`base`, `p1`, ...). Optional: copies start
  unsorted and you can say later.
- **Container** — where a card physically is: a deck, or a location you create.
  Cards with nowhere named go to `just-loose`.

**Seven commands cover almost everything:**

```
deck import mydeck.txt --name "Reiju"      a sleeved deck - its cards join your collection
deck show "Reiju"                          what you own vs what's missing
collection add OP06-060 2                  loose cards you own
collection show OP06-060                   how many, which arts, where they are
find ichiji                                every card with that in its name, and what you own
location add "Binder A" --kind binder      a place to keep cards
collection move OP06-060 2 --to "Binder A"
```

Decklists can be Limitless-style or deckbuilder exports — `help deck import`
shows both.

**Two habits that keep your data safe:**

- Add `--dry-run` to an import you're unsure of — it shows the result and saves
  nothing.
- Run `backup export` at the end of a session. Your decks and collection exist
  only on your machine; that file is your copy. See [Backups](#backups).

When a new set comes out, `optcg sync` downloads it. Your collection is never
touched.

---

## Where the project is right now

**Last updated: 2026-10-29 — Phase 2, M0 under way: real cards are going in.**

The card database is built, and the full collection/deck CLI works: import
decklists (Limitless or deckbuilder-export format, from a file or pasted in),
track what you own and which art, record where every copy is, and see what's
missing for a deck you want to build. `optcg shell` makes entering
a lot at once quick.

**Real data has started.** The first sleeved deck, Reiju, is in: 51 copies of 18
cards, arts not yet sorted. From here the database holds data that exists
nowhere else, so it's protected now (D-032, D-034): destructive commands
snapshot the database first and ask when something would be lost, imports take
`--dry-run`, and `backup export` writes a JSON copy that can be moved to
another machine.

Phase 2 was re-cut on 2026-09-15 around one sentence — *I open the app, see my
real cards with images, know where each one is, and can check a decklist against
what I own.* The old Phase 2 (CLI-only insights) became Phase 3 and will be built
as UI instead of terminal tables. See **D-024** for the reasoning.

| | |
|---|---|
| Unique printings (`cards`) | 4,712 |
| Unique gameplay cards (`base_card_id`) | 2,797 |
| Sets covered | OP01–OP17, EB01–EB04, ST01–ST36, PRB01–02, promos |
| Bad rows in logs | none |
| Owned so far | 51 copies, 1 deck |
| Tests | 557 passing |

**Known limitation:** OptcgAPI lags the real game. It caught up to **OP17** by
2026-10-29 (synced that day: 158 new OP17 printings, ST31–ST36, and three new
card types). When a newer set is out, its ids are missing until the API catches
up — re-run the sync then. A new set usually needs new types in `SEED_TYPES`
first; the sync summary says so.

**Next up:** the rest of [PHASE_2.md](PHASE_2.md) M0 — import every sleeved
deck. The safety work before bulk entry is done: backups (D-032), confirmations
and dry-run (D-034), and a sync that reports printings that stopped refreshing
instead of losing track of them (D-035).

---

## Developer quickstart

From a fresh clone to a working database, with the tests:

```bash
venv\Scripts\activate                 # Windows; source venv/bin/activate elsewhere
pip install -e ".[dev]"               # the optcg command, plus pytest

optcg setup                           # schema + card types + every card (never resets)

python -m pytest tests/ -v
python -m pytest tests/ --cov=optcg --cov-report=term-missing   # coverage (99%)
```

**Verify it worked.** You should see:

```
=== Sync summary ===
  Total rows fetched: 4716
  Inserted/updated:   4712
  Skipped (no id):    0
  Unknown sub_types:  0
  Field-shift repairs:3
  cards table count:  4712
  Stale printings:    0
```

...and 557 passing tests. Anything else means something changed upstream —
`Unknown sub_types` above 0 usually means a new set introduced a card type;
add it to `SEED_TYPES` in `optcg/known_types.py` and re-sync. `Stale printings`
above 0 means the API stopped sending some rows; they are kept, and if you own
any the summary says what to do (D-035).

Add `--use-cache` to `optcg sync` to reuse the saved JSON in
`data/cache/` instead of hitting the API. Handy while developing; delete the
cache files to force a fresh fetch.

`data/optcg.db` is gitignored. The **card catalog** in it is regenerable by the
three commands above; **your decks, locations and copies are not** — they exist
nowhere else. See [Backups](#backups).

---

## Using it

Everything runs through one entry point. `--help` works at every level.

For entering a lot at once, `optcg shell` opens an `optcg>` prompt
that takes the same commands without the prefix, until `exit`:

```
optcg> collection add OP05-097 4
optcg> collection add OP05-020 3 --to "Binder A"
optcg> exit
```

Inside the shell, `help` shows a getting-started guide, and `help deck` or
`help deck import` shows a group or a single command with examples.

```bash
# Decks — importing a sleeved deck also credits your collection
optcg deck import mydeck.txt --name "Red Luffy" --physical
optcg deck import wanted.txt --name "Purple Doffy" --wishlist
optcg deck paste --name "Red Luffy"  # type/paste the list, finish with: end
optcg deck list
optcg deck show "Red Luffy"          # owned vs missing per card
optcg deck set-physical "Purple Doffy"
optcg deck delete "Red Luffy"        # asks first; copies return to just-loose
optcg deck import new.txt --name "Enel" --dry-run   # see the result, save nothing

# Finding a card — by id in any case, or by part of its name
optcg find vinsmoke ichiji                   # every match, what it is, how many you own
optcg collection show cosette                # a name works anywhere an id does

# Collection — you own gameplay cards; the art is optional detail
optcg collection add OP05-097 4                  # unsorted, into just-loose
optcg collection add OP05-097 2 --art p1 --to "Mewtwo Binder"
optcg collection move OP05-097 2 --to "Mewtwo Binder"
optcg collection move OP05-097 4 --from "Red Luffy" --from-kind deck --to just-loose
optcg collection set-art OP05-097 1 p1 --in "Mewtwo Binder"
optcg collection remove OP05-097 1               # sold or traded away
optcg collection show OP05-097       # arts, containers, totals
optcg collection list --set OP05 --owned-only
optcg collection list --search "Luffy"

# Storage — where the copies physically are.
# Every location has a --kind: storage | binder | loose | loan
optcg location add "OP13 Box" --kind storage
optcg location add "Mewtwo Binder" --kind binder --notes "alt arts"
optcg location add "Friend 1" --kind loan
optcg location list                  # grouped by kind
optcg location show "Mewtwo Binder"  # what's in it, art by art
optcg location show                  # defaults to just-loose
optcg location rename "OP13 Box" "OP13 Storage"
optcg location delete "OP13 Storage" # asks first if it holds copies

# Loans — the card is still yours, it's just somewhere else
optcg loan OP05-097 1 "Friend 1" --art p1

# Backups — see below
optcg backup export my_collection.json
optcg backup import my_collection.json   # REPLACES what's here; asks first
optcg backup snapshot --label before-sorting
optcg backup list
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

### Backups

Two kinds, for two jobs (D-032):

- **Snapshots — undo on this machine.** A full copy of the database in
  `data/backups/`. Taken **automatically** before `deck delete`,
  `location delete`, a deck import that replaces, `backup import` and
  `db_setup --reset`; the newest 10 automatic ones are kept. Ones you make with
  `backup snapshot` are never deleted. **To undo**, close the app and copy the
  snapshot over `data/optcg.db`.
- **Export — moving to another machine.** A JSON file of your decks, locations
  and copies only. It names cards by their printed ids, so it still loads after
  code updates. On the new machine: clone, set up, **sync first**, then
  `backup import my_collection.json`. Import **replaces** everything there — it
  never merges — after a snapshot and a confirmation.

`data/backups/` is gitignored: it's your data, not code.

**Before it happens** (D-034): `deck delete`, and `location delete` on a location
that holds copies, say what will move and wait for `y`; `--yes` skips that.
`deck import`, `deck paste` and `backup import` take `--dry-run`, which runs the
command against a throwaway copy and prints exactly what it would do. Use it on
a decklist you're unsure of: deleting a wrongly imported deck leaves its copies
in just-loose, it doesn't remove them.

### First run, in this order

You can't move a card you don't own yet, and you own nothing until a deck import
or an `add` puts copies in.

```bash
optcg deck import mydeck.txt --name "Red Luffy" --physical
optcg location add "Binder A" --kind binder
optcg collection add OP05-097 2                  # into just-loose
optcg collection move OP05-097 2 --to "Binder A"
optcg collection show OP05-097   # where they are, and which arts
optcg deck show "Red Luffy"      # owned, sleeved, missing
```

---

## Layout

```
optcg/
  db_setup.py     schema creation; connect() is the shared connection factory
  api_client.py   OptcgAPI HTTP client (three endpoints)
  sanitize.py     pure cleaning functions, one per rule — no DB, no side effects
  known_types.py  seeds the card-type vocabulary
  sync.py         orchestrator: fetch → sanitize → upsert
  deck_parser.py  Limitless and deckbuilder-export decklists — pure, never raises
  resolve.py      what you typed → card ids: exact ids, name search, typo fixes
  collection.py   what you own, with the availability guard
  storage.py      storage locations and placements
  decks.py        deck import and management
  backup.py       snapshots, and JSON export/import of your own data
  cli.py          argparse entry point over all of the above
tests/            557 tests
data/             optcg.db, cache/ and backups/ (all gitignored)
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