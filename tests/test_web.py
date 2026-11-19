"""Tests for optcg/web/api.py - and the parity check D-044 makes M1's yardstick.

The owner's real 2026-11-18 sample (tests/fixtures/sample_2026-11-18) is loaded
into a throwaway database. Every number the browser would get must equal what
the modules behind the CLI say - and both must equal the sample file itself.

Run:
    python -m pytest tests/test_web.py -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from optcg import collection as coll
from optcg import storage as store
from optcg.backup import import_collection, read_export
from optcg.cli import main
from optcg.db_setup import create_schema
from optcg.decks import list_decks
from optcg.web import api

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample_2026-11-18"


def load_sample(db_path: Path) -> None:
    """Build a database holding exactly the frozen sample."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    cards = json.loads((SAMPLE / "cards.json").read_text(encoding="utf-8"))
    columns = list(cards[0])
    conn.executemany(
        f"INSERT INTO cards ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        [tuple(c[k] for k in columns) for c in cards],
    )
    conn.commit()
    import_collection(conn, read_export(SAMPLE / "collection.json"))
    conn.close()


def sample_truth() -> tuple[dict, dict]:
    """Straight from the sample file, no app code: copies per place and per card."""
    export = json.loads((SAMPLE / "collection.json").read_text(encoding="utf-8"))
    per_place: dict[tuple[str, str], int] = {}
    per_card: dict[str, int] = defaultdict(int)
    holders = [(loc["kind"], loc["name"], loc["copies"]) for loc in export["locations"]]
    holders += [("deck", d["name"], d["copies"]) for d in export["decks"]]
    for kind, name, copies in holders:
        per_place[(kind, name)] = sum(c["quantity"] for c in copies)
        for c in copies:
            per_card[c["card"]] += c["quantity"]
    return per_place, dict(per_card)


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "sample.db"
        load_sample(self.db)
        self.client = TestClient(api.create_app(self.db))
        self.conn = sqlite3.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.client.close()
        self.tmp.cleanup()

    def get(self, url, **params):
        response = self.client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class SampleParityTests(WebTestCase):
    """D-044: the sample comes through the browser unchanged."""

    def test_headline_numbers(self):
        self.assertEqual(self.get("/api/summary"),
                         {"copies": 172, "cards": 77, "places": 5, "catalog": 77})

    def test_every_place_matches_the_sample_and_the_cli(self):
        per_place, _ = sample_truth()
        places = [p for p in self.get("/api/places") if p["holds_copies"]]
        self.assertEqual({(p["kind"], p["name"]): p["copies"] for p in places}, per_place)

        for p in places:
            shown = self.get("/api/places/contents", name=p["name"], kind=p["kind"])
            cli_rows = store.contents(self.conn, p["name"], kind=p["kind"])
            self.assertEqual(
                [(r["base_card_id"], r["card_image_id"], r["quantity"]) for r in shown["rows"]],
                [(base, art, qty) for base, art, _, qty in cli_rows],
                p["name"])
            self.assertEqual(shown["copies"], per_place[(p["kind"], p["name"])])

    def test_decks_match_deck_list(self):
        decks = {p["name"]: p for p in self.get("/api/places") if p["kind"] == "deck"}
        for d in list_decks(self.conn):
            self.assertEqual((decks[d.name]["copies"], decks[d.name]["cards"]),
                             (d.total_cards, d.distinct_cards))
        self.assertEqual(sorted(decks), ["Eustass Kid Y", "Nico Robin YP", "Reiju"])

    def test_every_card_matches_the_sample_and_the_cli(self):
        _, per_card = sample_truth()
        grid = self.get("/api/cards", owned_only="true", limit=500)
        self.assertEqual(grid["total"], len(per_card))
        self.assertEqual({t["base_card_id"]: t["owned"] for t in grid["cards"]}, per_card)

        for card_id, owned in per_card.items():
            detail = self.get(f"/api/cards/{card_id}")
            status = coll.show(self.conn, card_id)
            self.assertEqual(detail["total_owned"], owned, card_id)
            self.assertEqual(detail["total_owned"], status.total_owned, card_id)
            self.assertEqual(
                [(h["container"], h["kind"], h["card_image_id"], h["quantity"])
                 for h in detail["holdings"]],
                [tuple(h) for h in status.holdings], card_id)
            self.assertEqual([p["card_image_id"] for p in detail["printings"]],
                             [p.card_image_id for p in status.printings], card_id)

    def test_nothing_has_an_art_yet(self):
        grid = self.get("/api/cards", owned_only="true", limit=500)
        self.assertEqual(sum(t["art_recorded"] for t in grid["cards"]), 0)

    def test_pictures_come_through(self):
        detail = self.get("/api/cards/OP09-107")
        self.assertEqual([p["image_url"].rsplit("/", 1)[-1] for p in detail["printings"]],
                         ["OP09-107.jpg", "OP09-107_p1.jpg"])


class SetArtTests(WebTestCase):
    """Recording the art from the page reads the same in the CLI's modules."""

    def post(self, **body):
        return self.client.post("/api/copies/set-art", json=body)

    def test_round_trip_into_the_cli(self):
        response = self.post(card="OP09-107", quantity=2, art="p1",
                             place="Nico Robin YP", kind="deck")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["unsorted_left_here"], 2)

        status = coll.show(self.conn, "OP09-107")
        self.assertEqual({a.card_image_id: a.quantity for a in status.arts},
                         {None: 2, "OP09-107_p1": 2})
        rows = store.contents(self.conn, "Nico Robin YP", kind="deck")
        self.assertIn(("OP09-107", "OP09-107_p1", 2), [(r[0], r[1], r[3]) for r in rows])
        # Only the split by art changed: every total is the same.
        self.assertEqual(self.get("/api/summary")["copies"], 172)
        self.assertEqual(self.get("/api/cards", owned_only="true", q="nico robin",
                                  limit=50)["total"], 3)

    def test_change_art_exactly_as_the_page_sends_it(self):
        # "Set art": from_art is null (the unsorted pile), art is a full printing id.
        first = self.post(card="OP09-107", quantity=2, art="OP09-107_p1",
                          place="Nico Robin YP", kind="deck", from_art=None)
        self.assertEqual(first.status_code, 200, first.text)
        # "Change art" on the p1 pile: one of them is really the plain art.
        second = self.post(card="OP09-107", quantity=1, art="OP09-107",
                           place="Nico Robin YP", kind="deck", from_art="OP09-107_p1")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(
            {h["card_image_id"]: h["quantity"] for h in second.json()["card"]["holdings"]},
            {None: 2, "OP09-107": 1, "OP09-107_p1": 1})
        self.assertEqual(coll.show(self.conn, "OP09-107").total_owned, 4)

    def test_more_than_the_place_holds_is_refused_and_writes_nothing(self):
        response = self.post(card="OP09-107", quantity=5, art="p1",
                             place="Nico Robin YP", kind="deck")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not 5", response.json()["detail"])
        self.assertEqual(coll.show(self.conn, "OP09-107").unsorted, 4)

    def test_unknown_art_is_refused_with_the_cli_message(self):
        response = self.post(card="OP09-107", quantity=1, art="p9",
                             place="Nico Robin YP", kind="deck")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["detail"])

    def test_unknown_place_is_refused(self):
        response = self.post(card="OP09-107", quantity=1, art="p1", place="Nowhere")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Nowhere", response.json()["detail"])

    def test_quantity_must_be_positive(self):
        response = self.post(card="OP09-107", quantity=0, art="p1",
                             place="Nico Robin YP", kind="deck")
        self.assertEqual(response.status_code, 422)


class RouteTests(WebTestCase):
    def test_unknown_card_is_404(self):
        self.assertEqual(self.client.get("/api/cards/XX99-999").status_code, 404)

    def test_card_id_is_case_insensitive(self):
        self.assertEqual(self.get("/api/cards/op09-107")["base_card_id"], "OP09-107")

    def test_sets(self):
        sets = self.get("/api/sets")
        self.assertIn("OP09", sets)
        self.assertEqual(sets, sorted(sets))

    def test_grid_filters_and_paging(self):
        first = self.get("/api/cards", owned_only="true", limit=10, offset=0)
        second = self.get("/api/cards", owned_only="true", limit=10, offset=10)
        self.assertEqual((first["total"], len(first["cards"])), (77, 10))
        self.assertNotEqual(first["cards"][0], second["cards"][0])
        _, per_card = sample_truth()
        op12 = sum(1 for card in per_card if card.startswith("OP12-"))
        self.assertEqual(self.get("/api/cards", set="OP12", owned_only="true")["total"], op12)

    def test_place_contents_of_just_loose(self):
        shown = self.get("/api/places/contents", name="just-loose")
        self.assertEqual((shown["copies"], shown["cards"]), (8, 6))

    def test_ambiguous_or_missing_place_is_400(self):
        response = self.client.get("/api/places/contents", params={"name": "Nowhere"})
        self.assertEqual(response.status_code, 400)

    def test_page_missing_says_how_to_build_it(self):
        with mock.patch.object(api, "UI_DIST", Path(self.tmp.name) / "no-such-dir"):
            client = TestClient(api.create_app(self.db))
            self.assertIn("npm run build", client.get("/").json()["detail"])
            client.close()

    def test_built_page_is_served(self):
        dist = Path(self.tmp.name) / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<p>Card Keeper</p>", encoding="utf-8")
        with mock.patch.object(api, "UI_DIST", dist):
            client = TestClient(api.create_app(self.db))
            self.assertIn("Card Keeper", client.get("/").text)
            self.assertEqual(client.get("/api/summary").status_code, 200)
            client.close()


class PlacesAndMoveTests(WebTestCase):
    """M2: create a place, move copies; the CLI's functions see the same."""

    def test_storage_types(self):
        self.assertIn("binder", self.get("/api/storage-types"))

    def test_create_a_place(self):
        response = self.client.post("/api/places", json={"name": "Box 1", "kind": "booster-box"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["copies"], 0)
        self.assertIn(("booster-box", "Box 1"),
                      [(p["kind"], p["name"]) for p in self.get("/api/places")])

    def test_duplicate_or_bad_type_is_refused(self):
        self.client.post("/api/places", json={"name": "Box 1", "kind": "binder"})
        again = self.client.post("/api/places", json={"name": "Box 1", "kind": "binder"})
        self.assertEqual(again.status_code, 400)
        bad = self.client.post("/api/places", json={"name": "X", "kind": "no-such-type"})
        self.assertEqual(bad.status_code, 400)

    def test_move_between_places_keeps_every_total(self):
        response = self.client.post("/api/copies/move", json={
            "card": "OP12-107", "quantity": 2, "from_place": "trading_binder",
            "to_place": "just-loose"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["now_at_destination"], 2)
        rows = store.contents(self.conn, "just-loose")
        self.assertIn(("OP12-107", None, 2), [(r[0], r[1], r[3]) for r in rows])
        self.assertEqual(self.get("/api/summary")["copies"], 172)

    def test_moving_out_of_a_deck_shrinks_its_list(self):
        response = self.client.post("/api/copies/move", json={
            "card": "OP09-107", "quantity": 1, "from_place": "Nico Robin YP",
            "from_kind": "deck", "to_place": "trading_binder"})
        self.assertEqual(response.status_code, 200, response.text)
        robin = next(d for d in list_decks(self.conn) if d.name == "Nico Robin YP")
        self.assertEqual(robin.total_cards, 50)
        self.assertEqual(self.get("/api/summary")["copies"], 172)

    def test_moving_more_than_is_there_is_refused(self):
        response = self.client.post("/api/copies/move", json={
            "card": "OP12-107", "quantity": 9, "from_place": "trading_binder",
            "to_place": "just-loose"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not 9", response.json()["detail"])


DECKLIST = "Leader\n1 Nico Robin (OP09-062)\nCharacter (4)\n4 Nico Robin (OP09-107)\n"


class DeckTests(WebTestCase):
    """M3: decks in the browser, the same as `deck list` / `deck show` / `deck paste`."""

    def deck_id(self, name):
        return next(d for d in self.get("/api/decks") if d["name"] == name)["id"]

    def test_deck_list_matches_the_cli(self):
        self.assertEqual(self.get("/api/decks"), [d._asdict() for d in list_decks(self.conn)])

    def test_deck_detail(self):
        detail = self.get(f"/api/decks/{self.deck_id('Reiju')}")
        self.assertEqual((detail["total_cards"], detail["in_deck_total"], detail["complete"]),
                         (51, 51, True))
        self.assertEqual(sum(1 for c in detail["cards"] if c["is_leader"]), 1)

    def test_unknown_deck_is_refused(self):
        self.assertEqual(self.client.get("/api/decks/999").status_code, 400)

    def test_preview_saves_nothing(self):
        response = self.client.post("/api/decks/import", json={
            "text": DECKLIST, "name": "Test", "physical": False, "preview": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["preview"])
        self.assertEqual(response.json()["total_cards"], 5)
        self.assertNotIn("Test", [d["name"] for d in self.get("/api/decks")])

    def test_import_a_wishlist_leaves_the_collection_alone(self):
        response = self.client.post("/api/decks/import", json={
            "text": DECKLIST, "name": "Want", "physical": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get("/api/summary")["copies"], 172)
        detail = self.get(f"/api/decks/{self.deck_id('Want')}")
        self.assertFalse(detail["physical"])

    def test_import_physical_credits_the_collection(self):
        response = self.client.post("/api/decks/import", json={
            "text": DECKLIST, "name": "Mini", "physical": True})
        self.assertEqual(response.json()["collection_credited"], 5)
        self.assertEqual(self.get("/api/summary")["copies"], 177)

    def test_name_clash_asks_instead_of_choosing(self):
        response = self.client.post("/api/decks/import", json={
            "text": DECKLIST, "name": "Reiju", "physical": False})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "name_exists")
        self.assertEqual(self.get("/api/summary")["copies"], 172)  # nothing happened

    def test_name_clash_import_as_second(self):
        response = self.client.post("/api/decks/import", json={
            "text": DECKLIST, "name": "Reiju", "physical": False, "on_conflict": "new"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sum(1 for d in self.get("/api/decks") if d["name"] == "Reiju"), 2)

    def test_empty_list_is_refused(self):
        response = self.client.post("/api/decks/import", json={"text": "hello", "name": "X"})
        self.assertEqual(response.status_code, 400)


class WebCommandTests(unittest.TestCase):
    def test_starts_on_this_computer_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "sample.db"
            load_sample(db)
            with mock.patch("uvicorn.run") as run, mock.patch("builtins.print"):
                code = main(["--db", str(db), "web", "--no-browser", "--port", "8123"])
            self.assertEqual(code, 0)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["port"], 8123)

    def test_opens_the_browser_and_says_when_the_page_isnt_built(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "sample.db"
            load_sample(db)
            with mock.patch("uvicorn.run"), mock.patch("threading.Timer") as timer, \
                    mock.patch.object(api, "UI_DIST", Path(tmp) / "none"), \
                    mock.patch("builtins.print") as printed:
                code = main(["--db", str(db), "web"])
            self.assertEqual(code, 0)
            self.assertEqual(timer.call_args.args[2], ["http://127.0.0.1:8000/"])
            timer.return_value.start.assert_called_once()
            self.assertIn("npm run build", " ".join(str(c) for c in printed.call_args_list))

    def test_port_in_use_is_explained(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "sample.db"
            load_sample(db)
            with mock.patch("uvicorn.run", side_effect=OSError("in use")), \
                    mock.patch("builtins.print") as printed:
                code = main(["--db", str(db), "web", "--no-browser"])
            self.assertEqual(code, 1)
            self.assertIn("--port 8001", " ".join(str(c) for c in printed.call_args_list))


if __name__ == "__main__":
    unittest.main()
