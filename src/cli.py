"""Command-line interface for the collection and deck manager.

    python -m src.cli deck import mydeck.txt --name "Red Luffy" --physical
    python -m src.cli deck paste --name "Red Luffy"           # type or paste the list
    python -m src.cli deck show "Red Luffy"
    python -m src.cli collection add OP05-097 4              # unsorted, into just-loose
    python -m src.cli collection move OP05-097 2 --to "Binder A"
    python -m src.cli collection set-art OP05-097 1 p1 --in "Binder A"
    python -m src.cli collection show OP05-097
    python -m src.cli location show "Binder A"
    python -m src.cli loan OP05-097 1 "Marco"
    python -m src.cli shell                                   # optcg> prompt, no prefix

Every command takes --db to point at a different SQLite file.
"""

from __future__ import annotations

import argparse
import shlex
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
    import_decklist,
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

# A paste ends on this line rather than a blank one, because decklists often
# separate their sections with blank lines.
PASTE_END = "end"


def read_pasted_list() -> str:
    """Collect decklist lines from the console until `end` or end of input."""
    print(f"paste the list, then type {PASTE_END} on its own line (or Ctrl+Z, Enter):")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().lower() == PASTE_END:
            break
        lines.append(line)
    return "\n".join(lines)


def _import_and_report(run, on_conflict: str | None) -> int:
    """Run an import, asking about a name clash, and print the result.

    `run(policy)` does the actual import; file and paste only differ there.
    """
    try:
        result = run(on_conflict)
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
        result = run(policy)

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


def cmd_deck_import(conn: sqlite3.Connection, args) -> int:
    is_physical = not args.wishlist
    return _import_and_report(
        lambda policy: import_deck(conn, args.path, args.name, is_physical, policy),
        args.on_conflict,
    )


def cmd_deck_paste(conn: sqlite3.Connection, args) -> int:
    is_physical = not args.wishlist
    text = read_pasted_list()
    return _import_and_report(
        lambda policy: import_decklist(conn, text, args.name, is_physical, policy),
        args.on_conflict,
    )


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
    if detail.is_physical:
        status += " - built" if detail.is_built else f" - {detail.unsleeved_total} not sleeved"
    print(f"{detail.name}  [{kind}]  {detail.total_cards} cards - {status}")
    print()
    print(f"  {'card':<14} {'need':>4} {'in':>4} {'own':>4} {'avail':>5} {'miss':>4}  name")
    for c in detail.cards:
        marker = "L" if c.is_leader else " "
        extra = "  (not in the recipe)" if c.needed == 0 else ""
        print(
            f"{marker} {c.base_card_id:<14} {c.needed:>4} {c.in_deck:>4} {c.owned:>4} "
            f"{max(0, c.available_to_deck):>5} {c.missing:>4}  {c.name}{extra}"
        )
    print()
    print("  'in' is copies actually sleeved into this deck; 'avail' counts copies")
    print("  not sleeved into OTHER physical decks.")
    return 0


def cmd_deck_delete(conn: sqlite3.Connection, args) -> int:
    result = delete_deck(conn, args.deck)
    print(f"deleted deck {result.name!r}. "
          f"{result.copies_returned} copies went back to just-loose.")
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
    to = store.resolve_container(conn, args.to, args.kind)
    total = coll.add(conn, args.base_card_id, args.quantity, args.art, to)
    art = args.art or "unsorted"
    print(f"{args.base_card_id}: +{args.quantity} {art} into {to.name} - now {total} owned")
    return 0


def cmd_collection_remove(conn: sqlite3.Connection, args) -> int:
    frm = store.resolve_container(conn, getattr(args, "from"), args.kind)
    total = coll.remove(conn, args.base_card_id, args.quantity, args.art, frm)
    print(f"{args.base_card_id}: -{args.quantity} from {frm.name} - now {total} owned")
    return 0


def cmd_collection_move(conn: sqlite3.Connection, args) -> int:
    frm = store.resolve_container(conn, getattr(args, "from"), args.from_kind)
    to = store.resolve_container(conn, args.to, args.to_kind)
    now_there = coll.move(conn, args.base_card_id, args.quantity, frm, to, args.art)
    print(f"{args.base_card_id}: {args.quantity} moved {frm.name} -> {to.name} "
          f"({now_there} there now)")
    return 0


def cmd_collection_set_art(conn: sqlite3.Connection, args) -> int:
    where = store.resolve_container(conn, args.location, args.kind)
    unsorted_left = coll.set_art(
        conn, args.base_card_id, args.quantity, args.art, where, args.from_art
    )
    print(f"{args.base_card_id}: {args.quantity} in {where.name} recorded as {args.art} "
          f"({unsorted_left} still unsorted there)")
    return 0


def cmd_loan(conn: sqlite3.Connection, args) -> int:
    frm = store.resolve_container(conn, getattr(args, "from"), args.from_kind)
    to = store.resolve_container(conn, args.to_whom, "loan")
    now_there = coll.move(conn, args.base_card_id, args.quantity, frm, to, args.art)
    print(f"{args.base_card_id}: {args.quantity} loaned to {to.name} "
          f"({now_there} out with them). You still own them.")
    return 0


def cmd_collection_show(conn: sqlite3.Connection, args) -> int:
    status = coll.show(conn, args.base_card_id)
    if status is None:
        print(f"no card with id {args.base_card_id!r}")
        return 1

    print(f"{status.base_card_id}  {status.name}  -  owned {status.total_owned}")

    if status.arts:
        print("\n  by art")
        for a in status.arts:
            tag = a.card_image_id or "(unsorted)"
            variant = a.printing_variant or ("-" if a.card_image_id else "")
            print(f"    {tag:<34} {variant:<8} {a.quantity:>3}")

    if status.holdings:
        print("\n  where they are")
        for h in status.holdings:
            art = h.card_image_id or "unsorted"
            print(f"    {h.container:<24} {h.kind:<8} {art:<34} {h.quantity:>3}")

    unowned = [p for p in status.printings
               if p.card_image_id not in {a.card_image_id for a in status.arts}]
    if unowned:
        print(f"\n  {len(unowned)} other printing(s) exist that you don't own")

    print(f"\n  committed {status.committed}   loose {status.loose}   "
          f"unsorted {status.unsorted}")
    return 0


def cmd_collection_list(conn: sqlite3.Connection, args) -> int:
    rows = coll.list_cards(
        conn, args.set, args.color, args.category, args.owned_only, args.limit, args.search
    )
    if not rows:
        print("no cards matched")
        return 0
    for card_image_id, name, category, color, owned, card_owned in rows:
        unsorted = card_owned - owned
        tail = f"  (+{unsorted} unsorted on the card)" if unsorted and owned else ""
        if not owned and unsorted:
            tail = f"  ({card_owned} owned, art not recorded)"
        print(f"  {card_image_id:<24} {owned:>3}  {category:<10} {color or '-':<12} {name}{tail}")
    print(f"\n  {len(rows)} printing(s)")
    return 0


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def cmd_location_add(conn: sqlite3.Connection, args) -> int:
    store.add_location(conn, args.name, args.kind, args.notes)
    print(f"created {args.kind} {args.name!r}")
    return 0


def cmd_location_list(conn: sqlite3.Connection, args) -> int:
    locations = store.list_locations(conn, args.kind)
    if not locations:
        print("no locations yet - create one with `location add`")
        return 0
    current = None
    for loc in locations:
        if loc.kind != current:
            current = loc.kind
            print(f"\n{current.upper()}")
        print(f"{loc.distinct_cards:>6} cards {loc.total_cards:>7} copies  {loc.name}"
              + (f"   ({loc.notes})" if loc.notes else ""))
    return 0


def cmd_location_rename(conn: sqlite3.Connection, args) -> int:
    store.rename_location(conn, args.old, args.new, args.kind)
    print(f"renamed {args.old!r} to {args.new!r}")
    return 0


def cmd_location_delete(conn: sqlite3.Connection, args) -> int:
    moved = store.delete_location(conn, args.name, args.kind)
    print(f"deleted location {args.name!r}. {moved} copies went back to just-loose.")
    return 0


def cmd_location_show(conn: sqlite3.Connection, args) -> int:
    rows = store.contents(conn, args.name, args.limit, args.kind)
    container = store.resolve_container(conn, args.name, args.kind)
    if not rows:
        print(f"{container.name} is empty")
        return 0
    for base_card_id, card_image_id, name, quantity in rows:
        art = card_image_id or "(unsorted)"
        print(f"  {base_card_id:<14} {art:<34} {quantity:>3}  {name}")
    print(f"\n  {sum(r[3] for r in rows)} copies, {len({r[0] for r in rows})} card(s)")
    return 0


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------

SHELL_BANNER = """optcg shell - type commands without the `python -m src.cli` prefix.
  e.g.  collection add OP05-097 4      deck show "Red Luffy"
  help lists the commands, exit (or Ctrl+Z, Enter) leaves."""


def split_command(line: str) -> list[str] | None:
    """Split a shell line like a terminal would. None on an unclosed quote.

    Backslash is not an escape character, so Windows paths such as
    C:\decks\red.txt survive, and # is not a comment.
    """
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def run_shell(db: Path) -> int:
    """Read commands until `exit`, running each one as its own CLI call.

    Every line gets a fresh connection, so a line behaves exactly as it would
    typed after `python -m src.cli` - same output, same errors, same commits.
    """
    print(SHELL_BANNER)
    while True:
        try:
            line = input("optcg> ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue

        argv = split_command(line)
        if argv is None:
            print("error: unclosed quote", file=sys.stderr)
            continue
        if not argv:
            continue
        if argv[0] in ("exit", "quit"):
            return 0
        if argv[0] == "shell":
            print("already in the shell")
            continue
        if argv[0] == "help":
            argv = ["--help"]

        try:
            main(["--db", str(db), *argv])
        except SystemExit:
            # argparse exits after --help and on bad arguments; it has already
            # printed why, and one typo shouldn't end the session.
            pass


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

    p = deck.add_parser("paste", help=f"type or paste a decklist, ending with '{PASTE_END}'")
    p.add_argument("--name", required=True, help="deck name")
    p.add_argument("--physical", action="store_true", help="a deck you have sleeved (default)")
    p.add_argument("--wishlist", action="store_true", help="a deck you want to build")
    p.add_argument("--on-conflict", choices=["replace", "new"], help="skip the prompt if the name exists")
    p.set_defaults(func=cmd_deck_paste)

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

    container_help = f"one of: {', '.join(store.CONTAINER_KINDS)}"
    art_help = "printing: a full id (OP05-097_p1), a variant (p1), or 'base'"

    p = col.add_parser("add", help="record copies you now own")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.add_argument("quantity", type=int)
    p.add_argument("--art", default=None, help=art_help + "; omit if not sorted yet")
    p.add_argument("--to", default=None, help="container (default: just-loose)")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None, help=container_help)
    p.set_defaults(func=cmd_collection_add)

    p = col.add_parser("remove", help="copies leaving the collection - sold, traded, lost")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.add_argument("quantity", type=int)
    p.add_argument("--art", default=None, help=art_help)
    p.add_argument("--from", default=None, help="container (default: just-loose)")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None, help=container_help)
    p.set_defaults(func=cmd_collection_remove)

    p = col.add_parser("move", help="move copies between containers")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.add_argument("quantity", type=int)
    p.add_argument("--from", default=None, help="source (default: just-loose)")
    p.add_argument("--to", required=True, help="destination location or deck")
    p.add_argument("--art", default=None, help=art_help)
    p.add_argument("--from-kind", choices=store.CONTAINER_KINDS, default=None, help=container_help)
    p.add_argument("--to-kind", choices=store.CONTAINER_KINDS, default=None, help=container_help)
    p.set_defaults(func=cmd_collection_move)

    p = col.add_parser("set-art", help="sort copies: say which printing some of them are")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.add_argument("quantity", type=int)
    p.add_argument("art", help=art_help)
    p.add_argument("--in", dest="location", default=None,
                   help="container holding them (default: just-loose)")
    p.add_argument("--from-art", default=None,
                   help="re-sort copies already recorded as this art")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None, help=container_help)
    p.set_defaults(func=cmd_collection_set_art)

    p = col.add_parser("show", help="one card: how many, which arts, where they are")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.set_defaults(func=cmd_collection_show)

    p = col.add_parser("list", help="browse cards")
    p.add_argument("--set", help="set code, e.g. OP05")
    p.add_argument("--color")
    p.add_argument("--category", help="Leader, Character, Event or Stage")
    p.add_argument("--search", default=None, help="match part of a card name")
    p.add_argument("--owned-only", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_collection_list)

    # ---- location -------------------------------------------------------
    loc = sub.add_parser("location", help="where loose cards are stored").add_subparsers(
        dest="action", required=True
    )

    # A bare name can match more than one kind, so every command that resolves
    # a location takes --kind to disambiguate.
    kind_help = f"one of: {', '.join(store.LOCATION_KINDS)}"

    p = loc.add_parser("add", help="create a storage location")
    p.add_argument("name")
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default="storage", help=kind_help)
    p.add_argument("--notes")
    p.set_defaults(func=cmd_location_add)

    p = loc.add_parser("list", help="list storage locations, grouped by kind")
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default=None, help=kind_help)
    p.set_defaults(func=cmd_location_list)

    p = loc.add_parser("rename", help="rename a storage location")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default=None, help=kind_help)
    p.set_defaults(func=cmd_location_rename)

    p = loc.add_parser("delete", help="delete a location (its copies return to just-loose)")
    p.add_argument("name")
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default=None, help=kind_help)
    p.set_defaults(func=cmd_location_delete)

    p = loc.add_parser("show", help="what's in a location or deck")
    p.add_argument("name", nargs="?", default=None, help="default: just-loose")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"one of: {', '.join(store.CONTAINER_KINDS)}")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_location_show)

    # ---- loans ----------------------------------------------------------
    p = sub.add_parser("loan", help="lend copies to someone (you still own them)")
    p.add_argument("base_card_id", help="gameplay id, e.g. OP05-097")
    p.add_argument("quantity", type=int)
    p.add_argument("to_whom", help="a location of kind 'loan' - create it with `location add`")
    p.add_argument("--art", default=None, help="which printing is going out")
    p.add_argument("--from", default=None, help="source (default: just-loose)")
    p.add_argument("--from-kind", choices=store.CONTAINER_KINDS, default=None)
    p.set_defaults(func=cmd_loan)

    # ---- shell ----------------------------------------------------------
    sub.add_parser("shell", help="type commands one after another, without the prefix")

    return parser


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    args = build_parser().parse_args(argv)

    if getattr(args, "physical", False) and getattr(args, "wishlist", False):
        print("error: --physical and --wishlist are mutually exclusive", file=sys.stderr)
        return 2

    if args.group == "shell":
        return run_shell(args.db)

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
