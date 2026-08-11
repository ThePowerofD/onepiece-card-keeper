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

Import credits the variant-free base printing automatically; where none exists
(8 promo cards) it falls back to the lowest-sorting printing. Correct it
afterwards with `collection set` if you actually sleeved an alt art.

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

See [foil_and_printing_variants.md](foil_and_printing_variants.md).

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

Now six files, each differing from every other on lifecycle, mutability, access
pattern or audience: `README` (front door), `DESIGN` (what exists), `PHASE_N`
(what doesn't yet), `CLAUDE.md` (agent instructions), `notes/DECISIONS.md` (why),
`notes/foil_and_printing_variants.md` (card-data domain).

The load-bearing rule: **built and planned must never share a file.** Once a
reader can't tell which parts are real, none of it is trustworthy.

`project_recap.md` and `PHASE_0.md` were deleted rather than archived — their
content lives here and in `DESIGN.md`, and git preserves the originals.
