"""Render-tests: help-pagina, favicon, tooltips en statische assets.

Rendert de volledige site naar een tijdelijke map (de live data wordt niet
aangeraakt) en controleert de nieuwe pagina-elementen.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pipeline import render, scoring  # noqa: E402

CONFIG_PATH = os.path.join(ROOT, "config", "categories.yaml")
ITEMS_PATH = os.path.join(ROOT, "data", "items.json")


def _load_items() -> dict:
    with open(ITEMS_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw["items"] if "items" in raw else raw
    return {slug: item for slug, item in items.items() if not item.get("dropped")}


class HelpPageTest(unittest.TestCase):
    """HELP-template rendert in beide talen met operationele inhoud."""

    @classmethod
    def setUpClass(cls):
        env = render._env()
        cls.html = env.get_template("help.html").render(
            page="help", page_title="Help · AI Trending", page_desc="x",
            main_class="page-help", active_tab="",
            categories=[{"id": "models", "title": {"en": "Models", "nl": "Modellen"}}],
            generated_at="2026-10-07", item_count=1)

    def test_secties_in_beide_talen(self):
        for text in ("Daily rhythm", "Dagelijks ritme",
                     "Commands", "Troubleshooting", "Problemen oplossen",
                     "Privacy and security", "Privacy en beveiliging"):
            self.assertIn(text, self.html)

    def test_operationele_inhoud(self):
        for text in ("docker compose up -d --build",
                     "--resummarize",
                     "WRITE_NETWORKS",
                     "OMNIROUTE_MODEL",
                     "data/.crawl.lock",
                     "python3 -m unittest"):
            self.assertIn(text, self.html)

    def test_help_ligt_in_layout(self):
        self.assertIn('favicon.svg', self.html)
        self.assertIn('href="/help.html"', self.html)
        self.assertIn('data-page="help"', self.html)


@unittest.skipUnless(os.path.exists(ITEMS_PATH), "data/items.json ontbreekt")
class SiteRenderTest(unittest.TestCase):
    """Volledige site-render naar een temp-dir."""

    @classmethod
    def setUpClass(cls):
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cls.config = yaml.safe_load(fh)
        cls.items = _load_items()
        cls.tmp = tempfile.mkdtemp(prefix="render_test_")
        cls.written = render.render_site(
            cls.config, cls.items, scoring.load_history(os.path.join(ROOT, "data")),
            cls.tmp, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _read(self, rel: str) -> str:
        with open(os.path.join(self.tmp, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_help_html_wordt_geschreven(self):
        self.assertIn("help.html", self.written)
        html = self._read("help.html")
        self.assertIn("help-section", html)
        self.assertIn("Dagelijks ritme", html)
        self.assertIn("Daily rhythm", html)

    def test_index_heeft_favicon_help_en_tooltips(self):
        html = self._read("index.html")
        self.assertIn('/assets/favicon.svg', html)
        self.assertIn('href="/help.html"', html)
        self.assertIn('data-tip-en=', html)
        self.assertIn('data-tip-nl=', html)
        self.assertNotIn('rel="icon" href="data:,"', html)

    def test_categoriepagina_heeft_tooltips_op_tabs(self):
        cats = [k for k in self.written if k.startswith("categories/")]
        self.assertTrue(cats)
        html = self._read(cats[0])
        self.assertIn('data-tip-en="Show this category', html)

    def test_itempagina_heeft_rating_en_bron_tips(self):
        items = [k for k in self.written if k.startswith("items/")]
        self.assertTrue(items)
        html = self._read(items[0])
        self.assertIn('data-tip-en="Rate ', html)
        self.assertIn('data-tip-en="Open the original source', html)

    def test_aantal_paginas(self):
        self.assertGreater(len(self.written), 10)


class StaticAssetsTest(unittest.TestCase):
    """Statische pagina's en assets bevatten de nieuwe elementen."""

    def _read(self, rel: str) -> str:
        with open(os.path.join(ROOT, "site", rel), encoding="utf-8") as fh:
            return fh.read()

    def test_favicon_bestaat(self):
        svg = self._read("assets/favicon.svg")
        self.assertIn("<svg", svg)
        self.assertIn("viewBox", svg)

    def test_favicon_link_in_statische_paginas(self):
        for page in ("manage.html", "search.html"):
            html = self._read(page)
            self.assertIn('/assets/favicon.svg', html, page)
            self.assertNotIn('rel="icon" href="data:,"', html, page)
            self.assertIn('href="/help.html"', html, page)

    def test_tooltips_in_statische_paginas(self):
        for page in ("manage.html", "search.html"):
            html = self._read(page)
            self.assertIn('data-tip-en=', html, page)
            self.assertIn('data-tip-nl=', html, page)
        manage = self._read("manage.html")
        self.assertIn("writes only from your home network", manage)

    def test_tooltip_engine_in_app_js(self):
        js = self._read("assets/app.js")
        self.assertIn("data-tip-en", js)
        self.assertIn("site-tip", js)
        self.assertIn("langchange", js)

    def test_i18n_heeft_not_json_en_tips(self):
        js = self._read("assets/i18n.js")
        self.assertIn("not_json", js)
        self.assertIn("tip_ref", js)
        self.assertIn("tipAttr", js)

    def test_css_voor_tooltip_en_help(self):
        css = self._read("assets/app.css")
        self.assertIn(".site-tip", css)
        self.assertIn(".help-section", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_json_foutafhandeling_in_js(self):
        for asset in ("assets/discover.js", "assets/manage.js"):
            js = self._read(asset)
            self.assertIn("res.text()", js, asset)
            self.assertIn("not_json", js, asset)


if __name__ == "__main__":
    unittest.main()
