# Foil & Printing Variants — what's actually in your data

Written 2026-08-11 from the synced DB (4338 printings). Examples only — this is
not a list of every card. Read this before deciding how you want to record foils.

---

## 1. The three-suffix system

Every alternate printing gets a suffix on its `card_image_id`. There are only
three, and **1690 of your 4338 printings have one** (the other 2648 are the
plain base printing).

| Suffix | Count | Means |
|---|---|---|
| `_p1` … `_p8` | 1106 | Parallel / alternate printing |
| `_r1` … `_r3` | 328 | Reprint |
| `_pr1` … `_pr9` | 256 | Promo printing |

```
OP05-097        ← base printing, no suffix
OP05-097_p1     ← first alternate printing
OP05-097_p2     ← second alternate printing
```

## 2. ⚠️ The number is an index, NOT a treatment

This is the part worth knowing. `_p1` does **not** mean "foil" or any one
specific thing. It just means *"the first alternate printing of this card."*
What that printing actually looks like differs per card:

```
EB01-009_p1   Just Shut Up and Come with Us!!!! (Pirate Foil)
OP01-001_p1   Roronoa Zoro (001) (Parallel)
OP01-008_p1   Cavendish (Box Topper)
EB01-023_p1   Edward Weevil - EB01-023 (SP)
```

All four are `_p1`. All four are different treatments. **The card's name is the
only reliable indicator of what the printing actually is** — the suffix only
tells you it isn't the base printing.

Treatments can also stack:

```
EB01-006_p2   Tony Tony.Chopper (Alternate Art) (Manga)
```

---

## 3. Explicitly foil-named treatments

Only **170 printings** say "Foil" in the name. These are the ones where foil is
stated outright:

| Treatment | Count | Example |
|---|---|---|
| Pirate Foil | 80 | `EB01-009_p1` — Just Shut Up and Come with Us!!!! (Pirate Foil) |
| Jolly Roger Foil | 68 | `OP01-006_p3` — Otama (Jolly Roger Foil) |
| Textured Foil | 22 | `OP01-029_p3` — Radical Beam!! (Textured Foil) |

Note `OP01-029` has both a `_p2` Jolly Roger Foil **and** a `_p3` Textured Foil
— same card, two different foil treatments, each its own printing.

---

## 4. Art treatments (foil in practice, not named "foil")

These are the big categories. They're generally shiny/premium cards, but the
name doesn't use the word "foil":

| Treatment | Count | Example |
|---|---|---|
| Alternate Art | 476 | `EB01-001_p1` — Kouzuki Oden (Alternate Art) |
| Reprint | 249 | `EB01-009_r1` — Just Shut Up and Come with Us!!!! (Reprint) |
| Parallel | 180 | `OP01-001_p1` — Roronoa Zoro (001) (Parallel) |
| SP | 108 | `EB01-023_p1` — Edward Weevil - EB01-023 (SP) |
| Full Art | 72 | `OP01-006_p4` — Otama (Full Art) |
| Manga | 40 | `EB02-061_p2` — Monkey.D.Luffy (061) (Manga) |
| SPR | 26 | `EB01-001_p2` — Kouzuki Oden (SPR) |
| Box Topper | 12 | `OP01-008_p1` — Cavendish (Box Topper) |
| Wanted Poster | 12 | `OP05-119_p6` — Monkey.D.Luffy (Wanted Poster) |

Plus a long tail of event/tournament printings: Online Regional 2023 (36),
Offline Regional 2023 (34), Dash Pack, Judge Pack, Sealed Battle Kit,
Championship, Premium Card Collection, and similar.

---

## 5. Rarity codes in your data

| Code | Count | Meaning |
|---|---|---|
| `C` | 1334 | Common |
| `R` | 825 | Rare |
| `UC` | 641 | Uncommon |
| `SR` | 632 | Super Rare |
| `PR` | 483 | Promo |
| `L` | 283 | Leader |
| `SEC` | 132 | Secret Rare |
| `TR` | 8 | Treasure Rare |

---

## 6. What this means for `foil_quantity`

You asked to add the column, and it's being added — but here's the honest
picture so you can decide how to use it:

**Most foils already have their own printing.** A Jolly Roger Foil Otama is
`OP01-006_p3`, a separate row from base `OP01-006`. So recording "I own 2 of
`OP01-006_p3`" already captures the foil — `foil_quantity` isn't needed for it.

**Where the column could earn its place:** if you hit a case where the *same*
`card_image_id` exists in your binder as both a foil and a non-foil. That does
happen in some print runs, and this data can't distinguish it.

Practical suggestion: enter everything via `quantity` at the right printing
first. Leave `foil_quantity` at 0 until you physically hit a card where you need
it. The column will be there waiting, which was your original reasoning for
keeping it — and now it costs nothing, because `collection` is still empty.

---

## 7. Quick reference — reading a card id

```
EB01-006_p2
└┬─┘ └┬┘ └┬┘
 │    │   └── printing: 2nd alternate printing (check the NAME for what it is)
 │    └────── card number within the set
 └─────────── set code (EB01 = Extra Booster 1)
```

- **`base_card_id`** = `EB01-006` — the gameplay identity. Decks use this.
- **`card_image_id`** = `EB01-006_p2` — the specific physical card. Your
  collection uses this.
