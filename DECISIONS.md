# Decisions

Why things are the way they are. The code shows *what* — this is the part it can't tell you.

**Append-only.** Never edit an entry; if you change your mind, add a new one and
mark the old one superseded. Entries are historical facts, not claims about now.

> Curated once on 2026-08-11: entries that a successor could derive from the code
> or the docs were removed, and three overlapping schema entries were merged.
> Append-only applies from here.

**The test for adding one:** would someone reasonably "fix" this thing without
knowing why it's that way? If not, it doesn't need an entry.

| # | Decision | Date |
|---|---|---|
| [D-001](#d-001--sqlite--python--tauri) | SQLite + Python + Tauri | 2026-05-25 |
| [D-002](#d-002--no-don-cards) | No DON!! cards | 2026-05-25 |
| [D-003](#d-003--collection-is-printing-level-decks-are-gameplay-level) | **Collection printing-level, decks gameplay-level** | 2026-05-25 |
| [D-004](#d-004--dont-enforce-the-4-copy-rule) | Don't enforce the 4-copy rule | 2026-05-25 |
| [D-005](#d-005--keep-zero-quantity-collection-rows) | Keep zero-quantity collection rows | 2026-05-25 |
| [D-006](#d-006--types-are-normalized-colors-arent-and-base_card_id-carries-no-foreign-keys) | Types normalized, colors not, no FKs on `base_card_id` | 2026-05-25 |
| [D-007](#d-007--keywords-are-substring-matches) | Keywords are substring matches | 2026-05-25 |
| [D-008](#d-008--counter-is-null-for-leaders-events-and-stages) | Counter NULL for non-Characters | 2026-05-25 |
| [D-009](#d-009--availability-is-computed-never-stored) | Availability computed, never stored | 2026-05-25 |
| [D-010](#d-010--is_physical-separates-sleeved-decks-from-wishlists) | `is_physical` separates sleeved from wishlist | 2026-05-25 |
| [D-011](#d-011--input-order-is-decks-first-then-loose-cards) | Input order: decks first | 2026-05-25 |
| [D-012](#d-012--three-api-endpoints-there-is-no-apiallcards) | Three API endpoints | 2026-05-26 |
| [D-013](#d-013--repair-shifted-api-fields-rather-than-skipping-the-rows) | Repair shifted API fields | 2026-08-11 |
| [D-014](#d-014--normalize-printing-keys-and-gameplay-identities) | Normalize printing keys | 2026-08-11 |
| [D-015](#d-015--navy-admiral-is-two-types-not-one) | "Navy Admiral" is two types | 2026-08-11 |
| [D-016](#d-016--deck-import-credits-the-base-printing) | Import credits the base printing | 2026-08-11 |
| [D-017](#d-017--re-importing-a-deck-name-asks-first) | Re-import asks first | 2026-08-11 |
| [D-018](#d-018--add-foil_quantity-anyway) | Add `foil_quantity` anyway | 2026-08-11 |
| [D-019](#d-019--storage-locations-move-into-phase-1) | Storage locations in Phase 1 | 2026-08-11 |
| [D-020](#d-020--six-doc-files-one-job-each) | Six doc files, one job each | 2026-08-11 |
| [D-021](#d-021--the-leader-is-stored-twice-on-purpose) | The leader is stored twice, on purpose | 2026-08-11 |
| [D-022](#d-022--replacing-a-deck-credits-only-increases) | Replacing a deck credits only increases | 2026-08-11 |
| [D-023](#d-023--drop-tauri-the-app-is-a-local-web-app) | **Drop Tauri — the app is a local web app** | 2026-09-15 |
| [D-024](#d-024--ui-before-cli-insights-and-the-cli-stays) | UI before CLI insights; the CLI stays | 2026-09-15 |
| [D-025](#d-025--locations-have-a-kind-decks-are-the-fifth-category) | **Locations have a `kind`; decks are the fifth category** | 2026-09-15 |
| [D-026](#d-026--delete-the-available_cards-view) | Delete the `available_cards` view | 2026-09-15 |
| [D-027](#d-027--attribute-and-life-are-synced-rotation_block-is-not) | Attribute and life are synced; `rotation_block` is not | 2026-09-15 |
| [D-028](#d-028--set-and-product-are-different-fields) | **Set and product are different fields** | 2026-09-15 |
| [D-029](#d-029--the-api-reuses-printing-ids-sync-resolves-them) | **The API reuses printing ids; sync resolves them** | 2026-09-15 |
| [D-030](#d-030--every-copy-is-somewhere-art-is-recorded-on-the-copy) | **Every copy is somewhere; art is recorded on the copy** | 2026-09-15 |
| [D-031](#d-031--every-printing-of-a-card-agrees-on-its-gameplay-fields) | **Every printing of a card agrees on its gameplay fields** | 2026-09-15 |

---

## D-001 — SQLite + Python + Tauri

**2026-05-25**

Offline single-user desktop app that needs to be easy to back up and share.
SQLite gives a single file you can copy and open in DB Browser. Tauri over
Electron for ~10 MB binaries instead of 100+. Python for the sync and CLI
because it's better for data work.

Cost: a Python↔Rust boundary to design at Phase 3.

---

## D-002 — No DON!! cards

**2026-05-25**

OptcgAPI exposes `/api/allDonCards/`. DON is a fixed resource deck — not
something you collect or vary — so it's excluded entirely. The endpoint is never
called and the deck parser ignores `DON!!:` lines.

> "we can skip it for the moment as this will strictly go as collection not part of decks."

**Don't "fix" the missing endpoint.** Adding DON later is additive, not a rework.

---

## D-003 — Collection is printing-level, decks are gameplay-level

**2026-05-25** · *The load-bearing decision. Most awkwardness elsewhere traces here.*

One card exists as many printings (`OP05-097`, `_p1`, `_p2`) with very different
values. But a Limitless decklist never says which printing is sleeved, because in
the game they're identical.

So: `collection` keys on `card_image_id` (the physical card).
`deck_cards` keys on `base_card_id` (the gameplay identity).

> "I am willing to let go the tracking of alt arts in decks if it will simplify things ahead."

Buys a much simpler schema, a direct match to the import format, and trivial
legality checks. Costs the ability to know *which* art is sleeved in a given deck
— and forces D-016 and the over-subtraction caveat on the `available_cards` view.

---

## D-004 — Don't enforce the 4-copy rule

**2026-05-25**

OPTCG limits decks to 4 copies of a card, but the game itself breaks this for
specific cards. So the app warns and never blocks; the warning is suppressible
via `app_settings`.

> "sometimes broken by the rules itself, special cases for some cards."

**This is not missing validation.** Don't add a hard check.

---

## D-005 — Keep zero-quantity collection rows

**2026-05-25**

When you trade away your last copy, the row stays at `quantity = 0` rather than
being deleted. Preserves history and any notes on the row, and makes re-adding
trivial.

**Don't "clean up" zero rows.**

---

## D-006 — Types are normalized, colors aren't, and `base_card_id` carries no foreign keys

**2026-05-25**

Three related schema calls that look inconsistent until you see why.

**Sub-types are normalized** into a `card_types` junction table, because storing
them as a string forces `LIKE '%Navy%'`, which false-matches `"Neo Navy"`.

**Colors are not normalized** — they stay a string queried with `LIKE`. Safe
here *specifically because* no OPTCG color name is a substring of another. That
substring property is the whole difference between these two calls.

**Neither `card_types.base_card_id` nor `decks.leader_id` has a foreign key.**
SQLite requires a UNIQUE or PK target, and `base_card_id` is deliberately
non-unique — every alt art shares it (D-003). Integrity is enforced in the sync
script instead.

**The missing FKs are not oversights.** They're unavoidable given D-003.

---

## D-007 — Keywords are substring matches

**2026-05-25**

`has_blocker`, `has_rush` etc. are set by matching the bracketed keyword in the
effect text. Properly parsing OPTCG effect text is a large project and wasn't
worth it for v1.

Known limitation: false positives on cards that merely mention a keyword
("your opponent's [Blocker]"). Accepted, not a bug.

---

## D-008 — Counter is NULL for Leaders, Events and Stages

**2026-05-25**

Only Characters have counter values. Forcing NULL for the others preserves a real
distinction: "a Character with counter 0" and "a card type that can't have a
counter" are different facts.

**Don't collapse this to 0.** Future counter values (+500, +3000) then need no
schema change.

---

## D-009 — Availability is computed, never stored

**2026-05-25**

`available = quantity − SUM(deck_cards.quantity WHERE decks.is_physical)`,
exposed through a view. It changes every time a deck changes, so a stored column
would need constant reconciling and would eventually be wrong.

Costs a join per read — irrelevant at this scale.

---

## D-010 — `is_physical` separates sleeved decks from wishlists

**2026-05-25**

Two needs — recording decks you've actually built, and planning decks you want to
build from cards you may not own — are served by one table with a flag. TRUE
locks its cards out of availability; FALSE locks nothing and reports what's
missing.

No separate wishlist concept to build or maintain.

---

## D-011 — Input order is decks first, then loose cards

**2026-05-25**

With 5000+ physical cards, how you *start* entering data decides whether the
project ever gets off the ground. So `deck import` credits the collection *and*
creates the deck in one transaction — importing your existing sleeved decks
bootstraps the collection for free.

> "the way it's easier for me inputting info at first is by decks and then loose cards/stored cards."

Drives D-016 and D-017.

---

## D-012 — Three API endpoints; there is no `/api/allCards/`

**2026-05-26**

The original design assumed a single endpoint. Live testing showed it 404s, as
does `/api/allPromoCards/`. Cards come from `/api/allSetCards/`,
`/api/allSTCards/` and `/api/promos/filtered/?rarity=PR`, deduplicated by
`card_image_id` — 4459 raw rows become 4338 unique printings, because the same
printing legitimately appears in more than one endpoint.

Field names also differ from the original assumptions (`card_name`, `card_type`,
`card_cost`, `card_set_id`). See `FIELD_ALIASES` in `sync.py`.

**Don't go looking for a single all-cards endpoint.**

---

## D-013 — Repair shifted API fields rather than skipping the rows

**2026-08-11**

Three cards had fields shifted out of position upstream:

```
EB03-050 Conis     card_power="Sky Island"        sub_types="1000"
EB03-009 Makino    card_power="Windmill Village"  sub_types="2000"
OP08-001 Chopper   card_power="4"                 sub_types="5000"
```

Conis and Makino had no power and no gameplay types at all in the database.

`repair_field_shift()` keys on the invariant that **a real sub-type is never a
bare number**, then either swaps the fields (power holds text → both values
recoverable) or takes the power and drops the unrecoverable sub-types.

Healthy rows never enter the repair path, so good data can't be corrupted by it.
The sync summary prints a `Field-shift repairs` count so new upstream breakage
surfaces immediately. Skipping the rows was rejected — it loses real cards.

---

## D-014 — Normalize printing keys and gameplay identities

**2026-08-11**

Eight promo cards had a `base_card_id` that still carried its printing suffix
(`P-029_r1` instead of `P-029`), splitting one gameplay identity in two. `P-029`
has 7 printings; six shared an identity and one didn't. Also found: an id
carrying a file extension (`EB02-052_p2.jpg`) and two using a hyphen before the
variant (`OP09-078-r1`).

`normalize_card_image_id()` and `strip_printing_suffix()` fix both. `card_set_id`
is still the preferred source — it correctly maps `EB03_OP09-034_p1` →
`OP09-034` — but it's now suffix-stripped.

Unique gameplay cards went 2656 → 2648. **Without this, a deck listing `P-029`
would resolve against only 6 of the 7 printings you own** — telling you that you
don't own a card sitting in your binder. Caught before any collection data
existed; afterwards it would have been a migration.

---

## D-015 — "Navy Admiral" is two types, not one

**2026-08-11**

OP16 introduced `sub_types = "Navy Admiral"` on 10 printings. Ambiguous, because
the vocabulary already held compound types (`Former Navy`, `Neo Navy`) *and*
plain `Navy`, and "Admiral" never appears alone anywhere in the data.

Resolved as two types — `"Admiral"` was added to `SEED_TYPES`, so those cards
carry both `Navy` and `Admiral`, and effects targeting either will match.

**Precedent for future compound trait lines.**

---

## D-016 — Deck import credits the base printing

**2026-08-11**

Direct consequence of D-003. A decklist says `4 Monkey D. Luffy OP05-097`, but
the collection needs a specific `card_image_id`. Something has to choose.

Import credits the variant-free base printing automatically, falling back to the
lowest-sorting printing if a card ever lacks one. Correct it afterwards with
`collection set` if you actually sleeved an alt art.

*(Factual correction, 2026-08-11: this entry first said 8 promo cards lack a
variant-free printing. That was already false when written — D-014's
normalization had fixed all 8 earlier the same day. The fallback is defensive,
not a live code path. The decision itself is unchanged.)*

A 50-card deck imports with zero prompts, which is what makes D-011 viable at
all. Prompting per card was rejected as unusable across 5000+ cards.

---

## D-017 — Re-importing a deck name asks first

**2026-08-11**

Because import credits the collection (D-011), importing the same decklist twice
would silently double your card counts. But sometimes you genuinely *have* built
a second physical copy of a deck.

So a name clash stops and asks: replace, or create a second deck. Never guess.
Always-replace is wrong for genuine duplicates; always-create double-counts by
accident.

---

## D-018 — Add `foil_quantity` anyway

**2026-08-11**

Analysis showed foil treatments usually get their *own* printing id —
`OP01-006_p3` is "Otama (Jolly Roger Foil)" — so the column largely duplicates
what `card_image_id` already captures. Only 170 of 4338 printings say "Foil"
outright. The recommendation was to drop it.

Added anyway, defaulting to 0 through v1: it costs nothing while `collection` is
empty, and adding it after 5000+ cards are entered would need a migration. It's a
reserve for the one case this data can't express — the *same* printing existing
as both foil and non-foil.

See "Printing variants" in [DESIGN.md](DESIGN.md) §3.

---

## D-019 — Storage locations move into Phase 1

**2026-08-11** · *Reverses an earlier Phase 6+ deferral*

Knowing what you own *and where it is* is a core goal, and deck membership only
answers that for sleeved cards — not for the majority sitting in binders and
boxes.

`storage_locations` plus a `collection_placements` junction, so one card's copies
can be split across several places. `collection.quantity` remains the single
source of truth for how many you own; placements only describe where the loose
ones are, and `placed > loose` warns rather than blocks.

**Placement is optional bookkeeping — never a precondition for recording that you
own something.**

---

## D-020 — Six doc files, one job each

**2026-08-11**

The project had 1359 lines of documentation and the largest file
(`project_recap.md`) had drifted badly — it claimed a `foil_quantity` column and
an `available_cards` view existed when neither ever had. It failed because it was
orientation, reference and decision log in one file, so no part could be
maintained on its own schedule.

Now five files, flat at the repo root, each differing from every other on
lifecycle, mutability, access pattern or audience: `README` (front door),
`DESIGN` (what exists), `PHASE_N` (what doesn't yet), `CLAUDE.md` (agent
instructions), `DECISIONS.md` (why).

A sixth file of card-data domain notes was cut the same day: it was the one file
whose separation rested on preference rather than any of those four properties.
Its load-bearing content — that a printing suffix is an index, not a treatment —
folded into `DESIGN.md` §3 in a fifth of the space. The `notes/` folder went with
it once only one file remained inside.

The load-bearing rule: **built and planned must never share a file.** Once a
reader can't tell which parts are real, none of it is trustworthy.

`project_recap.md` and `PHASE_0.md` were deleted rather than archived — their
content lives here and in `DESIGN.md`, and git preserves the originals.

---

## D-021 — The leader is stored twice, on purpose

**2026-08-11**

`decks.leader_id` names the leader, and the leader also gets a row in
`deck_cards` with quantity 1. That looks like duplication and someone will want
to remove one of them.

It isn't. The two carry different meaning:

- `leader_id` answers *which card is this deck's leader* — needed to display the
  deck and, later, to filter legal cards by leader colour.
- The `deck_cards` row makes the leader **lockable**. `available_cards` computes
  commitment from `deck_cards` alone (D-009), so a leader outside that table
  would be sleeved in a physical deck yet still report as free to use — and
  importing a deck would never credit the leader to your collection, leaving you
  owning a card the app can't see.

Removing either one breaks something real.

---

## D-022 — Replacing a deck credits only increases

**2026-08-11**

Re-importing an edited decklist over an existing deck (D-017's "replace") has to
decide what happens to the collection.

It credits the **positive difference only**. If a card went from 3 copies to 4,
the collection gains 1. If it went from 4 to 2, or left the list entirely, the
collection is **not** reduced.

That's the physically true model: taking a card out of a deck doesn't mean you
sold it. You still own it — it just becomes loose, and availability rises on its
own because commitment dropped. Decrementing would silently destroy inventory
every time you tuned a list, and it would target the wrong printing anyway if
you'd reassigned one by hand (D-016).

Consequence: replacing a deck can never lose you cards, and re-importing an
unchanged list is a no-op on the collection.

---

## D-023 — Drop Tauri; the app is a local web app

**2026-09-15** · *Supersedes the Tauri half of [D-001](#d-001--sqlite--python--tauri)*

D-001 chose Tauri and left "a Python↔Rust boundary to design at Phase 3" as an
accepted cost. Reviewed before starting the UI, that boundary turned out to have
no cheap resolution:

- **PyInstaller sidecar** — keeps every line of Python, but produces a 40 MB+
  bundle and real Windows packaging pain. That erases the "10 MB instead of
  100 MB" argument that picked Tauri over Electron in the first place.
- **Rewrite the data layer in Rust** — discards ~2,000 lines of tested Python
  and adds a new language to a project that doesn't otherwise need one.

So: **FastAPI serving the existing Python modules, React + Vite in the browser.**
Run locally, opened in a browser tab.

What made this cheap to choose is that `src/` was already written as a library —
`collection.py`, `decks.py`, `storage.py` and `resolve.py` contain no `print()`
at all. Every function takes a connection and returns a dataclass, with `cli.py`
as the only presentation layer. A web layer calls the same functions the CLI
calls.

The cost is real and accepted: **no desktop icon and no distributable binary.**
DESIGN.md §1 listed "shareable as a downloadable app" as a goal; that goal is
dropped. This is a single-user personal tool, and a localhost tab is the form
that costs the least to reach and the least to maintain.

**Don't reintroduce Tauri to recover "shareable."** If distribution ever matters
again, the FastAPI layer is what a Tauri shell would wrap anyway — nothing built
under this decision is wasted.

---

## D-024 — UI before CLI insights; the CLI stays

**2026-09-15** · *Replaces the original Phase 2 scope*

Phase 1 finished with 246 passing tests and **zero rows** in `collection`,
`decks` and `storage_locations`. The app was complete by test coverage and
unused by its only user.

The original Phase 2 was "deck completion & insights (CLI)" — cross-deck views,
what-can-I-build, richer filtering — with Phase 3 then replacing the CLI with a
UI. That ordering builds presentation code twice: the *queries* survive into the
UI, the CLI tables don't. Phase 5's image cache was similarly misplaced, since
`image_url` is already on every row and an `<img>` tag is most of what a card
app needs to feel like one.

So the phases are re-cut around a single sentence:

> *I open the app, see my real cards with images, know where each one is, and can
> check a decklist against what I own.*

Anything not serving that moves after it. The old Phase 2 features return later
as UI features — chosen from experience with a tool in daily use, rather than
guessed at now.

**The CLI is not being replaced.** The earlier plan said Phase 3 would retire it.
It stays: it is how bulk deck import happens (D-011), it is the fastest path for
one-off corrections, and it costs nothing to keep now that both it and the web
layer are thin callers of the same modules.

**Real data comes first.** Loading the actual collection is the first milestone,
ahead of any new code, because every load-bearing decision — decks-first entry
(D-011), import credits the base printing (D-016), placements as optional
bookkeeping (D-019) — is still an untested guess until real cards go in, and
reversing one is free now and a migration later.

---

## D-025 — Locations have a `kind`; decks are the fifth category

**2026-09-15** · *Extends [D-019](#d-019--storage-locations-move-into-phase-1)*

Asked where his cards physically live, the owner described five classes with
mutable members inside each:

```
Storage  ->  OP13 Box, Box 1, Leather Box
Binder   ->  Mewtwo Binder, Black Binder, Red Binder
Loose    ->  Counter, Floor1
Loan     ->  Friend 1, Friend 2
Decks    ->  Shirahoshi Deck, Law Deck, Luffy ST Deck
```

That is exactly **two levels** — a fixed class, then instances. It is not a
general tree, so `storage_locations` gets a `kind` column and **no `parent_id`**.
The grouping the owner drew is produced by ordering on `kind`, not by recursion.
A tree would have cost a tree-picker in every UI surface, cycle protection on
reparent, and recursive containment queries, to express a shape one column
already expresses.

> `parent_id` stays deferrable on purpose. It is a nullable column with nothing
> to backfill, so unlike `kind` it is *not* cheaper today than later. If binders
> ever need named sections, add it then.

**`kind` is `storage | binder | loose | loan` — four, not five.** Decks are the
fifth category the UI shows, but they live in `decks`, because a deck has a
leader, a card list and a physical flag that no binder has. Mirroring decks into
`storage_locations` would give two tables the power to define a deck, and they
would drift. The seam is invisible in the UI: `collection.show()` already returns
deck membership and placements together, which is what "where is this card?"
actually needs.

**Names are unique per kind, not globally** — `UNIQUE(name, kind)`. A box and a
binder may both reasonably be called "Box 1".

The cost of that: a bare name can match more than one location. Rather than
guess, lookups raise `AmbiguousLocation` and the caller passes `--kind` — the
same refusal-to-guess principle as D-017.

```
$ place OP05-097 "Box 1" 2
error: 'Box 1' exists as binder and as storage - say which with --kind
```

Landed while all three tables were still empty, so it was a schema edit and a
`--reset`, never a migration.

---

## D-026 — Delete the `available_cards` view

**2026-09-15** · *Supersedes the view half of [D-009](#d-009--availability-is-computed-never-stored); the over-subtraction caveat in [D-016](#d-016--deck-import-credits-the-base-printing) no longer applies*

The view reported `owned`, `committed` and `available` per **printing**, but
commitment is **gameplay-level** (D-003): a decklist says `OP05-097` without
naming an art. So the same committed figure was joined onto every printing row.
Own 4 plain and 2 parallel with 3 sleeved, and it read `(4,3,1)` and `(2,3,-1)`
— subtracting one commitment twice. Its own docstring told callers to aggregate
per `base_card_id` to undo it.

It was never fixable in that shape. Rolling the view up to `base_card_id` would
have made it correct and *still* useless, because the question that matters when
looking at a deck is "available **to this deck**", and a deck does not compete
with itself (`decks.py:272`). SQL has no way to know which deck is asking.

Meanwhile the correct computation already existed and shipped:

- `collection.show()` — `_committed()` and `_owned_total()` aggregate per
  `base_card_id`, so the commitment is subtracted exactly once.
- `decks.show_deck()` — `available_to_deck`, where only *other* physical decks
  subtract.

Nothing in `src/` ever read the view. Its only consumers were assertions in
`test_decks.py`, `test_schema.py` and `test_setup_scripts.py` — tests checking a
formula that nothing shipped, which is how it stayed wrong without failing.

Deleted rather than fixed, because the thing it would become is `show()`.

**The trigger was Phase 2.** `GET /api/cards` is exactly what reaches for a view
named `available_cards`, and it would have been wrong for every card owned in
more than one printing — quietly, and only for the cards a collector most likely
owns twice. Removing it before the API layer existed cost one afternoon of test
edits.

Two behaviours changed with it, both improvements:

- A real card you own **none** of now reports `0/0/0` instead of vanishing. The
  view inner-joined `collection`, so a card browser would have had to paper over
  the absence.
- `None` now means "not a real card", not "not in your collection".

`db_setup.VIEWS` is empty and `create_schema` makes no views. `LEGACY_VIEW_NAMES`
still carries `available_cards` so `--reset` drops it from databases created
before this entry; remove it when no such database is in use.

---

## D-027 — Attribute and life are synced; `rotation_block` is not

**2026-09-15**

Three card fields were added together because they are the last changes `cards`
needed before real collection data goes in, and while the tables are empty a
schema edit is a `--reset` rather than a migration.

**`attribute` and `life` were free — the API already sent them.** Sync simply
discarded both, which is why `normalize_attributes` had sat as dead code since
Phase 0, called by nothing but its own tests. Checked against all 4,459 cached
rows:

| Category | Rows | Has life | Has attribute |
|---|---|---|---|
| Character | 3,537 | 0 | 3,531 |
| Event | 559 | 0 | 0 |
| Leader | 297 | **297** | 296 |
| Stage | 66 | 0 | 0 |

So **life is Leaders-only**, and `normalize_life` guards on category exactly as
`normalize_counter` does — a shifted row cannot give a Character a life total.

The dead `normalize_attributes` turned out to be **insufficient**, not merely
unused: it split on `/` only, while the API separates most dual attributes with
a space (`"Slash Strike"`). It now splits on both and validates against the five
real attributes, which also drops the three junk values in the feed — `"?"`,
`"N/A"`, and `"Straw Hat Crew ODYSSEY"` on `OP09-033`. Attribute is a closed set
in this game, so validating is safe in a way it would not be for sub-types.

**`rotation_block` ships as an empty column, on purpose.**

Block is printed on every card and absent from every one of the 19 fields
OptcgAPI serves. It is a property of a *set*, so one `{set_code: block}` mapping
would derive it for every card — the `SEED_TYPES` pattern. Two things block
writing that mapping today:

1. **No trusted source.** Guessing where the set boundaries fall produces
   confidently wrong legality answers, which is worse than none.
2. **`set_code` is not yet consistent.** The feed serves `OP-01` *and* `OP01`,
   `ST-01` *and* `ST01`, `EB-01` *and* `EB04`, plus composites like
   `OP14-EB04` — 68 distinct values for far fewer real sets. A key written one
   way silently misses the other spelling.

The column lands now anyway because adding it later, once the collection is
entered, means an `ALTER TABLE` against data that matters.

`is_rotation_legal` is **tri-state**: `None` for an unknown block, not `False`.
With the mapping empty, returning `False` would mark the entire collection
illegal. The legality rule itself is settled — block 1 has rotated out, anything
from block 2 up is legal (`ROTATION_LEGAL_FROM = 2`).

`test_rotation.py` asserts `SET_BLOCKS == {}`. That test is designed to fail the
moment someone populates the mapping, as the prompt to write the tests it then
deserves.

---

## D-028 — Set and product are different fields

**2026-09-15** · *Clears blocker 2 in [D-027](#d-027--attribute-and-life-are-synced-rotation_block-is-not); extends [D-003](#d-003--collection-is-printing-level-decks-are-gameplay-level) to sets*

`set_code` looked broken. 1,187 of 4,338 cards carry a `set_code` that disagrees
with their own card id - `OP14-120` is filed under `OP14-EB04`, and an OP05 card
can arrive stamped `PRB-01`. The first read was that the field was wrong for a
quarter of the collection.

**It isn't wrong. It answers a different question.** One gameplay card, Koala:

| card_image_id | game_set | set_code | printing_kind |
|---|---|---|---|
| `OP05-006` | OP05 | `OP-05` | base |
| `OP05-006_p1` | OP05 | `OP-05` | parallel |
| `OP05-006_p3` | OP05 | `PRB-01` | parallel |
| `OP05-006_r1` | OP05 | `PRB-01` | reprint |
| `EB03_OP05-006_p1` | OP05 | `EB-03` | parallel |

Every `set_code` there is correct. `_r1` is a **reprint** that came in the PRB-01
premium booster; `EB03_OP05-006_p1` came in EB-03. They are OP05 cards printed
in other products. So there are two real axes, and this is the same printing-vs-
gameplay split D-003 already draws, applied to sets:

- **`set_code`** - the *product* a printing came in. Kept exactly as the API
  sends it. Answers "which box did this come out of".
- **`game_set`** - the *set the card belongs to*, derived from `base_card_id`.
  Answers "is this an OP05 card", which is what rotation and filtering need.

**Why derive rather than clean `set_code`:** it cannot answer the question even
in principle. There is no `OP-14` or `OP-15` in the feed at all - those sets sit
under `OP14-EB04` / `OP15-EB04`, mixed with EB04 cards and strays from eight
other sets. The promo feed also drops the hyphen (`OP01`, not `OP-01`) on 163
cards, all rarity PR. `base_card_id` has none of that: 53 clean prefixes across
all 4,338 rows, nothing unparseable, including the two `EB03_OP05-…` ids that
`strip_printing_suffix` already resolves correctly.

So `SET_BLOCKS` in `rotation.py` is now keyed on `game_set` - 53 stable values
instead of 68 product spellings. The only thing still missing for D-027 is a
trusted block mapping.

**`printing_kind` came out of the same investigation.** The variant suffix
already encoded the alt-art axis; classifying it makes it queryable:

| kind | cards | suffix |
|---|---|---|
| base | 2,648 | none |
| parallel | 1,106 | `p1`-`p8` |
| reprint | 328 | `r1`-`r3` |
| promo | 256 | `pr1`-`pr12` |

Nothing is unclassified. `pr` is matched before `p`, or every promo reads as a
parallel. This is what D-016 needs to stay honest - import credits the base
printing, and a picker can now offer the other three kinds by name rather than
by asking the user to know that `_r1` means reprint.

**One id was repaired in passing.** `OP04-016_P1` shipped with an uppercase
variant where all 691 other parallels use lowercase. `normalize_card_image_id`
now folds the suffix, so one printing stops being two primary keys. Verified
collision-free across all 4,338 ids before folding.

---

## D-029 — The API reuses printing ids; sync resolves them

**2026-09-15** · *Fixes a Phase 0 defect; extends [D-014](#d-014--normalize-printing-keys-and-gameplay-identities) and [D-028](#d-028--set-and-product-are-different-fields)*

Sync assumed one `card_image_id` names one printing. The API doesn't honour
that. Across its three feeds, **118 ids each label two or three physically
different cards**, and `INSERT OR REPLACE` let whichever row came last win.
Promos are fetched last, so the promo won 63 times; 41 more collisions live
inside a single feed and were decided by whatever order the API returned.

The regular `OP01-016` Nami and her alt-art promo both arrive as `OP01-016`.
The promo row carries a typo — counter 1000 instead of 2000 — and because it
overwrote the regular card, **the regular Nami had counter 1000** in every
database this project ever built. 180 base printings carried rarity PR. 53
carried pre-errata text. Nothing logged it, and 376 tests passed, because no
test compared the feeds against each other.

**Same id means same card.** A reprint, foil or promo of `OP01-016` is
mechanically identical to it — that's a rule of the game, not a data
convention. So the fix has two halves, both in `src/collisions.py` (pure, no
DB), run before any row is sanitized:

1. **Every row gets a unique id.** The base row keeps the API's id — chosen
   deterministically: plain image filename, `set_code` matching `game_set`,
   not from the promo feed, shortest name, then image URL. Feed order never
   decides. Each other row is re-identified, cheapest rule first:

   | rule | source of the new id | cases |
   |---|---|---|
   | image filename carries a real `_p1`/`_r1` id | `OP07-076_r1.jpg` → `OP07-076_r1` | 10 |
   | slug of what the name adds to the base name | `Nami - OP01-016 (Alternate Art)` → `OP01-016_alternate_art` | 107 |
   | that plus the set code, then an ordinal | never reached today | 0 |

   The API's own image storage had hit the same collisions — a second file
   wanting `OP04-056_p2.jpg` became `OP04-056_p2_6KicMKv.jpg`. That random
   token is the one thing in the feed that separates all 118 pairs, and it is
   deliberately **not** used: it means nothing and could change. The name
   part of a filename is used only when it is a real printing id.

2. **Gameplay fields are inherited from the base row.** Cost, power, counter,
   colour, attribute, life, effect and sub-types are copied onto every
   re-identified row. It keeps its own name, image, rarity and `set_code` —
   the things that actually differ between printings. A typo on a promo can
   no longer reach the regular card, and can't survive on the promo either.

**Four ids were the same row listed twice** — `ST25-001 Alvida` and three
others, identical in every field but the image token. Those collapse to one
row rather than becoming two printings.

**Rows with no image** (61, mostly promos) fall through to the name rule. Six
of them collide; the name separates all six.

Result on the current cache: 4,459 rows → **4,455 printings, 0 collisions**.
Nami is `counter 2000, R, base`; her promo is `OP01-016_alternate_art, PR,
promo`, counter 2000. The only base printings still rated PR are the 118
cards of the promo set itself, which is correct.

**Synthesised ids are stable as long as names are.** They derive from the
card's name and id, not from image tokens or feed order. If the API renames a
card, that printing re-identifies and the old row goes stale — which the
planned stale-printing check will report. Accepted.

**Left visible on purpose:** `OP01-016_p2 Nami (SP)` has counter 1000 too. It
isn't a collision — it has its own id — so this entry doesn't touch it. The
same-id-same-card rule argues that *every* printing should inherit gameplay
fields from its base printing, not only re-identified ones. That's a broader
decision for the collection re-keying, where gameplay data moves to
`base_card_id` anyway.

---

## D-030 — Every copy is somewhere; art is recorded on the copy

**2026-09-15** · *Supersedes [D-019](#d-019--storage-locations-move-into-phase-1) and [D-016](#d-016--deck-import-credits-the-base-printing); reshapes the collection half of [D-003](#d-003--collection-is-printing-level-decks-are-gameplay-level); extends [D-025](#d-025--locations-have-a-kind-decks-are-the-fifth-category) and D-011*

Designed with the owner before a single card was entered, from how the cards
physically live. Three facts about that drove everything:

1. A decklist names cards, not arts (`4 Monkey D. Luffy OP05-097`). Forcing a
   printing at entry made D-016 *guess* the plain art and record the guess as
   fact. The schema had no way to say "four of these, unsorted".
2. Art is decided by looking at the card, and you look at it where it is — in
   the binder, in the deck. So that is where it should be recorded: click the
   copy, set the art.
3. There is no such thing as a copy that is nowhere. What isn't in a binder,
   box or deck is in the loose pile. Every existing card is accounted for;
   that is the point of a tracker.

### The model

**Ownership is the set of copies, and every copy is in exactly one container.**

```
copies(base_card_id, card_image_id | NULL, location_id | deck_id, quantity)
```

- `base_card_id` is the gameplay identity — always present.
- `card_image_id` names the art when known; `NULL` means *unsorted*. Setting
  the art on some of a row's copies splits the row. The total never changes.
- The container is a storage location (D-025 kinds: storage, binder, loose,
  loan) **or a physical deck**. Same row shape, same logic, either way.
- `quantity > 0`, always. There is no negative pile and no over-placement.

**`just-loose` is a built-in location** of kind `loose`, created with the
schema, not deletable or renamable. It is where copies land when no container
is named, and where they are taken from when none is named. The owner can add
other loose locations ("Floor1"); this one is the default.

**Everything else is derived, never stored** (D-009):

| question | answer |
|---|---|
| how many do I own | `SUM(quantity)` over the card's copies |
| how many of art X | the same sum, filtered to that `card_image_id` |
| committed | copies whose container is a physical deck |
| loose | copies in a `loose`-kind location |
| deck completeness | recipe (`deck_cards`) minus copies placed in that deck, per card |

The `collection` table is **removed**. It was the total; the total is now a
sum. Keeping both would be two numbers that must agree, which is what this
entry exists to get rid of.

### Operations

Every write is a move between containers, or an add/remove at the edge:

```
add     OP05-097 4                      4 unsorted copies into just-loose
add     OP05-097 2 --art p1 --to "Binder A"
move    OP05-097 2 --from just-loose --to "Binder A"
set-art OP05-097 1 --in "Binder A" p1   splits the row; total unchanged
remove  OP05-097 1                      from just-loose (a sale)
remove  OP05-097 1 --from "Law deck"
loan    OP05-097 1 "Marco"              a move to a loan-kind location
```

**Without `--art`, a move or remove takes unsorted copies only.** If the
source has fewer unsorted copies than asked, it refuses and says what's there
(`just-loose has 1 unsorted and 2 p1 - say which with --art`). The D-017 rule:
ask, never guess. So art is always settled *before* a copy leaves - for a sale
because you named the row, for a loan because the row moves with its art.

**A move that exceeds the source is an error and writes nothing.** The
overflow case under D-019 - placed more than owned - warned and wrote. It
can't happen here; what can happen is "move 3 when there are 2", and that is
a miscount or a forgotten `add`, not bookkeeping to tolerate. The fix is one
`add` away and the numbers never lie. Decided explicitly by the owner.

### Decks

The recipe stays exactly where it is: `deck_cards`, gameplay-level, untouched.
What changes is that the sleeved copies are now modelled instead of assumed:

- **Importing a physical deck places the copies into the deck** as it credits
  them. D-011's "if it's sleeved, you own it" becomes explicit rows rather
  than an inference from `is_physical`. They land unsorted; sort them later.
- **A physical deck can be incomplete.** `deck show` compares recipe against
  copies in the deck. This is the mechanism, not a defect: strip a deck to
  build another, and the hole stays visible until you refill it.
- Re-import with `replace` (D-022) only adds copies for cards the new recipe
  needs more of. It never removes copies; cards dropped from the recipe stay
  in the deck's container until moved, and show as *in deck, not in recipe*.
- Toggling a physical deck to wishlist moves its copies to `just-loose` - the
  deck is unmade. Toggling wishlist to physical places nothing; the deck
  shows incomplete until copies are moved in.
- Deleting a deck moves its copies to `just-loose`. You still own the cards.
- Wishlist decks are recipe-only. Placing into one is refused.
- 4-copy warnings (D-004) are unchanged.

### What dies

- **D-016.** Import no longer picks an art. Its whole reason was that the
  schema demanded one.
- **D-019.** Placement is no longer optional bookkeeping; it is the ledger.
  `unplaced` and the over-placement warning go with it.
- **The `collection` table** and `resolve.default_printing`, which existed
  to serve D-016.
- The `collection_printings` table proposed during design - a second
  breakdown of the same total, free to disagree with the placements.

### What it deliberately doesn't do

- **No history.** Sold, traded and lost copies are removed and forgotten;
  the owner was asked and doesn't want a ledger. `loan` is not removal - the
  copy is still owned, in a `loan` location.
- **No hypothetical decks yet.** "Could I build this from what I own" is the
  Phase 3 deck builder. It is a count over these rows and needs no schema.
- **Gameplay-field inheritance for *all* printings is left open.** Extending
  D-029's base-wins rule from re-identified rows to every variant would fix
  `OP01-016_p2 Nami (SP)` reading counter 1000 against its base's 2000 — 748
  variants disagree with their base, 65 of them on counter. It is held back
  because the base row is not always the trustworthy one: `P-055` is a promo
  that *is* the base id and reads counter 0 where two set-feed variants read
  1000, so blanket inheritance would spread that instead of fixing it. A
  majority vote across a card's printings self-corrects single typos and is the
  likelier answer, but it is a heuristic and wants deciding on its own.

### Why now

`collection`, `decks` and `storage_locations` were empty. This was a schema
edit and a `--reset`. Every day after cards go in, it is a migration with the
owner's real data inside it.

---

## D-031 — Every printing of a card agrees on its gameplay fields

**2026-09-15** · *Completes the bullet D-030 left open; extends [D-029](#d-029--the-api-reuses-printing-ids-sync-resolves-them) from re-identified rows to every printing; refines [D-008](#d-008--counter-is-null-for-leaders-events-and-stages)*

An alt art is the same card. Cost, power, counter, colour, attribute, life and
effect cannot differ between printings without one of them being wrong. **748
variants disagreed with their base printing** - 691 on effect text, 74 on
sub-types, 65 on counter, 19 on power.

The counter disagreements were the ones with teeth, because they change how you
play. Broken down, they were barely a conflict at all:

| | count | reading |
|---|---|---|
| variant empty (`0`/`NULL`), base has a number | 49 | the variant is missing data |
| base empty, variant has a number | 6 | the *base* is missing data |
| `NULL` vs `0` | 4 | one fact, two spellings |
| both a real number, genuinely conflicting | 6 | one of them is a typo |

So the rule is not a heuristic: **the base printing wins, unless the base is
empty, in which case the first printing with a real value supplies it.**

**A majority vote was tried and rejected.** It looked principled and it is
wrong: across the 65 it picks the base value 34x, a different value 14x and ties
17x - and on `OP01-041` Kouzuki Momonosuke (base 1000, three variants 0) the
majority is 0, which would corrupt a correct value. Counted opinions are not
evidence when the count is of the same API's mistakes.

**The six genuine conflicts all resolve to base-wins**, checked card by card:

- `OP01-016` Nami - base, `_p1` and `_alternate_art` carry the API's
  `"This card has been officially errata'd."` marker and read 2000; the SP alone
  reads 1000 and still has the *pre-errata* wording. A stale row.
- `ST06-006` Tashigi - the SP reads 1000 and its effect text is corrupt too
  (`"Characters 2 cost"` where the base says `"-2 cost"`).
- `OP03-112` Pudding - three printings say 2000, the SP and a reprint say 1000.
- `P-003` Kid and `OP03-102` Sanji - the odd row reads a counter exactly equal
  to the card's power. See the counter ceiling below.

**Using the errata marker as a tie-break was considered and dropped.** It is
sound reasoning and it is already covered: of 91 cards carrying the marker,
there is **not one** where a variant has it and the base doesn't - the only
shape in which it could ever overrule base-wins. It would be a rule no data
exercises. Inheritance propagates the errata'd text from the base to every
printing anyway, which is the outcome it was reaching for.

It is kept as a **tripwire** instead of a rule: sync counts the cards where a
variant carries the marker and its base doesn't, and warns. That is precisely
the shape in which base-wins is backwards, so if the feed ever produces one, it
says so rather than silently overwriting corrected text with outdated text. The
count is 0 today.

**A counter above 2000 is reported, never rewritten.** All five instances in the
feed have `counter` exactly equal to `power` - the power leaked into the field,
the same defect class as D-013's field shift. Real counters are 1000 (1,971
cards) or 2000 (556). Inheritance fixes the two that had a healthy sibling
printing; the remaining three are base rows where every printing agrees and
every printing is wrong, so sync prints a warning and leaves them. Rewriting on
a guess would hide the next one.

**`NULL` and `0` stop meaning the same thing in `counter`.** D-008 already uses
`NULL` for "this category cannot have a counter". The API also sent nothing for
7 Characters that do have the field and simply have no counter - all of them
high-power cards. A Character now always gets an integer, `0` meaning "no
counter", and `NULL` stays reserved for not-applicable. `0` still counts as
*empty* for inheritance, because the six cases where a base read 0 against
variants reading 1000 were all the base missing data.

**`src/overrides.py` is the escape hatch** - a hand-maintained
`{base_card_id: {column: value}}` dict applied after inheritance, keyed on the
gameplay id so a correction reaches every printing. Same pattern as `SEED_TYPES`
and `SET_BLOCKS`. It exists for the case inheritance cannot reach: every
printing agrees and every printing is wrong. It **ships empty**: `OP08-001`
Chopper reading `life 1` is a known typo, but the correct value has to be read
off the physical card, and a guessed override would be worse than the typo it
replaces.

Result on the current feed: 852 fields rewritten across 455 cards, and **zero
variants left disagreeing with their base**.
