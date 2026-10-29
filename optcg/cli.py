"""Command-line interface for the collection and deck manager.

    optcg deck import mydeck.txt --name "Red Luffy" --physical
    optcg deck paste --name "Red Luffy"           # type or paste the list
    optcg deck show "Red Luffy"
    optcg collection add OP05-097 4              # unsorted, into just-loose
    optcg collection move OP05-097 2 --to "Binder A"
    optcg collection set-art OP05-097 1 p1 --in "Binder A"
    optcg collection show OP05-097
    optcg location show "Binder A"
    optcg loan OP05-097 1 "Marco"
    optcg backup export                           # JSON, to move machines
    optcg backup import my_collection.json        # replaces what's here
    optcg shell                                   # optcg> prompt, no prefix

Every command takes --db to point at a different SQLite file.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shlex
import sqlite3
import sys
import tempfile
from pathlib import Path

from optcg import backup
from optcg import collection as coll
from optcg import resolve
from optcg import storage as store
from optcg.backup import BackupError
from optcg.collection import CollectionError, NotThere
from optcg.db_setup import DB_PATH, DEFAULT_LOOSE_LOCATION, connect, create_schema
from optcg.known_types import SEED_TYPES, seed_known_types
from optcg.decks import (
    DeckError,
    DeckNameExists,
    delete_deck,
    import_deck,
    import_decklist,
    list_decks,
    set_physical,
    show_deck,
)
from optcg.storage import StorageError


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


def ask_to_continue(question: str) -> bool:
    """Ask before something that loses information. False means stop.

    Only asked when something would actually be lost (D-034) - a prompt that
    appears every time is one nobody reads.
    """
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # No console to ask on - piped input, CI, or a closed stdin.
        print()
        print("error: cannot prompt here - re-run with --yes", file=sys.stderr)
        return False
    if answer not in ("y", "yes"):
        print("cancelled - nothing changed.")
        return False
    return True


# --------------------------------------------------------------------------
# naming a card
# --------------------------------------------------------------------------

#: More matches than this aren't offered as a list to pick from - nobody reads
#: 91 Luffys. The person types more of the name instead.
MAX_PICK = 10

# OP06-042, ST31-004, EB02-039, P-003: an id that isn't in the catalog gets an
# "unknown card" error, not a name search that can only come back empty.
LOOKS_LIKE_AN_ID = re.compile(r"^[A-Za-z]+\d*-\d{3}$")


def describe(m: resolve.CardMatch) -> str:
    """One line that tells same-name cards apart: what they are, and what you own."""
    cost = f"{m.cost}-cost" if m.cost is not None else ""
    return (f"{m.base_card_id:<10} {m.name:<28} {m.category:<10} {m.color or '-':<14} "
            f"{cost:<7} own {m.owned}")


def choose_card(conn: sqlite3.Connection, text: str) -> str | None:
    """Turn what was typed - an id in any case, or part of a name - into a card id.

    One match is used as-is, and said out loud so a wrong match is obvious.
    Several are offered as a numbered list (D-037). None means stop; the
    reason has been printed.
    """
    exact = resolve.exact_card(conn, text)
    if exact:
        return exact

    matches = resolve.find_cards(conn, text)
    if len(matches) == 1:
        print(f"{text!r} is {matches[0].base_card_id} {matches[0].name}")
        return matches[0].base_card_id

    if not matches and LOOKS_LIKE_AN_ID.match(text.strip()):
        print(f"error: unknown card {text.strip().upper()!r} - not in the card database. "
              f"If it's from a new set, `optcg sync` may fetch it.", file=sys.stderr)
        return None

    if not matches:
        print(f"error: no card matches {text!r}", file=sys.stderr)
        suggestion = resolve.corrected_query(conn, text)
        if suggestion:
            print(f"  did you mean {suggestion!r}?", file=sys.stderr)
        return None

    if len(matches) > MAX_PICK:
        print(f"{text!r} matches {len(matches)} cards - type more of the name, or the id.")
        for m in matches[:MAX_PICK]:
            print(f"  {describe(m)}")
        print(f"  ... `find {text}` lists all {len(matches)}")
        return None

    print(f"{text!r} matches {len(matches)} cards:")
    for i, m in enumerate(matches, start=1):
        print(f"  [{i}] {describe(m)}")
    while True:
        try:
            answer = input(f"pick 1-{len(matches)}, or c to cancel: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("error: several cards match - use the id instead", file=sys.stderr)
            return None
        if answer in ("c", "cancel", ""):
            print("cancelled - nothing changed.")
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(matches):
            return matches[int(answer) - 1].base_card_id
        print(f"  type a number from 1 to {len(matches)}, or c")


def cmd_find(conn: sqlite3.Connection, args) -> int:
    text = " ".join(args.text)
    exact = resolve.exact_card(conn, text)
    matches = resolve.card_matches(conn, {exact}) if exact else resolve.find_cards(conn, text)
    if not matches:
        print(f"no card matches {text!r}")
        suggestion = resolve.corrected_query(conn, text)
        if suggestion:
            print(f"  did you mean {suggestion!r}?")
        return 1
    for m in matches:
        print(f"  {describe(m)}")
    owned = sum(1 for m in matches if m.owned)
    print(f"\n  {len(matches)} card(s), {owned} of them owned. "
          f"`collection show <id>` shows arts and where they are.")
    return 0


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
    if not args.yes:
        detail = show_deck(conn, args.deck)
        kind = "physical" if detail.is_physical else "wishlist"
        print(f"Deleting {kind} deck {detail.name!r} deletes its recipe "
              f"({detail.total_cards} cards).")
        if detail.sleeved_total:
            print(f"Its {detail.sleeved_total} sleeved copies go to just-loose - "
                  f"you still own them.")
        if not ask_to_continue("delete it?"):
            return 1
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
    if status.unsorted and unowned:
        # Unsorted copies are *some* printing, just not recorded - calling the
        # rest "printings you don't own" would include the one they really are.
        arts = ", ".join(p.printing_variant or "base" for p in unowned)
        places = {h.container for h in status.holdings if h.card_image_id is None}
        where = f'"{places.pop()}"' if len(places) == 1 else "<where they are>"
        print(f"\n  your {status.unsorted} unsorted copies can be recorded as: {arts}")
        print(f"  e.g. collection set-art {status.base_card_id} 1 "
              f"{unowned[0].printing_variant or 'base'} --in {where}")
    elif unowned:
        print(f"\n  {len(unowned)} other printing(s) exist that you don't own")

    print(f"\n  in decks {status.committed}   loose {status.loose}   "
          f"unsorted {status.unsorted}")
    return 0


def cmd_collection_list(conn: sqlite3.Connection, args) -> int:
    """What you own - never a card you don't have (D-039). `browse` is everything."""
    rows = coll.owned_rows(conn, args.set, args.color, args.category, args.search)
    if not rows:
        filtered = any((args.set, args.color, args.category, args.search))
        print("none of your cards match" if filtered else
              "your collection is empty - `deck import` or `collection add` puts cards in")
        return 0

    shown = rows[:args.limit] if args.limit else rows
    print(f"  {'card':<10} {'art':<16} {'own':>3}  {'category':<10} {'color':<12} name")
    for r in shown:
        if r.card_image_id is None:
            art = "(not sorted)"
        elif r.card_image_id == r.base_card_id:
            art = "base"
        else:
            art = r.card_image_id[len(r.base_card_id) + 1:] or r.card_image_id
        print(f"  {r.base_card_id:<10} {art:<16} {r.quantity:>3}  {r.category:<10} "
              f"{r.color or '-':<12} {resolve.display_name(r.name)}")

    copies = sum(r.quantity for r in rows)
    cards = len({r.base_card_id for r in rows})
    print(f"\n  {copies} copies of {cards} card(s)")
    if len(shown) < len(rows):
        print(f"  showing {len(shown)} of {len(rows)} rows - narrow it with a filter, "
              f"or --limit {len(rows)}")
    return 0


def cmd_browse(conn: sqlite3.Connection, args) -> int:
    """Every card in the game, with what you own of each printing (D-039)."""
    rows = coll.list_cards(
        conn, args.set, args.color, args.category, False, args.limit, args.search
    )
    if not rows:
        print("no cards matched")
        return 0

    # One row per printing; 'own' counts copies recorded as that art. Copies
    # whose art isn't sorted yet belong to the card, not to any one printing,
    # so they're noted once, on the card's first row, rather than on every art.
    print(f"  {'printing':<24} {'own':>3}  {'category':<10} {'color':<12} name")
    noted: set[str] = set()
    for card_image_id, name, category, color, owned, card_owned in rows:
        card = card_image_id.split("_")[0]
        tail = ""
        if card_owned > owned and card not in noted:
            noted.add(card)
            unsorted = coll.unsorted_copies(conn, card)
            if unsorted:
                tail = f"  + {unsorted} of this card, art not sorted yet"
        own = str(owned) if owned else "-"
        print(f"  {card_image_id:<24} {own:>3}  {category:<10} {color or '-':<12} {name}{tail}")
    print(f"\n  {len(rows)} printing(s). `collection set-art` records which art you have.")
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
    # just-loose is refused by delete_location itself; asking first would be
    # a question whose answer doesn't matter.
    if not args.yes and args.name.strip() != DEFAULT_LOOSE_LOCATION:
        rows = store.contents(conn, args.name, None, args.kind)
        if rows:  # an empty location loses nothing, so it isn't asked about
            print(f"{args.name!r} holds {sum(r[3] for r in rows)} copies of "
                  f"{len({r[0] for r in rows})} card(s).")
            print("They go to just-loose - you still own them, but not where they were.")
            if not ask_to_continue("delete it?"):
                return 1
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
# backup
# --------------------------------------------------------------------------

def _size(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{n / 1024:.0f} KB"


def cmd_backup_snapshot(conn: sqlite3.Connection, args) -> int:
    path = backup.snapshot(conn, args.label)
    print(f"snapshot saved: {path}")
    print("  manual snapshots are never deleted automatically")
    return 0


def cmd_backup_list(conn: sqlite3.Connection, args) -> int:
    snapshots = backup.list_snapshots(conn)
    if not snapshots:
        print("no snapshots yet - `backup snapshot` makes one")
        return 0
    for s in snapshots:
        kind = "auto" if s.automatic else "manual"
        print(f"  {kind:<6} {_size(s.size_bytes):>8}  {s.path.name}")
    print(f"\n  in {snapshots[0].path.parent}")
    print(f"  the newest {backup.KEEP_AUTO_SNAPSHOTS} auto snapshots are kept; manual ones always")
    print("  to undo, close the app and copy a snapshot over the database file")
    return 0


def cmd_backup_export(conn: sqlite3.Connection, args) -> int:
    path = backup.write_export(conn, args.path)
    counts = backup.current_counts(conn)
    print(f"exported to {path}")
    print(f"  {counts.decks} deck(s), {counts.locations} location(s), {counts.copies} copies")
    return 0


def cmd_backup_import(conn: sqlite3.Connection, args) -> int:
    plan = backup.read_export(args.path)
    when = f", exported {plan.exported_at}" if plan.exported_at else ""
    print(f"{args.path}: {len(plan.decks)} deck(s), {plan.location_count} location(s), "
          f"{plan.copy_total} copies{when}")

    if not args.yes and not args.dry_run:
        now = backup.current_counts(conn)
        print(f"This REPLACES what is here now: {now.decks} deck(s), {now.locations} "
              f"location(s), {now.copies} copies.")
        print("A snapshot is taken first, so it can be undone.")
        if not ask_to_continue("replace?"):
            return 1

    result = backup.import_collection(conn, plan)
    print(f"imported {result.decks} deck(s), {plan.location_count} location(s), "
          f"{result.copies} copies")
    warn(result.warnings)
    return 0


# --------------------------------------------------------------------------
# setup and sync
# --------------------------------------------------------------------------

def cmd_setup(conn: sqlite3.Connection, args) -> int:
    """Everything a fresh clone needs, in one command. Safe to re-run: it never resets."""
    create_schema(conn)
    print("database ready")
    return cmd_sync(conn, args)


def cmd_sync(conn: sqlite3.Connection, args) -> int:
    # Imported here: it pulls in the HTTP client, which no other command needs.
    from optcg.sync import print_summary, run_sync

    # Seeding first means a code update that adds card types needs no extra step.
    added, _ = seed_known_types(conn, SEED_TYPES)
    if added:
        print(f"{added} new card type(s) learned")
    print("downloading cards from OptcgAPI - this takes about a minute...")
    stats, stale = run_sync(conn, use_cache=args.use_cache)
    print_summary(conn, stats, stale)
    if stats["unknown_types"]:
        print(f"\n  {stats['unknown_types']} card(s) have a card type this version doesn't know -")
        print("  a new set probably added some. Update the code and run `optcg sync` again.")
    return 0


# --------------------------------------------------------------------------
# shell
# --------------------------------------------------------------------------

SHELL_BANNER = """optcg shell - type commands without the `optcg` prefix.
  e.g.  find ichiji      collection add cosette 2      deck show "Reiju"
  help shows the guide, help deck shows the deck commands, exit leaves."""


def split_command(line: str) -> list[str] | None:
    r"""Split a shell line like a terminal would. None on an unclosed quote.

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
    typed after `optcg` - same output, same errors, same commits.
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
            argv = [*argv[1:], "--help"]  # "help deck import" -> "deck import --help"

        global _in_shell
        _in_shell = True
        try:
            main(["--db", str(db), *argv])
        except SystemExit:
            # argparse exits after --help and on bad arguments; it has already
            # printed why, and one typo shouldn't end the session.
            pass
        finally:
            _in_shell = False


# --------------------------------------------------------------------------
# help text
# --------------------------------------------------------------------------

# Keeps the line breaks in descriptions and examples instead of re-wrapping them.
HELP_FORMAT = argparse.RawDescriptionHelpFormatter

#: True while the shell runs, so help hints read the way you type them there.
_in_shell = False

_INVALID_CHOICE = re.compile(r"invalid choice: '([^']*)' \(choose from (.*)\)")

#: Aliases for `browse` that never appear in help, error messages or
#: suggestions (D-039). Poneglyphs record the world's whole history; a Log Pose
#: finds your way across it.
HIDDEN_COMMANDS = ("poneglyph", "logpose")


class Parser(argparse.ArgumentParser):
    """argparse, except a mistake shows that command's examples and where more help is.

    Sub-parsers inherit the class, so every command gets this.
    """

    def error(self, message: str):
        typo = _INVALID_CHOICE.search(message)
        choices = []
        if typo:
            choices = [c for c in re.findall(r"'([^']*)'", typo.group(2))
                       if c not in HIDDEN_COMMANDS]
            listed = ", ".join(f"'{c}'" for c in choices)
            message = message.replace(typo.group(2), listed)
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        if typo:
            close = difflib.get_close_matches(typo.group(1), choices, n=1)
            if close:
                print(f"  did you mean {close[0]!r}?", file=sys.stderr)
        if self.epilog:
            print(f"\n{self.epilog}", file=sys.stderr)
        command = self.prog.removeprefix("optcg").strip()
        if _in_shell:
            more = f"help {command}".strip()
        else:
            more = f"optcg {command} --help".replace("  ", " ")
        print(f"\nmore help: {more}", file=sys.stderr)
        self.exit(2)

GUIDE = """\
One Piece TCG collection and deck manager.

how it fits together
  card        the id printed on the card, e.g. OP06-042 - or part of its name.
              A name several cards share gives you a numbered list to pick
              from; `find <name>` just lists them.
  art        which printing a copy is: 'base', or a variant like p1. Optional -
              copies start unsorted, and you can say which art later.
  container   where copies physically are: a deck, or a location you create
              (storage box, binder, loan). Cards with nowhere named go to
              'just-loose'.

getting started
  deck import decklists\\Reiju_deck.txt --name "Reiju"
      a sleeved deck - its cards join your collection, sitting in the deck
  collection add OP06-060 2
      loose cards you own
  location add "Binder A" --kind binder
  collection move OP06-060 2 --to "Binder A"
  collection show OP06-060
      how many you own, which arts, and where they are
  find ichiji
      every card with that in its name, and how many you own

more help
  help                    this page
  help deck               the deck commands
  help deck import        one command, with examples

The examples are written the way you type them in the shell, which
'optcg' on its own opens. Outside it, put 'optcg' in front:
  optcg deck show "Reiju"
"""

ID_HELP = "card id (OP06-042) or part of its name (ichiji, \"germa 66\")"
NAME_HELP = "put it in quotes if it has spaces"


def dry_run_option(p) -> None:
    """Only on the imports (D-034): where you want to see the result before it lands."""
    p.add_argument("--dry-run", action="store_true",
                   help="show what would happen, then throw it away - nothing is saved")


def _command(sub, name: str, summary: str, *examples: str, description: str | None = None):
    """Add a subcommand whose --help says what it is for and shows it in use."""
    epilog = None
    if examples:
        epilog = "examples:\n" + "\n".join(f"  {line}" for line in examples)
    return sub.add_parser(
        name,
        help=summary,
        description=description or summary,
        epilog=epilog,
        formatter_class=HELP_FORMAT,
    )


def _group(sub, name: str, summary: str, description: str, *examples: str):
    """Add a command group (deck, collection, ...) and return its subcommands."""
    parser = _command(sub, name, summary, *examples, description=description)
    return parser.add_subparsers(
        dest="action", required=True, title="commands", metavar="<command>"
    )


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = Parser(
        prog="optcg",
        description=GUIDE,
        formatter_class=HELP_FORMAT,
    )
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite file (default: {DB_PATH})")
    # Not required: a bare `optcg` opens the shell.
    sub = parser.add_subparsers(
        dest="group", required=False, title="command groups", metavar="<group>"
    )

    # ---- deck -----------------------------------------------------------
    deck = _group(
        sub, "deck", "import and manage decks",
        "Decks are lists of cards. A physical deck is one you have sleeved: importing\n"
        "it adds its cards to your collection, located in the deck. A wishlist deck\n"
        "is one you want to build: it only tells you what you're missing.",
        'deck import decklists\\Reiju_deck.txt --name "Reiju"',
        'deck show "Reiju"',
        "deck list",
    )

    def deck_options(p) -> None:
        p.add_argument("--physical", action="store_true", help="a deck you have sleeved (default)")
        p.add_argument("--wishlist", action="store_true",
                       help="a deck you want to build - your collection is untouched")
        p.add_argument("--on-conflict", choices=["replace", "new"],
                       help="if the name is taken: replace the old deck, or keep both. "
                            "Without this you are asked")
        dry_run_option(p)

    p = _command(
        deck, "import", "import a decklist from a text file",
        'deck import decklists\\Reiju_deck.txt --name "Reiju"',
        "deck import decklists\\Enel.txt --wishlist",
        'deck import decklists\\Enel.txt --name "Enel" --dry-run',
        'deck import decklists\\Reiju_v2.txt --name "Reiju" --on-conflict replace',
        description="Import a decklist from a text file. Both of these formats work:\n\n"
                    "  Leader: 1 Vinsmoke Reiju OP06-042      Leader\n"
                    "  Character:                             1 Vinsmoke Reiju (OP06-042)\n"
                    "  4 Vinsmoke Ichiji OP06-060             Character (40)\n"
                    "                                         4 Vinsmoke Ichiji (OP06-060)",
    )
    p.add_argument("path", help="the decklist file, e.g. decklists\\Reiju_deck.txt")
    p.add_argument("--name", help="deck name (default: the file name)")
    deck_options(p)
    p.set_defaults(func=cmd_deck_import)

    p = _command(
        deck, "paste", "type or paste a decklist instead of using a file",
        'deck paste --name "Reiju"',
        'deck paste --name "Enel" --wishlist',
        description="Type or paste a decklist straight into the terminal, then type\n"
                    f"'{PASTE_END}' on its own line to import it. Blank lines are fine.",
    )
    p.add_argument("--name", required=True, help=f"deck name - {NAME_HELP}")
    deck_options(p)
    p.set_defaults(func=cmd_deck_paste)

    p = _command(deck, "list", "list all decks", "deck list")
    p.set_defaults(func=cmd_deck_list)

    p = _command(
        deck, "show", "show a deck: what you own and what you're missing",
        'deck show "Reiju"',
        "deck show 1",
    )
    p.add_argument("deck", help="deck name or id (the number in `deck list`)")
    p.set_defaults(func=cmd_deck_show)

    p = _command(
        deck, "delete", "delete a deck - its copies go back to just-loose",
        'deck delete "Reiju"',
        'deck delete "Reiju" --yes',
        description="Delete a deck. You still own its cards: the copies that were\n"
                    "sleeved in it move to just-loose. You are asked first, and the\n"
                    "database is snapshotted to data/backups/ before anything changes.",
    )
    p.add_argument("deck", help="deck name or id")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=cmd_deck_delete)

    p = _command(
        deck, "set-physical", "mark a deck as sleeved, or back to a wishlist",
        'deck set-physical "Enel"',
        'deck set-physical "Enel" --off',
    )
    p.add_argument("deck", help="deck name or id")
    p.add_argument("--off", action="store_true", help="make it a wishlist deck instead")
    p.set_defaults(func=cmd_deck_set_physical)

    # ---- collection -----------------------------------------------------
    col = _group(
        sub, "collection", "track what you own",
        "Your collection is every copy you own. Each copy has a card id, an art\n"
        "(optional - copies start unsorted) and a container it sits in.",
        "collection add OP06-060 2",
        "collection show OP06-060",
        "collection list",
    )

    container_help = f"one of: {', '.join(store.CONTAINER_KINDS)}"
    kind_note = "only needed when two containers share a name"
    art_help = "printing: a full id (OP06-042_p1), a variant (p1), or 'base'"

    p = _command(
        col, "add", "record copies you now own",
        "collection add OP06-060 2",
        'collection add OP06-042 1 --art p1 --to "Binder A"',
        description="Record copies you now own. They go to just-loose unless you name\n"
                    "a container with --to.",
    )
    p.add_argument("base_card_id", help=ID_HELP)
    p.add_argument("quantity", type=int)
    p.add_argument("--art", default=None, help=art_help + "; leave out if you haven't sorted them")
    p.add_argument("--to", default=None, help="container (default: just-loose)")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"{container_help} - {kind_note}")
    p.set_defaults(func=cmd_collection_add)

    p = _command(
        col, "remove", "copies leaving your collection - sold, traded, lost",
        "collection remove OP06-060 1",
        'collection remove OP06-042 1 --art p1 --from "Binder A"',
    )
    p.add_argument("base_card_id", help=ID_HELP)
    p.add_argument("quantity", type=int)
    p.add_argument("--art", default=None, help=art_help)
    p.add_argument("--from", default=None, help="container (default: just-loose)")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"{container_help} - {kind_note}")
    p.set_defaults(func=cmd_collection_remove)

    p = _command(
        col, "move", "move copies from one container to another",
        'collection move OP06-060 2 --to "Binder A"',
        'collection move OP06-060 2 --from "Binder A" --to just-loose',
        'collection move OP06-060 4 --from "Reiju" --to "Binder A"',
        description="Move copies between containers. Moving more than the source holds is\n"
                    "an error, and the message says what is actually there.",
    )
    p.add_argument("base_card_id", help=ID_HELP)
    p.add_argument("quantity", type=int)
    p.add_argument("--from", default=None, help="source (default: just-loose)")
    p.add_argument("--to", required=True, help="destination location or deck")
    p.add_argument("--art", default=None, help=art_help)
    p.add_argument("--from-kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"{container_help} - {kind_note}")
    p.add_argument("--to-kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"{container_help} - {kind_note}")
    p.set_defaults(func=cmd_collection_move)

    p = _command(
        col, "set-art", "say which printing some copies are",
        'collection set-art OP06-042 1 p1 --in "Reiju"',
        'collection set-art OP06-060 2 base --in "Binder A"',
        'collection set-art OP06-042 1 base --from-art p1 --in "Reiju"',
        description="Say which printing some of your copies are. Use it where the cards sit:\n"
                    "name the container with --in. `collection show <card id>` lists the\n"
                    "arts that exist.",
    )
    p.add_argument("base_card_id", help=ID_HELP)
    p.add_argument("quantity", type=int)
    p.add_argument("art", help=art_help)
    p.add_argument("--in", dest="location", default=None,
                   help="container holding them (default: just-loose)")
    p.add_argument("--from-art", default=None,
                   help="fix copies already recorded as this art")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"{container_help} - {kind_note}")
    p.set_defaults(func=cmd_collection_set_art)

    p = _command(
        col, "show", "one card: how many you own, which arts, where they are",
        "collection show OP06-042",
    )
    p.add_argument("base_card_id", help=ID_HELP)
    p.set_defaults(func=cmd_collection_show)

    def card_filters(p, limit: int, limit_help: str) -> None:
        p.add_argument("--set", help="set code, e.g. OP06")
        p.add_argument("--color", help="e.g. Red, Blue")
        p.add_argument("--category", help="Leader, Character, Event or Stage")
        p.add_argument("--search", default=None, help="part of a card name")
        p.add_argument("--limit", type=int, default=limit, help=limit_help)

    p = _command(
        col, "list", "what you own: every card and art, with how many",
        "collection list",
        "collection list --set OP06",
        "collection list --search ichiji",
        "collection list --color Purple --category Character",
        description="Everything you own, one row per card and art. Copies whose art you\n"
                    "haven't recorded yet show as '(not sorted)'. Filters combine. To see\n"
                    "cards you don't own, use `browse`.",
    )
    card_filters(p, 100, "most rows to show (default: 100)")
    p.set_defaults(func=cmd_collection_list)

    # ---- browse ---------------------------------------------------------
    # Every card in the game, owned or not (D-039). 'poneglyph' and 'logpose'
    # are hidden aliases - an easter egg the owner asked for. They are added
    # without help text, so they never appear in a command list.
    browse_description = (
        "Every card in the game, one row per printing, with how many you own of\n"
        "each. Filters combine. `collection list` shows only what you own."
    )
    p = _command(
        sub, "browse", "every card in the game, owned or not",
        "browse --set OP17",
        "browse --color Purple --category Leader",
        "browse --search luffy --limit 200",
        description=browse_description,
    )
    card_filters(p, 50, "most rows to show (default: 50)")
    p.set_defaults(func=cmd_browse)
    for name in HIDDEN_COMMANDS:
        p = sub.add_parser(name, description=browse_description, formatter_class=HELP_FORMAT)
        card_filters(p, 50, "most rows to show (default: 50)")
        p.set_defaults(func=cmd_browse)

    # ---- location -------------------------------------------------------
    loc = _group(
        sub, "location", "where cards are stored: boxes, binders, loans",
        "Locations are places your copies sit outside a deck. Each has a kind:\n"
        "storage, binder, loose or loan. 'just-loose' always exists.",
        'location add "Binder A" --kind binder',
        "location list",
        'location show "Binder A"',
    )

    # A bare name can match more than one kind, so every command that resolves
    # a location takes --kind to disambiguate.
    kind_help = f"one of: {', '.join(store.LOCATION_KINDS)}"

    p = _command(
        loc, "add", "create a location",
        'location add "Binder A" --kind binder',
        'location add "OP06 Box" --kind storage --notes "bulk"',
        'location add "Marco" --kind loan',
    )
    p.add_argument("name", help=NAME_HELP)
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default="storage",
                   help=f"{kind_help} (default: storage)")
    p.add_argument("--notes", help="anything you want to remember about it")
    p.set_defaults(func=cmd_location_add)

    p = _command(
        loc, "list", "list locations, grouped by kind",
        "location list",
        "location list --kind binder",
    )
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default=None, help=f"only this kind: {kind_help}")
    p.set_defaults(func=cmd_location_list)

    p = _command(
        loc, "rename", "rename a location",
        'location rename "OP06 Box" "Germa Box"',
    )
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default=None,
                   help=f"{kind_help} - {kind_note}")
    p.set_defaults(func=cmd_location_rename)

    p = _command(
        loc, "delete", "delete a location - its copies go back to just-loose",
        'location delete "OP06 Box"',
        'location delete "OP06 Box" --yes',
        description="Delete a location. Its copies move to just-loose. If it holds any,\n"
                    "you are asked first; the database is snapshotted either way.",
    )
    p.add_argument("name")
    p.add_argument("--kind", choices=store.LOCATION_KINDS, default=None,
                   help=f"{kind_help} - {kind_note}")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=cmd_location_delete)

    p = _command(
        loc, "show", "what's inside a location or deck",
        'location show "Binder A"',
        'location show "Reiju"',
        "location show",
    )
    p.add_argument("name", nargs="?", default=None, help="default: just-loose")
    p.add_argument("--kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"one of: {', '.join(store.CONTAINER_KINDS)}")
    p.add_argument("--limit", type=int, default=None, help="most rows to show")
    p.set_defaults(func=cmd_location_show)

    # ---- loans ----------------------------------------------------------
    p = _command(
        sub, "loan", "lend copies to someone - you still own them",
        'location add "Marco" --kind loan',
        'loan OP06-042 1 "Marco"',
        'collection move OP06-042 1 --from "Marco" --to just-loose',
        description="Lend copies to someone. They stay in your collection, located with\n"
                    "that person. Create the person first as a location of kind 'loan';\n"
                    "when the cards come back, move them out again.",
    )
    p.add_argument("base_card_id", help=ID_HELP)
    p.add_argument("quantity", type=int)
    p.add_argument("to_whom", help="a location of kind 'loan'")
    p.add_argument("--art", default=None, help="which printing is going out")
    p.add_argument("--from", default=None, help="source (default: just-loose)")
    p.add_argument("--from-kind", choices=store.CONTAINER_KINDS, default=None,
                   help=f"{container_help} - {kind_note}")
    p.set_defaults(func=cmd_loan)

    # ---- backup ---------------------------------------------------------
    bak = _group(
        sub, "backup", "snapshots for undo, export/import to move machines",
        "Two kinds of backup. A snapshot is a full copy of the database, kept in\n"
        "data/backups/ - deleting a deck or location, replacing a deck, importing\n"
        f"and --reset all take one first (the newest {backup.KEEP_AUTO_SNAPSHOTS} are kept). "
        "An export is a\n"
        "JSON file of your decks, locations and copies only: it survives code\n"
        "updates and loads on another machine after a sync.",
        "backup export",
        "backup import my_collection.json",
        "backup snapshot --label before-sorting",
    )

    p = _command(
        bak, "snapshot", "save a full copy of the database now",
        "backup snapshot",
        "backup snapshot --label before-sorting",
        description="Save a full copy of the database to data/backups/. Snapshots you\n"
                    "make yourself are never deleted automatically.",
    )
    p.add_argument("--label", default=None, help="a word or two added to the file name")
    p.set_defaults(func=cmd_backup_snapshot)

    p = _command(bak, "list", "list the snapshots in data/backups/", "backup list")
    p.set_defaults(func=cmd_backup_list)

    p = _command(
        bak, "export", "write your decks, locations and copies to a JSON file",
        "backup export",
        "backup export E:\\my_collection.json",
        description="Write your decks, locations and copies to a JSON file - what you\n"
                    "own and where it is, not the card database (sync rebuilds that).",
    )
    p.add_argument("path", nargs="?", default=None,
                   help="where to write it (default: a dated file in data/backups/)")
    p.set_defaults(func=cmd_backup_export)

    p = _command(
        bak, "import", "replace your collection with one from an export",
        "backup import my_collection.json",
        "backup import my_collection.json --dry-run",
        "backup import my_collection.json --yes",
        description="Load an export, REPLACING the decks, locations and copies here.\n"
                    "Nothing is merged. A snapshot is taken first, and you are asked to\n"
                    "confirm unless you pass --yes. Run the sync first on a new machine:\n"
                    "arts can only be kept for printings the card database knows.",
    )
    p.add_argument("path", help="a file written by `backup export`")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    dry_run_option(p)
    p.set_defaults(func=cmd_backup_import)

    # ---- find -----------------------------------------------------------
    p = _command(
        sub, "find", "look a card up by name: its id, what it is, how many you own",
        "find ichiji",
        "find monkey d luffy",
        "find germa 66",
        description="List every card whose name contains all the words you type. Capitals\n"
                    "and dots don't matter: 'monkey d luffy' finds Monkey.D.Luffy. No quotes\n"
                    "needed.",
    )
    p.add_argument("text", nargs="+", help="part of a card name, or an id")
    p.set_defaults(func=cmd_find)

    # ---- setup and sync -------------------------------------------------
    p = _command(
        sub, "setup", "first-time setup: create the database and download every card",
        "setup",
        description="Create the database and download every card from OptcgAPI. Run it\n"
                    "once after installing. Safe to run again - it never deletes anything.",
    )
    p.add_argument("--use-cache", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_setup)

    p = _command(
        sub, "sync", "download the latest cards - run it when a new set is out",
        "sync",
        description="Download the latest cards from OptcgAPI. Your collection and decks\n"
                    "are never touched, and no card is ever deleted (D-035).",
    )
    p.add_argument("--use-cache", action="store_true",
                   help="reuse the last download in data/cache/ instead of the API")
    p.set_defaults(func=cmd_sync)

    # ---- shell ----------------------------------------------------------
    _command(
        sub, "shell", "type commands one after another, without the prefix",
        "shell",
        description="Open an optcg> prompt. Type commands without 'optcg' in front;\n"
                    "'help' shows the guide, 'exit' leaves. Running 'optcg' on its own\n"
                    "does the same.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    args = build_parser().parse_args(argv)

    if getattr(args, "physical", False) and getattr(args, "wishlist", False):
        print("error: --physical and --wishlist are mutually exclusive", file=sys.stderr)
        return 2

    if args.group in (None, "shell"):
        return run_shell(args.db)

    if getattr(args, "dry_run", False):
        return run_dry(args)
    return run_command(args)


def run_dry(args) -> int:
    """Run a command against a throwaway copy of the database (D-034).

    A copy rather than a transaction rolled back at the end: the modules
    commit as they go, so a rollback would undo nothing.
    """
    # ignore_cleanup_errors: on Windows a file still being released can make
    # removing the temp folder fail, and that must not fail the command.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        copy = Path(tmp) / args.db.name
        source = connect(args.db)
        dest = sqlite3.connect(copy)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        args.db = copy
        code = run_command(args)
    print("(dry run - nothing was saved)")
    return code


def not_ready(conn: sqlite3.Connection, args) -> str | None:
    """Why this command can't run on this database yet, or None.

    Without this a fresh clone that skipped setup gets a Python traceback
    ('no such table: decks') instead of being told what to run.
    """
    if args.func in (cmd_setup, cmd_sync):
        return None
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"cards", "copies", "decks", "storage_locations"} <= tables:
        return "the database isn't set up yet - run `optcg setup` first"
    needs_cards = getattr(args, "base_card_id", None) or args.func in (
        cmd_find, cmd_deck_import, cmd_deck_paste, cmd_browse,
    )
    if needs_cards and conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is None:
        return "no cards downloaded yet - run `optcg sync`"
    return None


def run_command(args) -> int:
    conn = connect(args.db)
    try:
        problem = not_ready(conn, args)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 1
        # Every command that takes a card takes it as `base_card_id`, so a name
        # is resolved here once rather than in each command (D-037).
        if getattr(args, "base_card_id", None):
            chosen = choose_card(conn, args.base_card_id)
            if chosen is None:
                return 1
            args.base_card_id = chosen
        return args.func(conn, args)
    except NotThere as exc:
        print(f"error: {exc}", file=sys.stderr)
        # One other place holds them: say exactly what to add. With several,
        # the message above lists them and the choice is the owner's.
        places = {(h.container, h.kind) for h in exc.elsewhere}
        if len(places) == 1:
            name, _ = places.pop()
            flag = "--in" if args.func is cmd_collection_set_art else "--from"
            print(f'  add {flag} "{name}" to use those', file=sys.stderr)
        return 1
    except (CollectionError, StorageError, DeckError, BackupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled.", file=sys.stderr)
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
