"""HTTP layer for the browser page (PHASE_2 M1, D-044).

Every route calls a function the CLI already uses and shapes its answer as
JSON - **no SQL in this file.** That is what keeps the page and the terminal in
agreement: there is one database file and one set of rules, and this is only a
second way of asking them.

Errors keep the modules' own messages (`CollectionError`, `StorageError`,
`DeckError` are already written for a person to read): they come back as
`400 {"detail": message}` and the page shows them as they are.

Bound to 127.0.0.1 by `optcg web`: nothing outside this computer can reach it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from optcg import collection as coll
from optcg import storage as store
from optcg.collection import CollectionError
from optcg.decks import (
    DeckError,
    DeckNameExists,
    import_decklist,
    list_decks,
    resolve_deck,
    show_deck,
)
from optcg.storage import StorageError

# ui/dist, built by `npm run build` in ui/. Two levels above optcg/web/.
UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"


class SetArtRequest(BaseModel):
    """Say which art some copies in one place are (collection.set_art)."""
    card: str = Field(description="gameplay id, e.g. OP09-107")
    quantity: int = Field(gt=0)
    art: str = Field(description="a printing: 'base', 'p1', or a full id like OP09-107_p1")
    place: str = Field(description="the deck or storage place the copies are in")
    kind: str | None = Field(default=None, description="the place's type, or 'deck'")
    from_art: str | None = Field(
        default=None, description="which copies to change; none = the unsorted ones")


class NewPlaceRequest(BaseModel):
    """Create a storage place (storage.add_location) - M2."""
    name: str = Field(min_length=1)
    kind: str = Field(description="a storage type: binder, booster-box, ... (GET /api/storage-types)")
    notes: str | None = None


class MoveRequest(BaseModel):
    """Move copies from one place to another (collection.move) - M2."""
    card: str
    quantity: int = Field(gt=0)
    from_place: str
    from_kind: str | None = None
    to_place: str
    to_kind: str | None = None
    art: str | None = Field(default=None, description="which pile; none = the unsorted copies")


class CopiesRequest(BaseModel):
    """Copies arriving in (or, for undo, leaving) one place - M4."""
    card: str
    quantity: int = Field(default=1, gt=0)
    place: str | None = Field(default=None, description="none = just-loose")
    kind: str | None = None
    art: str | None = Field(default=None, description="none = unsorted")


class DeckImportRequest(BaseModel):
    """Import a pasted decklist (decks.import_decklist) - M3."""
    text: str
    name: str = Field(min_length=1)
    physical: bool = True
    on_conflict: str | None = Field(
        default=None, description="when the name is taken: 'replace' or 'new'; none = ask")
    preview: bool = Field(default=False, description="show the result, save nothing (D-034)")


def _rows(items) -> list[dict]:
    return [i._asdict() for i in items]


def card_detail(conn: sqlite3.Connection, base_card_id: str) -> dict | None:
    """`collection.show` as JSON, with the per-art and per-place breakdowns."""
    status = coll.show(conn, base_card_id)
    if status is None:
        return None
    return {
        "base_card_id": status.base_card_id,
        "name": status.name,
        "total_owned": status.total_owned,
        "in_decks": status.committed,
        "not_in_decks": status.loose,
        "unsorted": status.unsorted,
        "arts": _rows(status.arts),
        "holdings": [{**h._asdict(), "art": h.art} for h in status.holdings],
        "printings": _rows(status.printings),
    }


def places(conn: sqlite3.Connection) -> list[dict]:
    """Every place a copy can be: storage places, then decks (wishlists hold none)."""
    out = [
        {"name": loc.name, "kind": loc.kind, "notes": loc.notes,
         "cards": loc.distinct_cards, "copies": loc.total_cards, "holds_copies": True}
        for loc in store.list_locations(conn)
    ]
    for deck in list_decks(conn):
        # A physical deck is exactly what's in it (D-041), so its list's size
        # is its copies. A wishlist is a plan and holds no copies (D-010).
        out.append({
            "name": deck.name, "kind": store.DECK_KIND, "notes": None,
            "deck_id": deck.id, "leader_id": deck.leader_id,
            "physical": deck.is_physical,
            "cards": deck.distinct_cards,
            "copies": deck.total_cards if deck.is_physical else 0,
            "holds_copies": deck.is_physical,
        })
    return out


def place_contents(conn: sqlite3.Connection, name: str, kind: str | None) -> dict:
    """What one place holds, art by art - what `optcg storage show` prints."""
    container = store.resolve_container(conn, name, kind)
    rows = store.contents(conn, name, kind=kind)
    return {
        "name": container.name,
        "kind": container.kind,
        "copies": sum(r[3] for r in rows),
        "cards": len({r[0] for r in rows}),
        "rows": [
            {"base_card_id": base, "card_image_id": art, "name": card_name, "quantity": qty}
            for base, art, card_name, qty in rows
        ],
    }


def deck_detail(conn: sqlite3.Connection, deck_id: int) -> dict:
    """`decks.show_deck` as JSON - what `optcg deck show` prints."""
    detail = show_deck(conn, deck_id)
    return {
        "id": detail.id, "name": detail.name, "leader_id": detail.leader_id,
        "physical": detail.is_physical, "total_cards": detail.total_cards,
        "in_deck_total": detail.in_deck_total, "missing_total": detail.missing_total,
        "complete": detail.is_complete, "cards": _rows(detail.cards),
    }


def import_deck_text(conn: sqlite3.Connection, body: DeckImportRequest) -> dict:
    """Import a pasted list, or with `preview` run it on a copy and keep nothing.

    A copy rather than a rolled-back transaction, for the same reason as the
    CLI's --dry-run (D-034): the modules commit as they go.
    """
    target = conn
    if body.preview:
        target = sqlite3.connect(":memory:")
        conn.backup(target)
        target.execute("PRAGMA foreign_keys = ON")
    try:
        result = import_decklist(target, body.text, body.name, body.physical, body.on_conflict)
        out = result._asdict()
        out["preview"] = body.preview
        return out
    finally:
        if target is not conn:
            target.close()


def summary(conn: sqlite3.Connection) -> dict:
    """The headline numbers, from the same places the CLI's lists read."""
    all_places = places(conn)
    owned = coll.card_page(conn, owned_only=True, limit=1)
    return {
        "copies": sum(p["copies"] for p in all_places),
        "cards": owned.total,
        "places": sum(1 for p in all_places if p["holds_copies"]),
        "catalog": coll.card_page(conn, limit=1).total,
    }


def create_app(db_path: Path) -> FastAPI:
    """The app for one database file. Tests pass a throwaway copy."""
    app = FastAPI(title="OPTCG Card Keeper", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def get_conn() -> Iterator[sqlite3.Connection]:
        # One connection per request. check_same_thread is off because FastAPI
        # may open it in one worker thread and use it in another; it is never
        # shared between requests.
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    # A name clash is a question for the owner, not an error (D-017): 409 with
    # a code the page recognises, so it can offer replace / import as second.
    @app.exception_handler(DeckNameExists)
    async def name_taken(_: Request, exc: DeckNameExists) -> JSONResponse:
        return JSONResponse(status_code=409, content={
            "detail": str(exc), "code": "name_exists", "deck_id": exc.deck_id})

    @app.exception_handler(CollectionError)
    @app.exception_handler(StorageError)
    @app.exception_handler(DeckError)
    async def refused(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/summary")
    def get_summary(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return summary(conn)

    @app.get("/api/cards")
    def get_cards(
        set: str | None = None,
        color: str | None = None,
        category: str | None = None,
        owned_only: bool = False,
        q: str | None = None,
        limit: int = 60,
        offset: int = 0,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict:
        page = coll.card_page(conn, set, color, category, owned_only, q,
                              min(max(limit, 1), 500), max(offset, 0))
        return {"total": page.total, "cards": _rows(page.tiles)}

    @app.get("/api/cards/{base_card_id}")
    def get_card(base_card_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        detail = card_detail(conn, base_card_id.strip().upper())
        if detail is None:
            raise HTTPException(status_code=404, detail=f"no card {base_card_id!r}")
        return detail

    @app.get("/api/sets")
    def get_sets(conn: sqlite3.Connection = Depends(get_conn)) -> list[str]:
        return coll.game_sets(conn)

    @app.get("/api/places")
    def get_places(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
        return places(conn)

    @app.get("/api/places/contents")
    def get_place_contents(name: str, kind: str | None = None,
                           conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return place_contents(conn, name, kind)

    @app.post("/api/copies/set-art")
    def post_set_art(body: SetArtRequest, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        card = body.card.strip().upper()
        where = store.resolve_container(conn, body.place, body.kind)
        unsorted_left = coll.set_art(conn, card, body.quantity, body.art, where, body.from_art)
        return {"unsorted_left_here": unsorted_left, "card": card_detail(conn, card)}

    # ---- M2: places and moving copies -----------------------------------

    @app.get("/api/storage-types")
    def get_storage_types(conn: sqlite3.Connection = Depends(get_conn)) -> list[str]:
        return store.storage_types(conn)

    @app.post("/api/places")
    def post_place(body: NewPlaceRequest, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        store.add_location(conn, body.name.strip(), body.kind, body.notes)
        return place_contents(conn, body.name.strip(), body.kind)

    @app.post("/api/copies/move")
    def post_move(body: MoveRequest, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        card = body.card.strip().upper()
        frm = store.resolve_container(conn, body.from_place, body.from_kind)
        to = store.resolve_container(conn, body.to_place, body.to_kind)
        now_there = coll.move(conn, card, body.quantity, frm, to, body.art)
        return {"now_at_destination": now_there, "card": card_detail(conn, card)}

    # ---- M4: fast entry ---------------------------------------------------

    @app.post("/api/copies/add")
    def post_add(body: CopiesRequest, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        """New copies you now own (`collection add`), into one place."""
        card = body.card.strip().upper()
        to = store.resolve_container(conn, body.place, body.kind)
        total = coll.add(conn, card, body.quantity, body.art, to)
        return {"card": card, "total_owned": total, "place": to.name, "kind": to.kind}

    @app.post("/api/copies/remove")
    def post_remove(body: CopiesRequest, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        """Copies leaving the collection (`collection remove`). The page uses it
        only to undo an add it just made, from the same place."""
        card = body.card.strip().upper()
        frm = store.resolve_container(conn, body.place, body.kind)
        total = coll.remove(conn, card, body.quantity, body.art, frm)
        return {"card": card, "total_owned": total, "place": frm.name, "kind": frm.kind}

    # ---- M3: decks -------------------------------------------------------

    @app.get("/api/decks")
    def get_decks(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
        return _rows(list_decks(conn))

    @app.get("/api/decks/{deck_id}")
    def get_deck(deck_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return deck_detail(conn, resolve_deck(conn, deck_id))

    @app.post("/api/decks/import")
    def post_deck_import(body: DeckImportRequest,
                         conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        return import_deck_text(conn, body)

    if UI_DIST.is_dir():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:
        @app.get("/")
        def no_ui() -> dict:
            return {"detail": "the page isn't built yet - run `npm install` and "
                              "`npm run build` in the ui folder. The API is at /api."}

    return app
