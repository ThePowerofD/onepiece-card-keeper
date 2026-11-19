# Phase 2 — MVP: the app you actually use

**Goal:**

> I open the app, see my real cards with images, know where each one is, and can
> check a decklist against what I own.

Anything not serving that sentence waits for Phase 3. Rationale for the re-cut
and for dropping Tauri: **D-023** and **D-024** in [DECISIONS.md](DECISIONS.md).

**Deliverable:** FastAPI on `localhost` wrapping the existing modules, React +
Vite in the browser, and a collection with real cards in it.

Depends on Phase 1. Model and schema: [DESIGN.md](DESIGN.md).

| Milestone | Status |
|---|---|
| M0 Real data in — no code | Sample in: 172 copies, 5 places (2026-11-18); more decks to come |
| M1 Card browser + record the art | **Built** (branch `m1-browser`, D-045); owner's look + first real art pending |
| M2 Locations | **Built** in the browser (branch `m1-browser`, D-046): new place, move copies |
| M3 Decks & completion | **Built** (branch `m1-browser`, D-046): list, owned vs missing, paste with preview; building from a wishlist still to do |
| M4 Fast loose-card entry | **Built** (branch `m1-browser`, D-047): +1 from the grid into a chosen place, Undo; no -1 yet |

**The starting position (2026-11-19):** 4,712 printings synced, 639 tests
passing, and a real sample of 172 copies in 3 decks, a binder and just-loose.
The CLI covers the whole loop; no art has been recorded yet.

---

## M0 — Real data in

**No code.** This is the milestone, not a prerequisite to it.

```bash
optcg deck import <file.txt> --name "<deck>" --physical
optcg storage add "Binder A" --type binder --notes "red decks"
```

Import every deck you have built. Each one credits the collection as a
side effect (D-011), so this is also the cheapest bulk entry that exists.

Once there's real data, `optcg backup export` after a session of
entry is the copy that survives anything (D-032). Destructive commands already
snapshot by themselves. Add `--dry-run` to an import you're unsure of: a wrong
physical import can't be cleanly undone with `deck delete` (D-034).

**Watch for, and report rather than fix quietly:**

- The wrong printing credited — import assumes the plain art (D-016). Expected,
  not a bug. Correct with `collection set`.
- Unknown ids — cards from a set newer than the API. It has OP17 as of
  2026-10-29, so these should now be rare.
- 4-copy warnings — never blocking (D-004).

**Why this comes before any code:** D-011 (decks-first), D-016 (credit the base
printing) and D-019 (placement is optional) are all still untested guesses.
Reversing one now is an afternoon; reversing it after 5,000 cards are entered is
a migration. Build the UI against the real collection, not against fixtures.

**Done when:** `collection list` shows your actual cards, and every
built deck appears in `deck list`.

---

## M1 — Card browser with images, and recording the art

The milestone where it stops being a script. **Widened by D-044:** besides
showing the cards, M1 records which art a copy is - the one thing the owner
wants a screen for first, because an art is recognised by looking at it. It
was M2's `set-art` route; it moves here. Adding decks in the browser stays M3:
until then the remaining decks go in with `deck import` in the terminal.

Built in small, separately checkable steps - each one is usable before the
next starts.

### Before starting

- **Node.js (LTS) installed** - it isn't on this machine yet, and Vite needs it.
  One-time, by the owner.
- **The baseline is frozen** (see *Traceability* below).

### a. Logic-layer gaps

`collection.list_cards()` already takes `q` (name search) and `offset`. Still
missing before it can back a grid:

- **`image_url` in the SELECT** - it's on every `cards` row and currently unread.
- **A total count** for the same filters, so the grid can page.

Keep it returning plain rows; the HTTP layer does the JSON shaping.

### b. `optcg/web/api.py`

FastAPI. Imports the existing modules and calls them - **no SQL in this file.**
One dependency yields a connection from `db_setup.connect()`. New dependencies:
`fastapi`, `uvicorn`, and `httpx` for tests. Started by one command,
`optcg web`, on `localhost` only.

| Route | Calls |
|---|---|
| `GET /api/cards` | `collection.list_cards` - `set`, `color`, `category`, `owned_only`, `q`, `limit`, `offset` |
| `GET /api/cards/{base_card_id}` | `collection.show` -> `CardStatus` (arts, where every copy is, every printing) |
| `GET /api/sets` | distinct `set_code`, for the filter dropdown |
| `GET /api/places` | `storage.list_locations` + `decks.list_decks` - every place with its counts |
| `POST /api/copies/set-art` | `collection.set_art` - card, quantity, art, place (D-044) |

`CardStatus` and friends are `NamedTuple`s, so `._asdict()` gets most of the way
to a response body.

**Error mapping:** `CollectionError` / `DeckError` carry messages already fit to
print - map them to `400` with the message intact, and let the UI show it. Don't
invent a second error vocabulary. A set-art that asks for more copies than the
place holds is refused by `set_art` and writes nothing; the UI keeps its state.

### c. `ui/` - Vite + React

1. **Grid** - card images with name and owned count, filter bar (set / color /
   owned-only / search). Owned-only is the default view: it's the sample.
2. **Card detail** - every printing side by side, how many you own of each art,
   and *where* the copies are (each deck, binder, just-loose), unsorted first.
3. **Record the art** - on the detail, pick a place's unsorted copies, click the
   printing they are, say how many. Same rule as the CLI: the total in the place
   never changes, only how it's split by art (D-030).

Images hotlink `image_url` directly. The local cache stays Phase 5.

### Traceability - the sample must come through unchanged

The 2026-11-18 sample is the yardstick: **172 copies of 77 cards in 5 places**
(decks Reiju, Nico Robin YP, Eustass Kid Y at 51 each; binder `trading_binder`
11; `just-loose` 8), no art recorded.

- **Frozen baseline:** `data/backups/collection-20261118-235202.json`, kept as
  is, plus a one-page human list of it (place -> card -> copies -> art) for
  checking the screen by eye.
- **A parity test:** the baseline is loaded into a throwaway database and every
  API answer is compared with what the modules behind the CLI say for the same
  thing - per place, per card, totals. A mismatch fails the suite. The test
  reads a copy of the real sample from `tests/fixtures/` (card ids, counts,
  place names). The repo is public; the owner is fine with the sample being in
  it (2026-11-19). The database and `decklists/` stay gitignored.
- **Round trip on the first real write:** the first art recorded in the browser
  must read the same in `optcg collection show` and `optcg storage show`.

**Done when:**
- [ ] you open a browser and see your own 172 copies, with pictures
- [x] every place and card count on screen matches the CLI (parity test green)
- [ ] you record the art of a real alt art in the browser, and the CLI shows it

---

## M2 — Locations

Answers "where is this card", which is a core goal (D-019) and the thing a deck
list can't tell you.

| Route | Calls |
|---|---|
| `GET /api/locations` | `storage.list_locations` |
| `POST /api/locations` | `storage.add_location` |
| `GET /api/locations/{name}` | `storage.contents` |
| `POST /api/copies/move` | `collection.move` |

UI: a locations page, plus move controls on the card detail from M1. Recording
the art moved to M1 (D-044); here it also goes on the container view, since
it's the click-the-card-where-it-sits flow (D-030).

A move that exceeds the source is an **error** (D-030 superseded D-019's warn).
Surface the message — it names what the container actually holds — and keep the
UI state unchanged, because the server wrote nothing.

**Done when:** you can look up a card and see which binder it's in.

---

## M3 — Decks & completion

| Route | Calls |
|---|---|
| `GET /api/decks` | `decks.list_decks` |
| `GET /api/decks/{id}` | `decks.show_deck` → owned vs missing |
| `POST /api/decks/import` | `decks.import_decklist` |
| `PATCH /api/decks/{id}` | `decks.set_physical` |

`import_decklist(conn, text, name, is_physical, on_conflict)` already takes raw
text, so the browser posts a textarea straight into it — no file upload, no temp
file.

**The name clash is a real UI flow, not an error.** `DeckNameExists` exists so
the caller can ask (D-017). The API should return it as a distinct, recognisable
response and the UI should offer *replace* or *import as a second deck* — the two
options D-017 refuses to choose between. Never auto-pick.

**Building a deck from a wishlist is a screen, not a command** (D-041). A
physical deck is exactly what's in it, so turning a wishlist physical means
*gathering its cards into it* - and choosing where each one comes from is what a
UI does well and a terminal does badly. Decided with the owner, for when this is
built:

- **Pick the source per card.** Taken automatically when a card is in exactly
  one storage place; asked when it's in several.
- **Taking from another physical deck always asks** - it shrinks that deck.
- **Short of cards: refuse by default**, listing what's missing, so the wishlist
  keeps showing what to buy. A `--partial`-style choice builds with what you
  have; the missing cards leave the new deck's list.

Until then the CLI refuses `set-physical` on a wishlist with cards listed, and
`deck new` + `deck add` build a deck by hand.

**Done when:** you paste a Limitless list into the browser and see what you're
missing.

---

## M4 — Fast loose-card entry

What makes the remaining thousands of loose cards actually enterable. A terminal
never will.

| Route | Calls |
|---|---|
| `PATCH /api/collection/{card_image_id}` | `collection.add` / `set_quantity` |

UI: search-as-you-type over card names, `+1` / `-1` straight from the grid, no
page transition and no save button.

Remember collection is **printing-level** (D-003) — the picker has to let you say
`OP05-097_p1` rather than `OP05-097`, or you'll credit the wrong art every time
and M0's correction problem becomes permanent.

**Done when:** entering 50 loose cards takes under five minutes.

---

## Phase 2 complete when

- [ ] The real collection is in the database
- [ ] Cards are browsable in a browser, with images
- [ ] A card's storage location is visible from its detail view
- [ ] A decklist can be pasted in and shows owned vs missing
- [ ] Loose cards can be added without touching a terminal
- [ ] The CLI still works — it is kept, not replaced (D-024)
- [ ] `README.md` "where the project is" reflects all of it

## Explicitly not in Phase 2

Cross-deck views, what-can-I-build, cannibalization (availability form 3),
drag-and-drop deck building, local image cache. These are Phase 3+ and should be
chosen from experience with an app in daily use — which is the whole reason for
the re-cut (D-024).
