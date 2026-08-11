"""Command-line interface for the collection and deck manager.

    python -m src.cli deck import mydeck.txt --name "Red Luffy" --physical
    python -m src.cli deck list
    python -m src.cli deck show "Red Luffy"
    python -m src.cli collection add OP05-097_p1 3
    python -m src.cli collection show OP05-097
    python -m src.cli location add "Binder A"
    python -m src.cli place OP05-097 "Binder A" 2

Every command takes --db to point at a different SQLite file.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from src import collection as coll
from src import storage as store
from src.collection import CollectionError
from src.db_setup import DB_PATH, connect
from src.decks import (
    DeckError,
    DeckNameExists,
    delete_deck,
    import_deck,
    list_decks,
    set_physical,
    show_deck,
)
from src.storage import StorageError


def _use_utf8_output() -> None:
    """Windows consoles default to a codepage that mangles non-ASCII names."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def warn(lines: list[str], label: str = "warning") -> None:
    for line in lines:
        print(f"  {label}: {line}")


# --------------------------------------------------------------------------
# deck
# --------------------------------------------------------------------------

def cmd_deck_import(conn: sqlite3.Connection, args) -> int:
    is_physical = not args.wishlist
    try:
        result = import_deck(conn, args.path, args.name, is_physical, args.on_conflict)
    except DeckNameExists as exc:
        print(f"A deck named {exc.name!r} already exists (id {exc.deck_id}).")
        print("  [r] replace it   [n] import as a second deck   [c] cancel")
        try:
            choice = input("choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # No console to ask on - piped input, CI, or a closed stdin.
            print()
            print("error: cannot prompt here - re-run with --on-conflict replace|new",
                  file=sys.stderr)
            return 1
        policy = {"r": "replace", "n": "new"}.get(choice)
        if policy is None:
            print("cancelled.")
            return 1
        result = import_deck(conn, args.path, args.name, is_physical, policy)

    kind = "physical" if result.is_physical else "wishlist"
    action = "replaced" if result.replaced else "created"
    print(f"{action} {kind} deck {result.name!r} (id {result.deck_id})")
    print(f"  leader:        {result.leader_id or '(none)'}")
    print(f"  cards:         {result.total_cards} across {result.distinct_cards} entries")
    if result.is_physical:
        print(f"  collection:    +{result.collection_credited} copies")
    else:
        print("  collection:    untouched (wishlist)")
    warn(result.warnings)
    return 0


def cmd_deck_list(conn: sqlite3.Connection, args) -> int:
    decks = list_decks(conn)
    if not decks:
        print("no decks yet - import one with `deck import`")
        return 0
    print(f"{'id':>4}  {'kind':<9} {'cards':>5}  {'leader':<14} name")
    for d in decks:
        kind = "physical" if d.is_physical else "wishlist"
        print(f"{d.id:>4}  {kind:<9} {d.total_cards:>5}  {d.leader_id or '-':<14} {d.name}")
    return 0


def cmd_deck_show(conn: sqlite3.Connection, args) -> int:
    detail = show_deck(conn, args.deck)
    kind = "physical" if detail.is_physical else "wishlist"
    status = "complete" if detail.is_complete else f"missing {detail.missing_total}"
    print(f"{detail.name}  [{kind}]  {detail.total_cards} cards - {status}")
    print()
    print(f"  {'card':<14} {'need':>4} {'own':>4} {'avail':>5} {'miss':>4}  name")
    for c in detail.cards:
        marker = "L" if c.is_leader else " "
        print(
            f"{marker} {c.base_card_id:<14} {c.needed:>4} {c.owned:>4} "
            f"{max(0, c.available_to_deck):>5} {c.missing:>4}  {c.name}"
        )
    if not detail.is_complete:
        print()
        print("  'avail' counts copies not committed to OTHER physical decks.")
    return 0


def cmd_deck_delete(conn: sqlite3.Connection, args) -> int:
    name = delete_deck(conn, args.deck)
    print(f"deleted deck {name!r}. Your collection is unchanged.")
    return 0


def cmd_deck_set_physical(conn: sqlite3.Connection, args) -> int:
    physical = not args.off
    warnings = set_physical(conn, args.deck, physical)
    print(f"deck {args.deck!r} is now {'physical' if physical else 'wishlist'}")
    warn(warnings)
    return 0


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------

def cmd_collection_add(conn: sqlite3.Connection, args) -> int:
    total = coll.add(conn, args.card_image_id, args.quantity)
    print(f"{args.card_image_id}: now {total}")
    return 0


def cmd_collection_set(conn: sqlite3.Connection, args) -> int:
    total = coll.set_quantity(conn, args.card_image_id, args.quantity)
    print(f"{args.card_image_id}: now {total}")
    return 0


def cmd_collection_remove(conn: sqlite3.Connection, args) -> int:
    total = coll.remove(conn, args.card_image_id, args.quantity)
    print(f"{args.card_image_id}: now {total}")
    return 0


def cmd_collection_show(conn: sqlite3.Connection, args) -> int:
    status = coll.show(conn, args.base_card_id)
    if status is None:
        print(f"no card with id {args.base_card_id!r}")
        return 1

    print(f"{status.base_card_id}  owned {status.total_owned}")
    for p in status.printings:
        tag = p.printing_variant or "base"
        line = f"  {p.card_image_id:<18} {tag:<5} {p.owned:>3}"
        if p.placements:
            line += "   " + ", ".join(f"{pl.location} {pl.quantity}" for pl in p.placements)
        print(line)

    if status.decks:
        print()
        for d in status.decks:
            kind = "physical" if d.is_physical else "wishlist"
            print(f"  {d.deck_name:<24} {d.quantity:>3}  ({kind})")

    print()
    if status.unplaced < 0:
        # More copies are recorded in locations than are actually loose.
        print(f"  committed {status.committed}   loose {status.loose}   placed {status.placed}")
        warn([f"{-status.unplaced} more copies placed in locations than are loose"])
    else:
        print(f"  committed {status.committed}   loose {status.loose}   unplaced {status.unplaced}")
    return 0


def cmd_collection_list(conn: sqlite3.Connection, args) -> int:
    rows = coll.list_cards(
        conn, args.set, args.color, args.category, args.owned_only, args.limit
    )
    if not rows:
        print("no cards matched")
        return 0
    for card_image_id, name, category, color, owned in rows:
        print(f"  {card_image_id:<18} {owned:>3}  {category:<10} {color or '-':<12} {name}")
    print(f"\n  {len(rows)} card(s)")
    return 0


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def cmd_location_add(conn: sqlite3.Connection, args) -> int:
    store.add_location(conn, args.name, args.notes)
    print(f"created location {args.name!r}")
    return 0


def cmd_location_list(conn: sqlite3.Connection, args) -> int:
    locations = store.list_locations(conn)
    if not locations:
        print("no locations yet - create one with `location add`")
        return 0
    print(f"{'cards':>6} {'copies':>7}  name")
    for loc in locations:
        print(f"{loc.distinct_cards:>6} {loc.total_cards:>7}  {loc.name}"
              + (f"   ({loc.notes})" if loc.notes else ""))
    return 0


def cmd_location_rename(conn: sqlite3.Connection, args) -> int:
    store.rename_location(conn, args.old, args.new)
    print(f"renamed {args.old!r} to {args.new!r}")
    return 0


def cmd_location_delete(conn: sqlite3.Connection, args) -> int:
    removed = store.delete_location(conn, args.name)
    print(f"deleted location {args.name!r} ({removed} placement rows). You still own the cards.")
    return 0


def cmd_place(conn: sqlite3.Connection, args) -> int:
    result = store.place(conn, args.card_image_id, args.location, args.quantity)
    print(f"{args.card_image_id} in {args.location!r}: {result.quantity}")
    if result.warning:
        warn([result.warning])
    return 0


def cmd_unplace(conn: sqlite3.Connection, args) -> int:
    remaining = store.unplace(conn, args.card_image_id, args.location, args.quantity)
    print(f"{args.card_image_id} in {args.location!r}: {remaining}")
    return 0


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="One Piece TCG collection and deck manager.",
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite file (default: {DB_PATH})")
    sub = parser.add_subparsers(dest="group", required=True)

    # ---- deck -----------------------------------------------------------
    deck = sub.add_parser("deck", help="import and manage decks").add_subparsers(
        dest="action", required=True
    )

    p = deck.add_parser("import", help="import a Limitless decklist")
    p.add_argument("path", help="decklist text file")
    p.add_argument("--name", help="deck name (defaults to the filename)")
    p.add_argument("--physical", action="store_true", help="a deck you have sleeved (default)")
    p.add_argument("--wishlist", action="store_true", help="a deck you want to build")
    p.add_argument("--on-conflict", choices=["replace", "new"], help="skip the prompt if the name exists")
    p.set_defaults(func=cmd_deck_import)

    p = deck.add_parser("list", help="list all decks")
    p.set_defaults(func=cmd_deck_list)

    p = deck.add_parser("show", help="show a deck with owned vs missing")
    p.add_argument("deck", help="deck name or id")
    p.set_defaults(func=cmd_deck_show)

    p = deck.add_parser("delete", help="delete a deck (collection untouched)")
    p.add_argument("deck", help="deck name or id")
    p.set_defaults(func=cmd_deck_delete)

    p = deck.add_parser("set-physical", help="toggle whether a deck locks its cards")
    p.add_argument("deck", help="deck name or id")
    p.add_argument("--off", action="store_true", help="make it a wishlist deck instead")
    p.set_defaults(func=cmd_deck_set_physical)

    # ---- collection -----------------------------------------------------
    col = sub.add_parser("collection", help="track what you own").add_subparsers(
        dest="action", required=True
    )

    for verb, func, helptext in [
        ("add", cmd_collection_add, "add copies of a printing"),
        ("set", cmd_collection_set, "set an absolute quantity"),
        ("remove", cmd_collection_remove, "remove copies (row is kept at 0)"),
    ]:
        p = col.add_parser(verb, help=helptext)
        p.add_argument("card_image_id", help="printing id, e.g. OP05-097_p1")
        p.add_argument("quantity", type=int)
        p.set_defaults(func=func)

    p = col.add_parser("show", help="every printing of one card, with decks and locations")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.set_defaults(func=cmd_collection_show)

    p = col.add_parser("list", help="browse cards")
    p.add_argument("--set", help="set code, e.g. OP-05")
    p.add_argument("--color")
    p.add_argument("--category", help="Leader, Character, Event or Stage")
    p.add_argument("--owned-only", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_collection_list)

    # ---- location -------------------------------------------------------
    loc = sub.add_parser("location", help="where loose cards are stored").add_subparsers(
        dest="action", required=True
    )

    p = loc.add_parser("add", help="create a storage location")
    p.add_argument("name")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_location_add)

    p = loc.add_parser("list", help="list storage locations")
    p.set_defaults(func=cmd_location_list)

    p = loc.add_parser("rename", help="rename a storage location")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=cmd_location_rename)

    p = loc.add_parser("delete", help="delete a location (ownership untouched)")
    p.add_argument("name")
    p.set_defaults(func=cmd_location_delete)

    # ---- placements -----------------------------------------------------
    p = sub.add_parser("place", help="record where copies of a printing are")
    p.add_argument("card_image_id")
    p.add_argument("location")
    p.add_argument("quantity", type=int)
    p.set_defaults(func=cmd_place)

    p = sub.add_parser("unplace", help="remove copies from a location")
    p.add_argument("card_image_id")
    p.add_argument("location")
    p.add_argument("quantity", type=int, nargs="?", default=None)
    p.set_defaults(func=cmd_unplace)

    return parser


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    args = build_parser().parse_args(argv)

    if getattr(args, "physical", False) and getattr(args, "wishlist", False):
        print("error: --physical and --wishlist are mutually exclusive", file=sys.stderr)
        return 2

    conn = connect(args.db)
    try:
        return args.func(conn, args)
    except (CollectionError, StorageError, DeckError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled.", file=sys.stderr)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
