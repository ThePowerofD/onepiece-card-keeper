"""Tests for src/api_client.py.

The network is always stubbed - these tests never touch optcgapi.com.

Run:
    python -m pytest tests/test_api_client.py -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from src import api_client
from src.api_client import (
    ApiError,
    _get_json,
    _unwrap_list,
    fetch_all_promos,
    fetch_all_set_cards,
    fetch_all_st_cards,
    fetch_card_by_id,
)

CARD = {"card_image_id": "OP05-097", "card_name": "Luffy"}


def fake_response(payload, status_ok=True):
    resp = mock.Mock()
    resp.json.return_value = payload
    resp.raise_for_status.side_effect = None if status_ok else requests.HTTPError("404")
    return resp


class UnwrapListTests(unittest.TestCase):
    def test_bare_list(self):
        self.assertEqual(_unwrap_list([CARD]), [CARD])

    def test_wrapped_under_known_keys(self):
        for key in ("data", "cards", "results", "items"):
            self.assertEqual(_unwrap_list({key: [CARD]}), [CARD], msg=key)

    def test_unexpected_shape_raises_api_error(self):
        for payload in ["a string", 42, None, {"unexpected": [CARD]}]:
            with self.assertRaises(ApiError, msg=repr(payload)):
                _unwrap_list(payload)


class GetJsonTests(unittest.TestCase):
    def test_returns_parsed_json(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            self.assertEqual(_get_json("http://x"), [CARD])

    def test_retries_once_then_succeeds(self):
        responses = [requests.ConnectionError("boom"), fake_response([CARD])]

        def side_effect(*a, **kw):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with mock.patch("requests.get", side_effect=side_effect), \
             mock.patch("time.sleep"):
            self.assertEqual(_get_json("http://x"), [CARD])

    def test_raises_api_error_after_the_retry(self):
        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")), \
             mock.patch("time.sleep"):
            with self.assertRaises(ApiError) as ctx:
                _get_json("http://x")
        self.assertIn("Failed to GET", str(ctx.exception))

    def test_http_error_becomes_api_error(self):
        with mock.patch("requests.get", return_value=fake_response(None, status_ok=False)), \
             mock.patch("time.sleep"):
            with self.assertRaises(ApiError):
                _get_json("http://x")

    def test_invalid_json_becomes_api_error(self):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("not json")
        with mock.patch("requests.get", return_value=resp), mock.patch("time.sleep"):
            with self.assertRaises(ApiError):
                _get_json("http://x")


class FetchTests(unittest.TestCase):
    def test_each_endpoint_returns_a_list(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            self.assertEqual(fetch_all_set_cards(), [CARD])
            self.assertEqual(fetch_all_st_cards(), [CARD])
            self.assertEqual(fetch_all_promos(), [CARD])

    def test_endpoints_hit_the_expected_paths(self):
        """D-012 - there is no /api/allCards/; three endpoints are required."""
        with mock.patch("requests.get", return_value=fake_response([CARD])) as get:
            fetch_all_set_cards()
            fetch_all_st_cards()
            fetch_all_promos()
        urls = [call.args[0] for call in get.call_args_list]
        self.assertTrue(urls[0].endswith("/api/allSetCards/"))
        self.assertTrue(urls[1].endswith("/api/allSTCards/"))
        self.assertIn("/api/promos/filtered/?rarity=PR", urls[2])

    def test_base_url_override(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])) as get:
            fetch_all_set_cards(base_url="http://localhost:9999")
        self.assertTrue(get.call_args.args[0].startswith("http://localhost:9999"))

    def test_wrapped_payloads_are_unwrapped(self):
        with mock.patch("requests.get", return_value=fake_response({"data": [CARD]})):
            self.assertEqual(fetch_all_set_cards(), [CARD])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._real_dir = api_client.CACHE_DIR
        api_client.CACHE_DIR = Path(self.tmp.name)

    def tearDown(self):
        api_client.CACHE_DIR = self._real_dir
        self.tmp.cleanup()

    def test_first_call_fetches_and_writes_the_cache(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])) as get:
            fetch_all_set_cards(use_cache=True)
        self.assertEqual(get.call_count, 1)
        cached = json.loads((Path(self.tmp.name) / "all_set_cards.json").read_text())
        self.assertEqual(cached, [CARD])

    def test_second_call_reads_the_cache_without_the_network(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            fetch_all_set_cards(use_cache=True)
        with mock.patch("requests.get", side_effect=AssertionError("network used")) as get:
            self.assertEqual(fetch_all_set_cards(use_cache=True), [CARD])
            get.assert_not_called()

    def test_promos_cache_separately(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            fetch_all_promos(use_cache=True)
        self.assertTrue((Path(self.tmp.name) / "all_promos.json").exists())

    def test_without_the_flag_nothing_is_cached(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            fetch_all_set_cards(use_cache=False)
        self.assertEqual(list(Path(self.tmp.name).glob("*.json")), [])


class FetchCardByIdTests(unittest.TestCase):
    def test_dict_payload(self):
        with mock.patch("requests.get", return_value=fake_response(CARD)):
            self.assertEqual(fetch_card_by_id("OP05-097"), CARD)

    def test_nested_payload(self):
        for key in ("data", "card"):
            with mock.patch("requests.get", return_value=fake_response({key: CARD})):
                self.assertEqual(fetch_card_by_id("OP05-097"), CARD)

    def test_list_payload_takes_the_first(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            self.assertEqual(fetch_card_by_id("OP05-097"), CARD)

    def test_empty_list_returns_none(self):
        with mock.patch("requests.get", return_value=fake_response([])):
            self.assertIsNone(fetch_card_by_id("OP05-097"))

    def test_404_returns_none_rather_than_raising(self):
        with mock.patch("src.api_client._get_json", side_effect=ApiError("404 Not Found")):
            self.assertIsNone(fetch_card_by_id("ZZ99-999"))

    def test_other_errors_still_raise(self):
        with mock.patch("src.api_client._get_json", side_effect=ApiError("500 Server Error")):
            with self.assertRaises(ApiError):
                fetch_card_by_id("OP05-097")



class PromoCacheReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._real_dir = api_client.CACHE_DIR
        api_client.CACHE_DIR = Path(self.tmp.name)

    def tearDown(self):
        api_client.CACHE_DIR = self._real_dir
        self.tmp.cleanup()

    def test_promos_read_back_from_cache_without_the_network(self):
        (Path(self.tmp.name) / "all_promos.json").write_text(
            json.dumps([CARD]), encoding="utf-8"
        )
        with mock.patch("requests.get", side_effect=AssertionError("network used")) as get:
            self.assertEqual(fetch_all_promos(use_cache=True), [CARD])
            get.assert_not_called()


class ApiClientCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._real_dir = api_client.CACHE_DIR
        api_client.CACHE_DIR = Path(self.tmp.name)

    def tearDown(self):
        api_client.CACHE_DIR = self._real_dir
        self.tmp.cleanup()

    def run_main(self, *argv) -> str:
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with mock.patch("sys.argv", ["api_client", *argv]), redirect_stdout(out):
            api_client.main()
        return out.getvalue()

    def test_prints_counts_and_a_sample(self):
        with mock.patch("requests.get", return_value=fake_response([CARD])):
            output = self.run_main()
        self.assertIn("Set cards: 1", output)
        self.assertIn("Starter deck cards: 1", output)
        self.assertIn("Promo cards: 1", output)
        self.assertIn("Total: 3", output)
        self.assertIn("Sample card:", output)

    def test_single_card_lookup(self):
        with mock.patch("requests.get", return_value=fake_response(CARD)):
            output = self.run_main("--card", "OP05-097")
        self.assertIn("OP05-097", output)

    def test_single_card_not_found(self):
        with mock.patch("src.api_client._get_json", side_effect=ApiError("404")):
            output = self.run_main("--card", "ZZ99-999")
        self.assertIn("No card found", output)

    def test_empty_result_skips_the_sample(self):
        with mock.patch("requests.get", return_value=fake_response([])):
            output = self.run_main()
        self.assertIn("Total: 0", output)
        self.assertNotIn("Sample card:", output)

if __name__ == "__main__":
    unittest.main()
