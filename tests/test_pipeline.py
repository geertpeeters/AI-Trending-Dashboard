"""Unittests voor de pipeline: review-logica, lock, config, references, scoring.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pipeline import ai, config_loader, crawl, references, scoring  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
TODAY = "2026-10-07"


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pipeline_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)


class NeedsReviewTest(unittest.TestCase):
    def test_selfheal_zonder_purpose(self):
        # geen purpose.eng = ooit beoordeeld maar AI faalde/oude prompt -> altijd opnieuw
        item = {"purpose": {"en": "", "nl": ""}, "ai_reviewed_at": "2026-10-07T00:00:00+00:00"}
        self.assertTrue(crawl.needs_review(item, NOW))

    def test_vers_item_niet_opnieuw(self):
        item = {"purpose": {"en": "x", "nl": "y"}, "ai_reviewed_at": "2026-10-06T00:00:00+00:00"}
        self.assertFalse(crawl.needs_review(item, NOW))

    def test_raam_van_zeven_dagen(self):
        reviewed = (NOW - timedelta(days=7, seconds=1)).isoformat()
        item = {"purpose": {"en": "x"}, "ai_reviewed_at": reviewed}
        self.assertTrue(crawl.needs_review(item, NOW))

    def test_ooit_beoordeeld_maar_geen_tijdstempel(self):
        self.assertTrue(crawl.needs_review({"purpose": {"en": "x"}}, NOW))

    def test_ongeldige_tijdstempel(self):
        self.assertTrue(crawl.needs_review(
            {"purpose": {"en": "x"}, "ai_reviewed_at": "gisteren"}, NOW))

    def test_gedropt_item_altijd(self):
        item = {"dropped": True, "purpose": {"en": "x"},
                "ai_reviewed_at": "2026-10-07T00:00:00+00:00"}
        self.assertTrue(crawl.needs_review(item, NOW))


class ApplyReviewTest(unittest.TestCase):
    def _item(self, **extra):
        item = {"slug": "demo", "relevance": 0, "tags": [],
                "summary": {"en": "", "nl": ""}, "purpose": {"en": "", "nl": ""},
                "kind": "", "dropped": False}
        item.update(extra)
        return item

    def test_keep_drop_en_velden(self):
        items = {"demo": self._item()}
        reviews = {"demo": {
            "relevance": 8, "verdict": "keep", "tags": ["LLM", "Rag"],
            "kind": "tool",
            "purpose": {"en": "Useful for X", "nl": "Handig voor X"},
            "summary": {"en": "English summary"},
        }}
        kept, dropped = crawl.apply_ai_review(reviews, items, TODAY)
        self.assertEqual((kept, dropped), (1, 0))
        item = items["demo"]
        self.assertEqual(item["relevance"], 8)
        self.assertEqual(item["tags"], ["LLM", "Rag"])
        self.assertEqual(item["summary"]["en"], "English summary")
        self.assertEqual(item["purpose"]["en"], "Useful for X")
        self.assertEqual(item["purpose"]["nl"], "Handig voor X")
        self.assertEqual(item["kind"], "tool")
        self.assertEqual(item["ai_verdict"], "keep")
        self.assertFalse(item["dropped"])
        self.assertTrue(item["ai_reviewed_at"])

    def test_nl_valt_terug_op_en(self):
        items = {"demo": self._item()}
        reviews = {"demo": {"relevance": 6, "verdict": "keep",
                            "purpose": {"en": "Only English"}}}
        crawl.apply_ai_review(reviews, items, TODAY)
        self.assertEqual(items["demo"]["purpose"]["nl"], "Only English")

    def test_drop_markeren(self):
        items = {"demo": self._item(relevance=9)}
        reviews = {"demo": {"relevance": 1, "verdict": "drop"}}
        kept, dropped = crawl.apply_ai_review(reviews, items, TODAY)
        self.assertEqual((kept, dropped), (0, 1))
        self.assertTrue(items["demo"]["dropped"])
        self.assertEqual(items["demo"]["relevance"], 1)

    def test_manual_referenties_nooit_dropped(self):
        items = {"demo": self._item(manual=True)}
        reviews = {"demo": {"relevance": 0, "verdict": "drop"}}
        kept, dropped = crawl.apply_ai_review(reviews, items, TODAY)
        self.assertEqual((kept, dropped), (1, 0))
        self.assertFalse(items["demo"]["dropped"])
        self.assertEqual(items["demo"]["ai_verdict"], "keep")

    def test_onbekende_slug_wordt_overgeslagen(self):
        kept, dropped = crawl.apply_ai_review({"bestaat-niet": {"relevance": 5}}, {}, TODAY)
        self.assertEqual((kept, dropped), (0, 0))

    def test_ongeldige_relevance_valt_terug_op_bestaande(self):
        items = {"demo": self._item(relevance=7)}
        reviews = {"demo": {"relevance": None, "verdict": "keep"}}
        crawl.apply_ai_review(reviews, items, TODAY)
        self.assertEqual(items["demo"]["relevance"], 7)


class MergeItemTest(unittest.TestCase):
    def test_scores_nooit_lager(self):
        old = {"slug": "x", "stars": 100, "first_seen": "2026-10-01"}
        new = {"slug": "x", "stars": 40, "category_id": "models"}
        out = crawl.merge_item(old, new, TODAY)
        self.assertEqual(out["stars"], 100)
        self.assertEqual(out["first_seen"], "2026-10-01")
        self.assertEqual(out["last_seen"], TODAY)
        self.assertEqual(out["category"], "models")

    def test_defaults_en_lege_velden_behouden(self):
        out = crawl.merge_item(None, {"slug": "x", "title": "T"}, TODAY)
        for key in ("stars", "hn_points", "relevance", "tags", "dropped", "kind"):
            self.assertIn(key, out)
        self.assertEqual(out["summary"], {"en": "", "nl": ""})
        self.assertEqual(out["purpose"], {"en": "", "nl": ""})

    def test_losse_summary_wordt_dict(self):
        out = crawl.merge_item(None, {"slug": "x", "summary": "tekst"}, TODAY)
        self.assertEqual(out["summary"], {"en": "tekst", "nl": "tekst"})


class ExtractJsonTest(unittest.TestCase):
    def test_markdown_fence(self):
        self.assertEqual(ai.extract_json('```json\n[{"a": 1}]\n```'), [{"a": 1}])

    def test_prose_rond_object(self):
        value = ai.extract_json('Hier is het antwoord: {"relevance": 5} — klaar.')
        self.assertEqual(value, {"relevance": 5})

    def test_array_met_voorafgaande_tekst(self):
        value = ai.extract_json('Resultaat:\n[{"slug": "x"}]')
        self.assertEqual(value[0]["slug"], "x")

    def test_geen_json_fout(self):
        with self.assertRaises(ai.LLMError):
            ai.extract_json("geen hierarchie, alleen tekst")


class PickModelTest(unittest.TestCase):
    def test_voorkeur_gewoon_model(self):
        self.assertEqual(ai._pick_model(["ddgw/image", "auto/cheap", "gpt-4o"]),
                         "auto/cheap")

    def test_gpt4o_mini_voor_gpt4o(self):
        self.assertEqual(ai._pick_model(["gpt-4o", "gpt-4o-mini"]), "gpt-4o-mini")

    def test_overslagen_ids_vallen_terug(self):
        self.assertEqual(ai._pick_model(["ddgw/a", "aihorde/b"]), "ddgw/a")

    def test_leeg(self):
        self.assertIsNone(ai._pick_model([]))


class ReviewItemsTest(unittest.TestCase):
    class StubLLM:
        def __init__(self, payload):
            self.payload = payload
            self.calls = []

        def json_call(self, messages, *, temperature=0.2, max_tokens=2200):
            self.calls.append(messages)
            return self.payload

    def test_items_afronden_met_stub(self):
        payload = [
            {"slug": "keep-me", "relevance": "7.6", "verdict": "keep",
             "tags": "LLM, Agents", "kind": "Framework",
             "purpose_en": "Does things", "summary_en": "Long text"},
            {"slug": "drop-me", "relevance": 2},
            {"relevance": 5},                      # geen slug -> overslaan
            {"slug": "bad", "relevance": "abc", "kind": "unicorn"},
        ]
        llm = self.StubLLM(payload)
        category = {"id": "models", "title": {"en": "Models", "nl": "Modellen"}}
        items = [
            {"slug": "keep-me", "title": "A", "description": "d", "source": "hn"},
            {"slug": "drop-me", "title": "B", "description": "d", "source": "hn"},
            {"slug": "bad", "title": "C", "description": "d", "source": "hn"},
        ]
        out = ai.review_items(llm, items, category=category,
                              min_relevance=5, keywords=["llm"])
        self.assertEqual(set(out), {"keep-me", "drop-me", "bad"})
        self.assertEqual(out["keep-me"]["relevance"], 7)   # afgerond
        self.assertEqual(out["keep-me"]["tags"], ["llm", "agents"])
        self.assertEqual(out["keep-me"]["kind"], "framework")
        self.assertEqual(out["keep-me"]["purpose"]["nl"], "Does things")
        # ontbrekende verdict: keep als relevance >= min_relevance
        self.assertEqual(out["keep-me"]["verdict"], "keep")
        self.assertEqual(out["drop-me"]["verdict"], "drop")
        self.assertEqual(out["bad"]["relevance"], 0)
        self.assertEqual(out["bad"]["kind"], "")           # onbekende soort
        self.assertEqual(len(llm.calls), 1)

    def test_lege_lijst_blijft_leeg_zonder_llm(self):
        llm = self.StubLLM([])
        out = ai.review_items(llm, [], category={"id": "models", "title": {}},
                              min_relevance=5, keywords=[])
        self.assertEqual(out, {})
        self.assertEqual(llm.calls, [])


class LockTest(TempDirTest):
    def test_acquire_release(self):
        path = crawl.acquire_lock(self.tmp)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))
        crawl.release_lock(path)
        self.assertFalse(os.path.exists(path))

    def test_tweede_acquire_faalt(self):
        first = crawl.acquire_lock(self.tmp)
        self.addCleanup(crawl.release_lock, first)
        self.assertIsNone(crawl.acquire_lock(self.tmp))

    def test_dode_pid_wordt_teruggenomen(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        dead_pid = proc.pid
        path = os.path.join(self.tmp, ".crawl.lock")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pid": dead_pid, "started": "2026-10-07T00:00:00Z"}, fh)
        self.assertFalse(crawl._pid_alive(dead_pid))
        reclaimed = crawl.acquire_lock(self.tmp)
        self.assertIsNotNone(reclaimed)
        crawl.release_lock(reclaimed)

    def test_verouderde_lock_wordt_teruggenomen(self):
        path = os.path.join(self.tmp, ".crawl.lock")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": "2026-10-07T00:00:00Z"}, fh)
        old = 1801
        os.utime(path, (old, old))
        # houder is onszelf (levend), dus alleen leeftijd redt: ouder dan 30 min
        reclaimed = crawl.acquire_lock(self.tmp)
        self.assertIsNotNone(reclaimed)
        crawl.release_lock(reclaimed)

    def test_release_met_none_is_pijnloos(self):
        crawl.release_lock(None)


class ReferencesTest(TempDirTest):
    def test_toevoegen_en_persistentie(self):
        ref, err = references.add_reference(
            self.tmp, url="https://voorbeeld.nl/tool", title="Mijn tool",
            note="n", category="models", tags=["LLM"])
        self.assertEqual(err, "")
        self.assertTrue(ref["id"])
        self.assertEqual(ref["url"], "https://voorbeeld.nl/tool")
        self.assertEqual(ref["category"], "models")
        again = references.load_references(self.tmp)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["id"], ref["id"])

    def test_dubbele_url_weigeren(self):
        references.add_reference(self.tmp, url="https://voorbeeld.nl/a")
        _, err = references.add_reference(self.tmp, url="https://voorbeeld.nl/a")
        self.assertIn("bestaat al", err)

    def test_verwijderen(self):
        ref, _ = references.add_reference(self.tmp, url="https://voorbeeld.nl/b")
        self.assertTrue(references.remove_reference(self.tmp, ref["id"]))
        self.assertFalse(references.remove_reference(self.tmp, ref["id"]))
        self.assertEqual(references.load_references(self.tmp), [])

    def test_normalize_url(self):
        self.assertEqual(references.normalize_url("")[1], "url ontbreekt")
        self.assertEqual(references.normalize_url("https://a b.nl")[1],
                         "url mag geen spaties bevatten")
        self.assertEqual(references.normalize_url("notaurl")[1],
                         "ongeldig domein (verwacht bijv. https://voorbeeld.nl/...)")
        url, err = references.normalize_url("//Example.com/Pad")
        self.assertEqual(err, "")
        self.assertEqual(url, "https://Example.com/Pad")


class ConfigLoaderTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.cfg_path = os.path.join(self.tmp, "categories.yaml")
        shutil.copyfile(config_loader.CONFIG_PATH, self.cfg_path)
        self._old_changelog = config_loader.CHANGELOG_PATH
        config_loader.CHANGELOG_PATH = os.path.join(self.tmp, "changelog.jsonl")
        self.addCleanup(setattr, config_loader, "CHANGELOG_PATH", self._old_changelog)
        self.config = config_loader.load_config(self.cfg_path)
        self.cat_id = self.config["categories"][0]["id"]

    def _changelog(self):
        path = config_loader.CHANGELOG_PATH
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_add_topic_web(self):
        topic, err = config_loader.add_topic_web(
            self.config,
            {"category_id": self.cat_id, "title_en": "Test topic",
             "title_nl": "Testtopic", "keywords": ["xyzzy"]},
            config_path=self.cfg_path)
        self.assertEqual(err, "")
        self.assertEqual(topic["topic_id"], "test-topic")
        reloaded = config_loader.load_config(self.cfg_path)
        ids = [t["id"] for t in config_loader.category_by_id(reloaded, self.cat_id)["topics"]]
        self.assertIn("test-topic", ids)
        entries = self._changelog()
        self.assertEqual(entries[-1]["action"], "add_topics")
        self.assertEqual(entries[-1]["actor"], "web")
        self.assertTrue(os.path.exists(self.cfg_path + ".prev"))

    def test_add_topic_dubbel(self):
        payload = {"category_id": self.cat_id, "title_en": "Dubbel"}
        _, err = config_loader.add_topic_web(self.config, payload, config_path=self.cfg_path)
        self.assertEqual(err, "")
        # de server laadt de config per request opnieuw van schijf
        fresh = config_loader.load_config(self.cfg_path)
        _, err2 = config_loader.add_topic_web(fresh, payload, config_path=self.cfg_path)
        self.assertIn("bestaat al", err2)

    def test_add_topic_onbekende_categorie(self):
        _, err = config_loader.add_topic_web(
            self.config, {"category_id": "onzin", "title_en": "X"},
            config_path=self.cfg_path)
        self.assertEqual(err, "onbekende categorie")

    def test_remove_topic(self):
        config_loader.add_topic_web(
            self.config, {"category_id": self.cat_id, "title_en": "Wegwerp"},
            config_path=self.cfg_path)
        fresh = config_loader.load_config(self.cfg_path)
        removed, err = config_loader.remove_topic(
            fresh, self.cat_id, "wegwerp", config_path=self.cfg_path)
        self.assertEqual(err, "")
        self.assertEqual(removed["id"], "wegwerp")
        reloaded = config_loader.load_config(self.cfg_path)
        ids = [t["id"] for t in config_loader.category_by_id(reloaded, self.cat_id)["topics"]]
        self.assertNotIn("wegwerp", ids)
        self.assertEqual(self._changelog()[-1]["action"], "remove_topic")
        fresh2 = config_loader.load_config(self.cfg_path)
        _, err2 = config_loader.remove_topic(fresh2, self.cat_id, "wegwerp",
                                              config_path=self.cfg_path)
        self.assertEqual(err2, "topic niet gevonden")

    def test_load_config_valideert(self):
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            fh.write("categories:\n  - id: dubbel\n  - id: dubbel\n")
        with self.assertRaises(ValueError):
            config_loader.load_config(self.cfg_path)


class ScoringTest(TempDirTest):
    def test_trend_toeneemt_met_relevantie(self):
        args = dict(hn_points=50, star_delta=10.0, created_at="2026-10-06")
        low = scoring.trend_score(relevance=1, **args)
        high = scoring.trend_score(relevance=10, **args)
        self.assertGreater(high, low)

    def test_heuristic_blijft_in_band(self):
        bare = scoring.heuristic_relevance({})
        self.assertTrue(1 <= bare <= 10)
        hot = scoring.heuristic_relevance({
            "hn_points": 100000, "star_delta": 9999.0,
            "created_at": TODAY, "free": True})
        self.assertEqual(hot, 10)

    def test_score_items_vult_velden(self):
        items = {"a": {"slug": "a", "stars": 10, "created_at": "2026-10-06"}}
        scoring.score_items(items, [])
        self.assertIn("trend", items["a"])
        self.assertIn("star_delta", items["a"])
        self.assertIn("scored_at", items["a"])
        self.assertGreater(items["a"]["relevance"], 0)

    def test_snapshot_en_history_rondje(self):
        items = {"a": {"trend": 55.0, "stars": 7, "hn_points": 3, "relevance": 6}}
        path = scoring.write_snapshot(self.tmp, items)
        self.assertTrue(os.path.exists(path))
        history = scoring.load_history(self.tmp, days=1)
        self.assertTrue(history)
        day, payload = history[-1]
        self.assertEqual(payload["a"]["stars"], 7)


if __name__ == "__main__":
    unittest.main()
