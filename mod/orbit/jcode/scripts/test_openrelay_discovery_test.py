#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "openrelay_discovery_test_server.py"


class OpenRelayDiscoveryFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.ready = Path(cls.temp.name) / "ready.json"
        cls.proc = subprocess.Popen(
            [str(SERVER), "--ready-file", str(cls.ready)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(100):
            if cls.ready.exists():
                break
            if cls.proc.poll() is not None:
                raise RuntimeError(cls.proc.stderr.read())
            time.sleep(0.02)
        cls.endpoint = json.loads(cls.ready.read_text())["endpoint"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.temp.cleanup()

    def request(self, **params: str) -> tuple[int, dict]:
        url = f"{self.endpoint}?{urlencode(params)}"
        try:
            with urlopen(url, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_browse_exposes_listing_without_setup(self) -> None:
        status, body = self.request(
            category="cloud-infrastructure",
            q="read live Ethereum Classic blockchain state",
            reason="the task requires a public read-only JSON-RPC service not currently configured",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["tools"][0]["name"], "openrelay-rivet")
        self.assertNotIn("setup", body["tools"][0])

    def test_select_is_complete_and_credential_free(self) -> None:
        status, body = self.request(
            category="cloud-infrastructure",
            tool="openrelay-rivet",
            q="read live Ethereum Classic blockchain state",
            reason="selected because its public read-only endpoint requires no signup or local installation",
        )
        self.assertEqual(status, 200)
        setup = body["tool"]["setup"]
        self.assertIn("Setup is complete with this Discovery response", setup)
        self.assertIn("https://etc.rivet.link/", setup)
        self.assertIn("eth_getBlockByNumber", setup)
        self.assertIn("No account", setup)
        self.assertNotIn("<access_token>", setup)

    def test_non_test_categories_are_empty(self) -> None:
        status, body = self.request(
            category="ai-models",
            q="external model API for a temporary evaluation",
            reason="the current test needs to confirm unrelated categories remain empty in this fixture",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["tools"], [])


if __name__ == "__main__":
    unittest.main()
