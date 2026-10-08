"""Integratietests voor server/server.py: keep-alive, XFF-guard, JSON-fouten.

De server draait echt op een ephemere poort in een achtergrondthread; data en
site-dir wijzen naar een tijdelijke map zodat er niets aan de live omgeving
wordt geschreven.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_server_module(data_dir: str, site_dir: str):
    os.environ["DATA_DIR"] = data_dir
    os.environ["SITE_DIR"] = site_dir
    os.environ["WRITE_NETWORKS"] = "127.0.0.1"
    os.environ["MIN_RATING_INTERVAL"] = "0"
    os.environ["MAX_WRITES_PER_HOUR"] = "10000"
    os.environ["MAX_RATINGS_PER_HOUR"] = "10000"
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    name = "server_under_test"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "server", "server.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="server_test_")
        cls.data_dir = os.path.join(cls.tmp, "data")
        cls.site_dir = os.path.join(cls.tmp, "site")
        os.makedirs(os.path.join(cls.data_dir, "logs"))
        os.makedirs(os.path.join(cls.site_dir, "assets"))
        os.makedirs(os.path.join(cls.site_dir, "items"))
        with open(os.path.join(cls.site_dir, "index.html"), "w", encoding="utf-8") as fh:
            fh.write("<html><body>index</body></html>")
        with open(os.path.join(cls.site_dir, "items", "demo-item.html"),
                  "w", encoding="utf-8") as fh:
            fh.write("<html><body>demo</body></html>")
        with open(os.path.join(cls.site_dir, "assets", "favicon.svg"),
                  "w", encoding="utf-8") as fh:
            fh.write("<svg xmlns='http://www.w3.org/2000/svg'/>")

        cls.mod = load_server_module(cls.data_dir, cls.site_dir)
        cls.httpd = cls.mod.ThreadingHTTPServer(("127.0.0.1", 0), cls.mod.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.mod.WRITE_NETWORKS = ["127.0.0.1"]
        self.mod._recent_post.clear()
        self.mod._rate_window.clear()
        self.mod._rebuild_proc["proc"] = None

    # ---------- helpers ----------
    def connect(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    @staticmethod
    def _recv(conn: http.client.HTTPConnection):
        res = conn.getresponse()
        body = res.read().decode("utf-8", "replace")
        try:
            data = json.loads(body)
        except ValueError:
            data = body
        return res.status, data, res.getheader("Content-Type", "")

    def get(self, path: str, conn=None):
        own = conn is None
        conn = conn or self.connect()
        conn.request("GET", path)
        out = self._recv(conn)
        if own:
            conn.close()
        return out

    def post_json(self, path: str, payload, conn=None, headers=None):
        own = conn is None
        conn = conn or self.connect()
        body = json.dumps(payload)
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        conn.request("POST", path, body=body, headers=hdrs)
        out = self._recv(conn)
        if own:
            conn.close()
        return out

    # ---------- basis ----------
    def test_healthz_en_index(self):
        status, data, ctype = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        status, body, ctype = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("index", body)
        self.assertIn("text/html", ctype)

    def test_favicon(self):
        status, body, ctype = self.get("/assets/favicon.svg")
        self.assertEqual(status, 200)
        self.assertIn("svg", ctype)

    # ---------- regressie: keep-alive na 403 ----------
    def test_403_daarna_zelfde_verbinding_blijft_json(self):
        """De kernbug: 403 zonder uitlezen >12 maakte de volgende request
        op dezelfde keep-alive-verbinding tot rommel (400-HTML)."""
        conn = self.connect()
        try:
            self.mod.WRITE_NETWORKS = []
            status, data, _ = self.post_json(
                "/api/references",
                {"url": "https://voorbeeld.nl/x", "category": "models"},
                conn=conn)
            self.assertEqual(status, 403)
            self.assertIsInstance(data, dict)
            self.assertIn("thuisnetwerk", data["error"])

            #zelfde verbinding, mét body: moet 403-JSON worden, niet 400-HTML
            status, data, _ = self.post_json(
                "/api/topics",
                {"category_id": "models", "title_en": "X"},
                conn=conn)
            self.assertEqual(status, 403)
            self.assertIsInstance(data, dict)
            self.assertIn("thuisnetwerk", data["error"])

            # en een volledige derde ronde op dezelfde socket
            status, data, _ = self.get("/healthz", conn=conn)
            self.assertEqual(status, 200)
            self.assertEqual(data["status"], "ok")
        finally:
            conn.close()
            self.mod.WRITE_NETWORKS = ["127.0.0.1"]

    def test_403_toont_ip_en_netwerken(self):
        self.mod.WRITE_NETWORKS = ["10.0.0.0/8"]
        status, data, _ = self.post_json(
            "/api/references", {"url": "https://voorbeeld.nl/y", "category": "models"})
        self.assertEqual(status, 403)
        self.assertIn("jouw IP: 127.0.0.1", data["error"])
        self.assertIn("10.0.0.0/8", data["error"])

    # ---------- X-Forwarded-For: rechterlijk deel ----------
    def test_xff_neemt_rechterlijk_ip(self):
        self.mod.WRITE_NETWORKS = ["10.0.0.0/8"]
        # proxy-hang: client spoofed links, Traefik voegt rechts het echte toe
        status, _, _ = self.post_json(
            "/api/references",
            {"url": "https://voorbeeld.nl/xff-ok", "category": "models"},
            headers={"X-Forwarded-For": "1.2.3.4, 10.1.2.3"})
        self.assertEqual(status, 201)
        # rechterlijk valt buiten WRITE_NETWORKS -> geweigerd
        status, data, _ = self.post_json(
            "/api/references",
            {"url": "https://voorbeeld.nl/xff-nok", "category": "models"},
            headers={"X-Forwarded-For": "10.1.2.3, 192.168.1.7"})
        self.assertEqual(status, 403)
        self.assertIn("192.168.1.7", data["error"])

    def test_spoof_zonder_xff_blijft_client_ip(self):
        self.mod.WRITE_NETWORKS = ["192.168.0.0/16"]
        status, data, _ = self.post_json(
            "/api/references", {"url": "https://voorbeeld.nl/noxff", "category": "models"})
        self.assertEqual(status, 403)
        self.assertIn("127.0.0.1", data["error"])

    # ---------- fouten blijven JSON ----------
    def test_onbekend_post_endpoint_is_json_404(self):
        conn = self.connect()
        try:
            status, data, _ = self.post_json("/api/niks", {"a": 1}, conn=conn)
            self.assertEqual(status, 404)
            self.assertIsInstance(data, dict)
            status, data, _ = self.get("/healthz", conn=conn)
            self.assertEqual(status, 200)
        finally:
            conn.close()

    def test_onbekende_api_get_is_json(self):
        status, data, _ = self.get("/api/niks")
        self.assertEqual(status, 404)
        self.assertIsInstance(data, dict)

    def test_ongeldige_json_body(self):
        conn = self.connect()
        try:
            conn.request("POST", "/api/rating", body="niet-json{{",
                         headers={"Content-Type": "application/json"})
            status, data, _ = self._recv(conn)
            self.assertEqual(status, 400)
            self.assertIsInstance(data, dict)
            status, data, _ = self.get("/healthz", conn=conn)
            self.assertEqual(status, 200)
        finally:
            conn.close()

    # ---------- ratings ----------
    def test_rating_flow(self):
        status, data, _ = self.post_json("/api/rating", {"slug": "demo-item", "score": 5})
        self.assertEqual(status, 200)
        self.assertEqual(data["my_score"], 5)
        self.assertEqual(data["count"], 1)
        status, data, _ = self.get("/api/rating?slug=demo-item")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["average"], 5.0)
        # herverstelling vervangt, niet optelt
        status, data, _ = self.post_json("/api/rating", {"slug": "demo-item", "score": 3})
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["average"], 3.0)

    def test_rating_onbekende_slug(self):
        status, data, _ = self.post_json("/api/rating", {"slug": "onzin-item", "score": 4})
        self.assertEqual(status, 404)
        self.assertIsInstance(data, dict)

    def test_rating_scores_bereik(self):
        status, data, _ = self.post_json("/api/rating", {"slug": "demo-item", "score": 9})
        self.assertEqual(status, 400)

    # ---------- schrijven: toegestaan ----------
    def test_referentie_toevoegen(self):
        status, data, _ = self.post_json(
            "/api/references",
            {"url": "https://voorbeeld.org/unique-ref", "title": "Ref",
             "category": "models"})
        self.assertEqual(status, 201)
        self.assertEqual(data["reference"]["category"], "models")
        with open(os.path.join(self.data_dir, "references.json"),
                  encoding="utf-8") as fh:
            refs = json.load(fh)["references"]
        self.assertTrue(any(r["url"] == "https://voorbeeld.org/unique-ref"
                            for r in refs))
        # dubbel -> 409
        status, data, _ = self.post_json(
            "/api/references",
            {"url": "https://voorbeeld.org/unique-ref", "category": "models"})
        self.assertEqual(status, 409)

    # ---------- rebuild ----------
    def test_rebuild_409_bij_vers_slot(self):
        lock = os.path.join(self.data_dir, ".crawl.lock")
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write('{"pid": 1, "started": "2026-10-07T00:00:00Z"}')
        try:
            status, data, _ = self.post_json("/api/rebuild", {})
            self.assertEqual(status, 409)
            self.assertIsInstance(data, dict)
        finally:
            os.remove(lock)

    def test_rebuild_409_bij_levend_proces(self):
        class FakeProc:
            def poll(self):
                return None

        self.mod._rebuild_proc["proc"] = FakeProc()
        try:
            status, data, _ = self.post_json("/api/rebuild", {})
            self.assertEqual(status, 409)
        finally:
            self.mod._rebuild_proc["proc"] = None


if __name__ == "__main__":
    unittest.main()
