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
from optcg.decks import DeckError, list_decks
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

    if UI_DIST.is_dir():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:
        @app.get("/")
        def no_ui() -> dict:
            return {"detail": "the page isn't built yet - run `npm install` and "
                              "`npm run build` in the ui folder. The API is at /api."}

    return app
