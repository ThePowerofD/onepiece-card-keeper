# Decisions

Why things are the way they are.

**This file is append-only.** Never edit an entry — if you change your mind,
add a new one and mark the old one `Superseded by D-0NN`. That's what stops
this file from ever going stale: entries are historical facts, not claims about
the present.

Each entry answers the one question code can't: *why*. The code already shows
*what*.

---

## Index

| # | Decision | Date | Status |
|---|---|---|---|
| [D-001](#d-001--sqlite--python--tauri) | SQLite + Python + Tauri | 2026-05-25 | Accepted |
| [D-002](#d-002--no-don-cards-in-v1) | No DON!! cards in v1 | 2026-05-25 | Accepted |
| [D-003](#d-003--collection-is-printing-level-decks-are-gameplay-level) | Collection printing-level, decks gameplay-level | 2026-05-25 | Accepted |
| [D-004](#d-004--dont-enforce-the-4-copy-rule) | Don't enforce the 4-copy rule | 2026-05-25 | Accepted |
| [D-005](#d-005--keep-zero-quantity-collection-rows) | Keep zero-quantity collection rows | 2026-05-25 | Accepted |
| [D-006](#d-006--limitless-format-is-the-deck-input-format) | Limitless is the deck input format | 2026-05-25 | Accepted |
| [D-007](#d-007--english-only-card-data) | English-only card data | 2026-05-25 | Accepted |
| [D-008](#d-008--card_types-is-a-junction-table-with-no-fk-to-cards) | `card_types` junction table, no FK | 2026-05-25 | Accepted |
| [D-009](#d-009--colors-are-strings-not-a-normalized-table) | Colors as strings | 2026-05-25 | Accepted |
| [D-010](#d-010--keywords-are-substring-matches) | Keywords as substring matches | 2026-05-25 | Accepted |
| [D-011](#d-011--counter-is-null-for-leaders-events-and-stages) | Counter NULL for non-Characters | 2026-05-25 | Accepted |
| [D-012](#d-012--leader_id-is-plain-text-with-no-foreign-key) | `leader_id` plain TEXT, no FK | 2026-05-25 | Accepted |
| [D-013](#d-013--availability-is-computed-never-stored) | Availability computed, not stored | 2026-05-25 | Accepted |
| [D-014](#d-014--is_physical-separates-sleeved-decks-from-wishlists) | `is_physical` separates sleeved from wishlist | 2026-05-25 | Accepted |
| [D-015](#d-015--input-order-is-decks-first-then-loose-cards) | Input order: decks first | 2026-05-25 | Accepted |
| [D-016](#d-016--three-api-endpoints-there-is-no-apiallcards) | Three API endpoints | 2026-05-26 | Accepted |
| [D-017](#d-017--bad-api-rows-are-logged-never-raised) | Bad rows logged, never raised | 2026-05-26 | Accepted |
| [D-018](#d-018--repair-shifted-api-fields-rather-than-skip-the-rows) | Repair shifted API fields | 2026-08-11 | Accepted |
| [D-019](#d-019--normalize-printing-keys-and-gameplay-identities) | Normalize printing keys | 2026-08-11 | Accepted |
| [D-020](#d-020--navy-admiral-is-two-types-not-one) | "Navy Admiral" is two types | 2026-08-11 | Accepted |
| [D-021](#d-021--deck-import-credits-the-base-printing) | Import credits base printing | 2026-08-11 | Accepted |
| [D-022](#d-022--re-importing-a-deck-name-asks-first) | Re-import asks first | 2026-08-11 | Accepted |
| [D-023](#d-023--add-foil_quantity-even-though-printings-mostly-encode-foil) | Add `foil_quantity` anyway | 2026-08-11 | Accepted |
| [D-024](#d-024--storage-locations-move-into-phase-1) | Storage locations in Phase 1 | 2026-08-11 | Supersedes deferral |
| [D-025](#d-025--documentation-lives-in-notes-as-three-files) | Docs structure: `notes/`, 3 files | 2026-08-11 | **Superseded by D-026** |
| [D-026](#d-026--six-files-one-job-each) | Six files, one job each | 2026-08-11 | Accepted |

---

## D-001 — SQLite + Python + Tauri

**2026-05-25 · Accepted**

**Context.** Personal offline desktop app, one user, needs to be easy to back up
and share between machines.

**Decision.** SQLite for storage, Python for the Phase 0–2 CLI, Tauri (over
Electron) for the eventual desktop shell.

**Consequences.** Single-file database that's trivial to back up and inspect in
DB Browser. Tauri binaries are ~10 MB versus Electron's 100+. Costs a
Python↔Rust boundary to design at Phase 3.

---

## D-002 — No DON!! cards in v1

**2026-05-25 · Accepted**

**Context.** OptcgAPI exposes `/api/allDonCards/`. DON cards are a fixed
resource deck, not something you collect or vary.

**Decision.** Never call that endpoint. DON is out of scope entirely, and the
deck parser ignores `DON!!:` lines.

> "we can skip it for the moment as this will strictly go as collection not part of decks."

**Consequences.** Simpler sync and simpler decks. If DON ever needs tracking
it's an additive change, not a rework.

---

## D-003 — Collection is printing-level, decks are gameplay-level

**2026-05-25 · Accepted**

**Context.** One card exists as many printings (`OP05-097`, `_p1`, `_p2`) with
different values. But a Limitless decklist never says which printing is sleeved
— in the game they're the same card.

**Decision.** `collection` keys on `card_image_id` (the printing).
`deck_cards` keys on `base_card_id` (the gameplay identity).

> "I am willing to let go the tracking of alt arts in decks if it will simplify things ahead."

**Consequences.** Much simpler schema, matches the import format, trivial
legality checks. The cost: you can't know *which* art is sleeved in a given
deck. This is the single most load-bearing decision in the project — most of
the awkward parts elsewhere trace back to it. See D-021.

---

## D-004 — Don't enforce the 4-copy rule

**2026-05-25 · Accepted**

**Context.** OPTCG normally limits a deck to 4 copies of a card, but the game
itself breaks this for specific cards.

**Decision.** Warn, never block. The warning is suppressible via `app_settings`.

> "sometimes broken by the rules itself, special cases for some cards."

**Consequences.** No legality guarantee from the database, but no false
rejections either.

---

## D-005 — Keep zero-quantity collection rows

**2026-05-25 · Accepted**

**Context.** What happens when you sell or trade away your last copy?

**Decision.** Keep the row with `quantity = 0`. Never delete collection rows.

**Consequences.** Preserves history and any notes attached to the row, and makes
re-adding trivial. Costs a slowly growing table — irrelevant at this scale.

---

## D-006 — Limitless format is the deck input format

**2026-05-25 · Accepted**

**Context.** Decks need to get into the app somehow, and typing 50 cards by hand
is not viable across 5000+ physical cards.

**Decision.** Parse the Limitless TCG export format as the primary input. Match
cards by id, warn on unmatched, never block the import.

**Consequences.** Bulk entry becomes practical. Ties the parser to one external
format's conventions.

---

## D-007 — English-only card data

**2026-05-25 · Accepted**

**Context.** OptcgAPI is English-focused; JP-exclusive cards would need another
source.

**Decision.** English only. JP exclusives are out of scope.

**Consequences.** One data source, simpler sync. JP cards can't be tracked.

---

## D-008 — `card_types` is a junction table with no FK to `cards`

**2026-05-25 · Accepted**

**Context.** Sub-types ("Straw Hat Crew", "Navy") need to be filterable. Storing
them as a string forces `LIKE '%Navy%'`, which false-matches "Neo Navy".

**Decision.** A `card_types` junction table keyed on `base_card_id`, with no
foreign key to `cards` (the target isn't unique — see D-012). Integrity is
enforced in the sync script instead.

**Consequences.** Clean indexed filtering with no false matches, and types are
stored once per gameplay card rather than once per printing. Costs DB-level
integrity and a second query to render a card. This reversed earlier advice to
use a plain string column.

---

## D-009 — Colors are strings, not a normalized table

**2026-05-25 · Accepted**

**Context.** Cards can be multi-colored ("Blue/Red").

**Decision.** Store as a normalized string and query with `LIKE`.

**Consequences.** Simple, and safe *specifically because* no OPTCG color name is
a substring of another. Note this is the opposite call from D-008 — the
difference is exactly that substring property.

---

## D-010 — Keywords are substring matches

**2026-05-25 · Accepted**

**Context.** `[Blocker]`, `[Rush]`, `[Trigger]` etc. appear in effect text.
Properly parsing OPTCG effect text is a large job.

**Decision.** Boolean columns set by matching the bracketed keyword in the text.

**Consequences.** No parser needed. False positives are possible on cards that
merely mention a keyword (e.g. "your opponent's [Blocker]").

---

## D-011 — Counter is NULL for Leaders, Events and Stages

**2026-05-25 · Accepted**

**Context.** Only Characters have counter values. But "a Character with counter
0" and "a card type that can't have a counter" are genuinely different facts.

**Decision.** Store the raw integer for Characters; force NULL for
Leader/Event/Stage.

**Consequences.** The distinction survives in the data, and future counter
values (+500, +3000) need no schema change.

---

## D-012 — `leader_id` is plain TEXT with no foreign key

**2026-05-25 · Accepted**

**Context.** `decks.leader_id` should reference a card. But SQLite requires a
UNIQUE or PK column as an FK target, and `base_card_id` is deliberately
non-unique — every alt art shares it (D-003).

**Decision.** Plain TEXT column, no FK.

**Consequences.** No DB-level guarantee the leader exists. This is a direct,
unavoidable consequence of D-003, not an oversight.

---

## D-013 — Availability is computed, never stored

**2026-05-25 · Accepted**

**Context.** "How many of this card are free to use?" depends on what's sleeved
into physical decks, which changes constantly.

**Decision.** Never store it. Compute:
`available = quantity − SUM(deck_cards.quantity WHERE decks.is_physical)`,
exposed through an `available_cards` view.

**Consequences.** Can never go stale or need reconciling. Costs a join on every
read — trivial at this scale.

---

## D-014 — `is_physical` separates sleeved decks from wishlists

**2026-05-25 · Accepted**

**Context.** Two different needs: recording decks you've actually built, and
planning decks you *want* to build from cards you may not own.

**Decision.** One `decks` table with an `is_physical` flag. TRUE locks its cards
out of availability; FALSE locks nothing and reports what's missing.

**Consequences.** Both needs served by one table, one import path, one set of
commands. No separate wishlist concept to maintain.

---

## D-015 — Input order is decks first, then loose cards

**2026-05-25 · Accepted**

**Context.** With 5000+ physical cards, how you *start* entering data determines
whether the project ever gets off the ground.

**Decision.** `deck import` credits the collection *and* creates the deck in one
transaction, so importing your existing sleeved decks bootstraps the collection.
Loose cards are entered afterwards.

> "the way it's easier for me inputting info at first is by decks and then loose cards/stored cards."

**Consequences.** The first hour of use produces real data instead of tedium.
Drives the design of D-021 and D-022.

---

## D-016 — Three API endpoints; there is no `/api/allCards/`

**2026-05-26 · Accepted**

**Context.** The original design assumed a single `/api/allCards/` endpoint.
Live testing showed it doesn't exist.

**Decision.** Fetch from three: `/api/allSetCards/`, `/api/allSTCards/`,
`/api/promos/filtered/?rarity=PR`. Deduplicate by `card_image_id`.

**Consequences.** 4459 raw rows collapse to 4338 unique printings. The same
printing legitimately appears in more than one endpoint. `DESIGN.md` §9's
original field-name assumptions were also wrong — the API uses `card_name`,
`card_type`, `card_cost`, `card_set_id`.

---

## D-017 — Bad API rows are logged, never raised

**2026-05-26 · Accepted**

**Context.** Third-party data will always contain surprises, and a sync that
dies on row 3000 of 4459 is useless.

**Decision.** Rows missing a `card_image_id` go to `skipped_cards_log`;
unparseable sub-types go to `unknown_type_log`. The sync never raises on bad
data and always prints a summary.

**Consequences.** Sync always completes. Data problems become a review queue
rather than an outage — which is exactly how D-018 and D-020 were found.

---

## D-018 — Repair shifted API fields rather than skip the rows

**2026-08-11 · Accepted**

**Context.** Three cards had fields shifted out of position upstream:

```
EB03-050 Conis     card_power="Sky Island"       sub_types="1000"
EB03-009 Makino    card_power="Windmill Village" sub_types="2000"
OP08-001 Chopper   card_power="4"                sub_types="5000"
```

Conis and Makino had no power and no gameplay types at all in the database.

**Decision.** Added `repair_field_shift()` (sanitize.py Rule 9). It keys on the
invariant that **a real sub-type is never a bare number**, then either swaps the
two fields (when power holds text, both values are recoverable) or takes the
power and drops the lost sub-types (when the row shifted further).

**Consequences.** The three cards are correct, and the sync summary now prints a
`Field-shift repairs` count so new upstream breakage surfaces immediately
instead of silently. Healthy rows never take the repair path, so good data can't
be corrupted by it. Alternative considered: skipping the rows — rejected because
it loses real cards.

---

## D-019 — Normalize printing keys and gameplay identities

**2026-08-11 · Accepted**

**Context.** Eight promo cards had a `base_card_id` that still carried the
printing suffix (`P-029_r1` instead of `P-029`), splitting one gameplay identity
into two. `P-029` has 7 printings; six shared an identity and one didn't. Also
found: one id carrying a file extension (`EB02-052_p2.jpg`) and two using a
hyphen before the variant (`OP09-078-r1`).

**Decision.** Two new pure rules — `normalize_card_image_id()` (strips file
extensions, folds `-variant` to `_variant`) and `strip_printing_suffix()`
(derives `base_card_id`). `card_set_id` is still preferred as the source since
it correctly maps `EB03_OP09-034_p1` → `OP09-034`, but it's now suffix-stripped.

**Consequences.** Unique gameplay cards went 2656 → 2648. Without this, a deck
listing `P-029` would have resolved against only 6 of the 7 printings you own —
telling you that you don't own a card sitting in your binder. Caught before any
collection data existed; after Phase 1 it would have been a migration.

---

## D-020 — "Navy Admiral" is two types, not one

**2026-08-11 · Accepted**

**Context.** OP16 introduced a `sub_types` value of `"Navy Admiral"` on 10
printings. The vocabulary already contained compound types (`Former Navy`,
`Happo Navy`, `Neo Navy`) *and* plain `Navy`, so the string alone was ambiguous.
"Admiral" never appears alone or with any other type in the data.

**Decision.** Two separate types. `"Admiral"` was added to `SEED_TYPES`, so
those cards carry both `Navy` and `Admiral`.

**Consequences.** Effects targeting Navy and effects targeting Admiral both
match. `unknown_type_log` is empty again. If a similar compound trait line shows
up in a future set, follow this precedent.

---

## D-021 — Deck import credits the base printing

**2026-08-11 · Accepted**

**Context.** Direct consequence of D-003. A decklist says `4 Monkey D. Luffy
OP05-097` but the collection needs a specific `card_image_id`. Something has to
choose.

**Decision.** Credit the variant-free base printing automatically. Where no
variant-free printing exists (8 promo cards), fall back to the lowest-sorting
one. Correct it afterwards with `collection set` if you actually sleeved an alt
art.

**Consequences.** A 50-card deck imports with zero prompts, which is what makes
D-015 viable at all. The cost is that alt-art owners get wrong printings on
import and must fix them. Alternative considered: prompting per card — rejected
as unusable across 5000+ cards.

---

## D-022 — Re-importing a deck name asks first

**2026-08-11 · Accepted**

**Context.** Because import credits the collection (D-015), importing the same
decklist twice would silently double your card counts. But sometimes you
genuinely *have* built a second physical copy of a deck.

**Decision.** When the deck name already exists, stop and ask: replace, or
create a second deck. Never guess.

**Consequences.** One prompt on re-import, in exchange for never silently
corrupting collection counts. Alternatives considered: always replace (wrong for
genuine duplicate decks) and always create new (easy to double-count by
accident).

---

## D-023 — Add `foil_quantity` even though printings mostly encode foil

**2026-08-11 · Accepted**

**Context.** Recorded early as a wanted column ("easier removing it than adding
it later") but never actually built. Analysis of the synced data then showed
foil treatments usually get their *own* printing id — `OP01-006_p3` is
"Otama (Jolly Roger Foil)" — so the column would largely duplicate what
`card_image_id` already captures. Only 170 of 4338 printings say "Foil" outright.
Recommendation at the time was to drop it.

**Decision.** Add it anyway, defaulting to 0 throughout v1.

**Consequences.** Costs nothing now because `collection` is empty; adding it
after 5000+ cards are entered would need a migration. It's a reserve for the
case this data can't express: the *same* printing existing as both foil and
non-foil. See `notes/foil_and_printing_variants.md`.

---

## D-024 — Storage locations move into Phase 1

**2026-08-11 · Supersedes the Phase 6+ deferral**

**Context.** Originally deferred to Phase 6+ as "would need a new
`storage_locations` table". But a core goal is knowing what you own *and where
it is*, and deck membership only answers that for sleeved cards — not for the
majority sitting in binders and boxes.

**Decision.** Build it in Phase 1: `storage_locations` plus a
`collection_placements` junction, so one card's copies can be split across
several places.

**Consequences.** `collection.quantity` stays the single source of truth for how
many you own; placements only describe where the loose ones are, and
`placed > loose` warns rather than blocks. Placement is optional bookkeeping —
never a precondition for recording that you own something. Adds scope to Phase 1.

---

## D-025 — Documentation lives in `notes/` as three files

**2026-08-11 · Superseded by [D-026](#d-026--six-files-one-job-each) the same day**

**Context.** The project already had 1359 lines of documentation, and the
largest file (`project_recap.md`, 376 lines) had drifted badly — it claimed
`foil_quantity` existed when it didn't, claimed an `available_cards` view
existed when it didn't, and still listed a finished task as "NEXT". It failed
because it was orientation, reference, and decision log in one file, so no part
could be maintained on its own schedule.

**Decision.** Split by *rate of change*, not topic:

| File | Changes |
|---|---|
| `notes/START_HERE.md` | Every session. Kept to one page on purpose |
| `notes/DECISIONS.md` | Append-only, never edited |
| `notes/learning/` | Write once |
| `DESIGN.md` | When the system changes |
| `PHASE_N.md` | While working that phase |

A single `DECISIONS.md` rather than the conventional folder-of-ADRs, because
that convention exists for team PR review — for one person reading cold, one
file you can read top to bottom is better. `project_recap.md` is archived rather
than deleted, since it holds the only record of the original reasoning.

**Consequences.** Documentation must change in the same commit as the behavior
it describes. That single practice, not the structure, is what prevents drift.

---

## D-026 — Six files, one job each

**2026-08-11 · Accepted · Supersedes [D-025](#d-025--documentation-lives-in-notes-as-three-files)**

**Context.** D-025 set up `notes/` with `START_HERE.md`, `DECISIONS.md` and a
`learning/` subfolder, and planned to archive `project_recap.md` in place. Within
the same session that proved to still carry redundancy: `START_HERE.md` and
`README.md` were two competing front doors, `learning/` held one file, and a
376-line archived recap plus a 201-line finished `PHASE_0.md` were dead weight
whose content had already been mined into `DECISIONS.md` and `DESIGN.md`.

**Decision.** Six files, each justified by differing from every other on at least
one of: lifecycle, mutability, access pattern, audience.

| File | Job | Unique property |
|---|---|---|
| `README.md` | Front door: what, current state, how to run | Read linearly, once |
| `DESIGN.md` | What **exists** — schema, rules, API | Jumped into for a fact |
| `PHASE_1.md` | What **doesn't exist yet** | Has a scheduled death |
| `CLAUDE.md` | Agent operating instructions | Auto-loaded; must stay short |
| `notes/DECISIONS.md` | Why | Append-only, never rewritten |
| `notes/foil_and_printing_variants.md` | Card-data domain knowledge | Weakest case — foldable into DESIGN |

`START_HERE.md` merged into `README.md` (one front door). `learning/` flattened.
`project_recap.md` and `PHASE_0.md` deleted rather than archived — their content
lives in `DECISIONS.md` and `DESIGN.md §10`, and git preserves the originals.

**Consequences.** 9 files → 6; no fact documented twice. The load-bearing rule is
that **built and planned must never share a file** — mixing them is what made the
recap untrustworthy. Honest caveats: docs (1463 lines) still exceed code (1349),
and the foil notes file is separate largely because it was asked for that way.
`CLAUDE.md` was rewritten here to stop duplicating `DESIGN.md`.
