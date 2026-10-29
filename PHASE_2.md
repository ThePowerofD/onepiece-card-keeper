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
| M0 Real data in — no code | |
| M1 API layer + card browser | |
| M2 Locations | Backend **done** — `kind` column, D-025. UI pending |
| M3 Decks & completion | |
| M4 Fast loose-card entry | |

**The starting position:** 4,338 cards synced, 246 tests passing, and zero rows
in `collection`, `decks` and `storage_locations`. The backend is built and has
never met a real card.

---

## M0 — Real data in

**No code.** This is the milestone, not a prerequisite to it.

```bash
optcg deck import <file.txt> --name "<deck>" --physical
optcg storage add "Binder A" --type binder --notes "red decks"
```

Import every deck that's currently sleeved. Each one credits the collection as a
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
sleeved deck appears in `deck list`.

---

## M1 — API layer + card browser

The milestone where it stops being a script.

### a. Logic-layer gaps

`collection.list_cards()` needs three things before it can back a grid:

- **`image_url` in the SELECT** — it's on every `cards` row and currently unread.
- **`q` — name search.** `LIKE %q%` on `c.name`.
- **`offset`, and a total count.** It takes `limit` but has no pagination, and
  4,712 printings will not arrive in one response.

Keep it returning plain rows; the HTTP layer does the JSON shaping.

### b. `optcg/web/api.py`

FastAPI. Imports the existing modules and calls them — **no SQL in this file.**
One dependency yields a connection from `db_setup.connect()`.

| Route | Calls |
|---|---|
| `GET /api/cards` | `collection.list_cards` — `set`, `color`, `category`, `owned_only`, `q`, `limit`, `offset` |
| `GET /api/cards/{base_card_id}` | `collection.show` → `CardStatus` |
| `GET /api/sets` | distinct `set_code`, for the filter dropdown |

`CardStatus` and `PrintingStatus` are `NamedTuple`s, so `._asdict()` gets most of
the way to a response body.

**Error mapping:** `CollectionError` / `DeckError` carry messages already fit to
print — map them to `400` with the message intact, and let the UI show it. Don't
invent a second error vocabulary.

### c. `ui/` — Vite + React

Card grid with images, filter bar (set / color / owned-only / search), card
detail showing every printing with owned counts.

Images hotlink `image_url` directly. The local cache stays Phase 5 — an `<img>`
tag is enough to make the app feel like a card app, which was the point of
pulling images forward.

**Done when:** you open a browser and see your own collection, with pictures.

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
| `POST /api/copies/set-art` | `collection.set_art` |

UI: a locations page, plus move and set-art controls on the card detail from M1.
Setting the art is the click-the-card-where-it-sits flow (D-030), so it belongs
on the container view as much as on the card view.

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
