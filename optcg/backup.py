"""Backups of the owner's data — snapshots for undo, a JSON export for moving (D-032).

Two formats, because they answer different questions:

- A **snapshot** is a byte-for-byte copy of the SQLite file, written next to it
  in `backups/`. Restoring one is copying it back over `optcg.db`. Commands that
  destroy data take one automatically first; only the newest
  `KEEP_AUTO_SNAPSHOTS` automatic ones are kept. Manual snapshots are never
  pruned.
- The **export** is JSON holding only what can't be re-downloaded: locations,
  decks and copies. It names cards by their printed ids, never by internal row
  ids, so it outlives schema changes and loads on another machine after a sync.
  Importing it *replaces* the owner's data rather than merging into it.

The card catalog is left out of the export on purpose — `sync` rebuilds it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from optcg.db_setup import DEFAULT_LOOSE_LOCATION
from optcg.storage import DECK_KIND, normalize_type

EXPORT_FORMAT = "optcg-collection"
EXPORT_VERSION = 1

BACKUP_DIR_NAME = "backups"
AUTO_PREFIX = "auto-"
MANUAL_PREFIX = "optcg-"
KEEP_AUTO_SNAPSHOTS = 10


class BackupError(Exception):
    """A backup, export or import was refused. Carries a message fit to print."""


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------

class Snapshot(NamedTuple):
    path: Path
    automatic: bool
    size_bytes: int


def db_file(conn: sqlite3.Connection) -> Path | None:
    """The file behind a connection, or None for an in-memory database."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def backup_dir(conn: sqlite3.Connection) -> Path:
    path = db_file(conn)
    if path is None:
        raise BackupError("an in-memory database has no file to back up")
    return path.parent / BACKUP_DIR_NAME


def has_user_data(conn: sqlite3.Connection) -> bool:
    """True once anything exists that `sync` couldn't rebuild.

    The built-in loose pile doesn't count: every database has it.
    """
    try:
        return bool(
            conn.execute("SELECT 1 FROM copies LIMIT 1").fetchone()
            or conn.execute("SELECT 1 FROM decks LIMIT 1").fetchone()
            or conn.execute(
                "SELECT 1 FROM storage_locations WHERE NOT (name = ? AND kind = 'loose') LIMIT 1",
                (DEFAULT_LOOSE_LOCATION,),
            ).fetchone()
        )
    except sqlite3.OperationalError:
        return False  # tables not created yet


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")


def _free_path(directory: Path, stem: str, suffix: str) -> Path:
    """`stem + suffix` in `directory`, numbered if two land in the same second."""
    path = directory / f"{stem}{suffix}"
    n = 1
    while path.exists():
        n += 1
        path = directory / f"{stem}-{n}{suffix}"
    return path


def snapshot(conn: sqlite3.Connection, label: str | None = None, automatic: bool = False) -> Path:
    """Copy the whole database into `backups/` and return the new file.

    Uses SQLite's backup API rather than a file copy, so the result is
    consistent even with this connection open.
    """
    directory = backup_dir(conn)
    directory.mkdir(parents=True, exist_ok=True)

    prefix = AUTO_PREFIX if automatic else MANUAL_PREFIX
    stem = prefix + datetime.now().strftime("%Y%m%d-%H%M%S")
    if label and _slug(label):
        stem += "-" + _slug(label)
    path = _free_path(directory, stem, ".db")

    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    except sqlite3.Error as exc:
        dest.close()
        path.unlink(missing_ok=True)
        raise BackupError(f"could not write snapshot {path}: {exc}") from exc
    dest.close()

    if automatic:
        prune_auto_snapshots(directory)
    return path


def auto_snapshot(conn: sqlite3.Connection, reason: str) -> Path | None:
    """Snapshot before a destructive command. None when there's nothing to lose.

    Skipped for an in-memory database and for one holding only the card
    catalog — that is regenerable, and empty snapshots would push out real ones.
    """
    if db_file(conn) is None or not has_user_data(conn):
        return None
    return snapshot(conn, reason, automatic=True)


def prune_auto_snapshots(directory: Path, keep: int = KEEP_AUTO_SNAPSHOTS) -> list[Path]:
    """Delete all but the newest `keep` automatic snapshots. Manual ones are never touched."""
    autos = sorted(directory.glob(f"{AUTO_PREFIX}*.db"))
    doomed = autos[:-keep] if keep > 0 else autos
    for path in doomed:
        path.unlink()
    return doomed


def list_snapshots(conn: sqlite3.Connection) -> list[Snapshot]:
    """Every snapshot in `backups/`, oldest first."""
    directory = backup_dir(conn)
    if not directory.is_dir():
        return []
    return [
        Snapshot(p, p.name.startswith(AUTO_PREFIX), p.stat().st_size)
        for p in sorted(directory.glob("*.db"), key=lambda p: (p.stat().st_mtime, p.name))
    ]


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def _card_names(conn: sqlite3.Connection) -> dict[str, str]:
    """base_card_id -> name, preferring the base printing's spelling."""
    names: dict[str, str] = {}
    # Base printings sort last, so their name is the one that sticks.
    for base_card_id, name in conn.execute(
        "SELECT base_card_id, name FROM cards ORDER BY (card_image_id = base_card_id)"
    ):
        names[base_card_id] = name
    return names


def _copies_where(conn, column: str, value: int, names: dict[str, str]) -> list[dict]:
    return [
        {"card": base, "art": art, "quantity": qty, "name": names.get(base)}
        for base, art, qty in conn.execute(
            f"SELECT base_card_id, card_image_id, quantity FROM copies WHERE {column} = ? "
            "ORDER BY base_card_id, COALESCE(card_image_id, '')",
            (value,),
        )
    ]


def export_collection(conn: sqlite3.Connection) -> dict:
    """The owner's data as a plain dict, ready for `json.dump`.

    Copies are nested inside the container that holds them, so the file reads
    like the shelf does and needs no ids to tie the two together. `name` fields
    are for a human reading the file; import ignores them.
    """
    names = _card_names(conn)

    locations = [
        {
            "name": name,
            "kind": kind,
            "notes": notes,
            "created_at": created_at,
            "copies": _copies_where(conn, "location_id", loc_id, names),
        }
        for loc_id, name, kind, notes, created_at in conn.execute(
            "SELECT id, name, kind, notes, created_at FROM storage_locations ORDER BY kind, name"
        )
    ]

    decks = [
        {
            "name": name,
            "leader": leader_id,
            "physical": bool(is_physical),
            "notes": notes,
            "created_at": created_at,
            "recipe": [
                {"card": base, "quantity": qty, "name": names.get(base)}
                for base, qty in conn.execute(
                    "SELECT base_card_id, quantity FROM deck_cards WHERE deck_id = ? ORDER BY id",
                    (deck_id,),
                )
            ],
            "copies": _copies_where(conn, "deck_id", deck_id, names),
        }
        for deck_id, name, leader_id, is_physical, notes, created_at in conn.execute(
            "SELECT id, name, leader_id, is_physical, notes, created_at FROM decks ORDER BY id"
        )
    ]

    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        # Optional on import: exports from before D-040 don't have it.
        "storage_types": [name for (name,) in conn.execute(
            "SELECT name FROM storage_types WHERE NOT builtin ORDER BY name")],
        "locations": locations,
        "decks": decks,
    }


def write_export(conn: sqlite3.Connection, path: str | Path | None = None) -> Path:
    """Write the export to `path`, or to a timestamped file in `backups/`."""
    if path is None:
        directory = backup_dir(conn)
        directory.mkdir(parents=True, exist_ok=True)
        target = _free_path(directory, "collection-" + datetime.now().strftime("%Y%m%d-%H%M%S"), ".json")
    else:
        target = Path(path)
    data = export_collection(conn)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        raise BackupError(f"could not write {target}: {exc}") from exc
    return target


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

class CopyEntry(NamedTuple):
    card: str
    art: str | None
    quantity: int


class LocationEntry(NamedTuple):
    name: str
    kind: str
    notes: str | None
    created_at: str | None
    copies: list[CopyEntry]


class DeckEntry(NamedTuple):
    name: str
    leader: str | None
    physical: bool
    notes: str | None
    created_at: str | None
    recipe: list[tuple[str, int]]
    copies: list[CopyEntry]


class ExportPlan(NamedTuple):
    """A validated export, not yet written anywhere."""
    exported_at: str | None
    locations: list[LocationEntry]
    decks: list[DeckEntry]
    storage_types: tuple[str, ...] = ()

    @property
    def location_count(self) -> int:
        """Locations other than the built-in loose pile, which every database has."""
        return sum(1 for loc in self.locations
                   if not (loc.name == DEFAULT_LOOSE_LOCATION and loc.kind == "loose"))

    @property
    def copy_total(self) -> int:
        return sum(c.quantity for holder in (*self.locations, *self.decks) for c in holder.copies)


class ImportSummary(NamedTuple):
    locations: int
    decks: int
    copies: int
    warnings: list[str]


class DataCounts(NamedTuple):
    locations: int  # not counting the built-in loose pile
    decks: int
    copies: int


def current_counts(conn: sqlite3.Connection) -> DataCounts:
    """What an import would replace, for the confirmation prompt."""
    return DataCounts(
        conn.execute(
            "SELECT COUNT(*) FROM storage_locations WHERE NOT (name = ? AND kind = 'loose')",
            (DEFAULT_LOOSE_LOCATION,),
        ).fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0],
        conn.execute("SELECT COALESCE(SUM(quantity), 0) FROM copies").fetchone()[0],
    )


def _text(value, where: str, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise BackupError(f"{where}: expected text, got {value!r}")
    return value.strip() if required else value


def _quantity(value, where: str) -> int:
    # bool is an int subclass; `true` is not a quantity.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BackupError(f"{where}: quantity must be a whole number above 0, got {value!r}")
    return value


def _list(value, where: str) -> list:
    if not isinstance(value, list):
        raise BackupError(f"{where}: expected a list")
    return value


def _object(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise BackupError(f"{where}: expected an object")
    return value


def _parse_copies(raw, where: str) -> list[CopyEntry]:
    copies = []
    for i, item in enumerate(_list(raw if raw is not None else [], where)):
        at = f"{where}[{i}]"
        item = _object(item, at)
        copies.append(CopyEntry(
            _text(item.get("card"), f"{at}.card"),
            _text(item.get("art"), f"{at}.art", required=False),
            _quantity(item.get("quantity"), f"{at}.quantity"),
        ))
    return copies


def parse_export(data) -> ExportPlan:
    """Check an export's shape and return it as a plan. Raises BackupError.

    Validation happens entirely before anything is deleted, so a damaged file
    is refused with the owner's current data untouched.
    """
    data = _object(data, "file")
    if data.get("format") != EXPORT_FORMAT:
        raise BackupError(f"not a collection export (expected format {EXPORT_FORMAT!r})")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BackupError(f"export has no valid version number: {version!r}")
    if version > EXPORT_VERSION:
        raise BackupError(
            f"export is format version {version}, this code reads up to {EXPORT_VERSION} - "
            f"update the code first"
        )

    locations = []
    seen_locations = set()
    for i, item in enumerate(_list(data.get("locations", []), "locations")):
        at = f"locations[{i}]"
        item = _object(item, at)
        name = _text(item.get("name"), f"{at}.name")
        # Any type name is fine: custom types travel with the data (D-040).
        kind = normalize_type(_text(item.get("kind"), f"{at}.kind"))
        if kind == DECK_KIND:
            raise BackupError(f"{at}.kind: 'deck' is not a storage type")
        if (name, kind) in seen_locations:
            raise BackupError(f"{at}: {kind} {name!r} appears twice")
        seen_locations.add((name, kind))
        locations.append(LocationEntry(
            name, kind,
            _text(item.get("notes"), f"{at}.notes", required=False),
            _text(item.get("created_at"), f"{at}.created_at", required=False),
            _parse_copies(item.get("copies"), f"{at}.copies"),
        ))

    decks = []
    for i, item in enumerate(_list(data.get("decks", []), "decks")):
        at = f"decks[{i}]"
        item = _object(item, at)
        physical = item.get("physical", False)
        if not isinstance(physical, bool):
            raise BackupError(f"{at}.physical: expected true or false, got {physical!r}")
        recipe = []
        seen_cards = set()
        for j, line in enumerate(_list(item.get("recipe", []), f"{at}.recipe")):
            line_at = f"{at}.recipe[{j}]"
            line = _object(line, line_at)
            card = _text(line.get("card"), f"{line_at}.card")
            if card in seen_cards:
                raise BackupError(f"{line_at}: {card} appears twice in the recipe")
            seen_cards.add(card)
            recipe.append((card, _quantity(line.get("quantity"), f"{line_at}.quantity")))
        decks.append(DeckEntry(
            _text(item.get("name"), f"{at}.name"),
            _text(item.get("leader"), f"{at}.leader", required=False),
            physical,
            _text(item.get("notes"), f"{at}.notes", required=False),
            _text(item.get("created_at"), f"{at}.created_at", required=False),
            recipe,
            _parse_copies(item.get("copies"), f"{at}.copies"),
        ))

    types = []
    for i, name in enumerate(_list(data.get("storage_types", []), "storage_types")):
        clean = normalize_type(_text(name, f"storage_types[{i}]"))
        if clean == DECK_KIND:
            raise BackupError(f"storage_types[{i}]: 'deck' is not a storage type")
        types.append(clean)

    exported_at = data.get("exported_at")
    return ExportPlan(exported_at if isinstance(exported_at, str) else None, locations, decks,
                      tuple(types))


def read_export(path: str | Path) -> ExportPlan:
    file_path = Path(path)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BackupError(f"could not read {file_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"{file_path} is not valid JSON: {exc}") from exc
    return parse_export(data)


def _merge_copies(
    copies: list[CopyEntry], printings: dict[str, str], where: str, warnings: list[str]
) -> dict[tuple[str, str | None], int]:
    """Collapse copies to one quantity per (card, art), as the schema requires.

    An art the catalog doesn't know, or one belonging to a different card,
    becomes unsorted with a warning: the copies are kept, only the detail that
    can't be honoured is dropped.
    """
    merged: dict[tuple[str, str | None], int] = {}
    for c in copies:
        art = c.art
        if art is not None and printings.get(art) != c.card:
            reason = "not in the card catalog" if art not in printings else f"is not a printing of {c.card}"
            warnings.append(f"{where}: {c.quantity}x {c.card} art {art} {reason} - kept as unsorted")
            art = None
        merged[(c.card, art)] = merged.get((c.card, art), 0) + c.quantity
    return merged


def import_collection(conn: sqlite3.Connection, plan: ExportPlan) -> ImportSummary:
    """Replace the owner's locations, decks and copies with `plan`.

    Takes an automatic snapshot first. The card catalog must already be
    synced: an art can only be kept if its printing exists. Card ids the
    catalog doesn't know are kept anyway, with a warning — they are the
    owner's data, and the API lagging the game is not a reason to lose them.

    Everything lands in one transaction, or nothing does.
    """
    if conn.execute("SELECT 1 FROM cards LIMIT 1").fetchone() is None:
        raise BackupError("the card catalog is empty - run the sync first, then import")

    printings = dict(conn.execute("SELECT card_image_id, base_card_id FROM cards"))
    known_cards = set(printings.values())
    warnings: list[str] = []

    referenced = {c.card for h in (*plan.locations, *plan.decks) for c in h.copies}
    referenced |= {card for d in plan.decks for card, _ in d.recipe}
    unknown = sorted(referenced - known_cards)
    if unknown:
        warnings.append(
            f"{len(unknown)} card id(s) not in the card catalog, kept anyway: {', '.join(unknown)}"
        )

    auto_snapshot(conn, "before-import")

    copy_total = 0
    try:
        conn.execute("DELETE FROM copies")
        conn.execute("DELETE FROM deck_cards")
        conn.execute("DELETE FROM decks")
        conn.execute("DELETE FROM storage_locations")
        # Types are added, never removed: a type the owner made here survives
        # an import, and every type the file uses exists before it's needed.
        conn.executemany(
            "INSERT OR IGNORE INTO storage_types (name) VALUES (?)",
            [(t,) for t in {*plan.storage_types, *(loc.kind for loc in plan.locations)}],
        )

        def place(merged, location_id, deck_id):
            nonlocal copy_total
            for (card, art), qty in merged.items():
                conn.execute(
                    "INSERT INTO copies (base_card_id, card_image_id, location_id, deck_id, quantity) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (card, art, location_id, deck_id, qty),
                )
                copy_total += qty

        for loc in plan.locations:
            cur = conn.execute(
                "INSERT INTO storage_locations (name, kind, notes, created_at) "
                "VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
                (loc.name, loc.kind, loc.notes, loc.created_at),
            )
            place(_merge_copies(loc.copies, printings, f"{loc.kind} {loc.name!r}", warnings),
                  cur.lastrowid, None)

        # An export from a database that somehow lost it still yields a usable one.
        conn.execute(
            "INSERT OR IGNORE INTO storage_locations (name, kind, notes) VALUES (?, 'loose', ?)",
            (DEFAULT_LOOSE_LOCATION, "built-in: copies with no container named"),
        )

        for deck in plan.decks:
            cur = conn.execute(
                "INSERT INTO decks (name, leader_id, is_physical, notes, created_at) "
                "VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
                (deck.name, deck.leader, 1 if deck.physical else 0, deck.notes, deck.created_at),
            )
            deck_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO deck_cards (deck_id, base_card_id, quantity) VALUES (?, ?, ?)",
                [(deck_id, card, qty) for card, qty in deck.recipe],
            )
            place(_merge_copies(deck.copies, printings, f"deck {deck.name!r}", warnings),
                  None, deck_id)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return ImportSummary(len(plan.locations), len(plan.decks), copy_total, warnings)
