from functools import partial
import gzip
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.request import urlopen

from atlas import parallel
from atlas.indexer import Cancelled, Project

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_large_project import generate  # noqa: E402


def comparable(project):
    payload = dict(project.payload)
    payload["stats"] = {k: v for k, v in payload["stats"].items() if k != "seconds"}
    return json.dumps(payload, sort_keys=True)


class ParallelAndCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name, "app")
        generate(cls.root, classes=400, depth=5, obfuscated=0.5, packages=8, seed=21)
        (cls.root / "w/Web.java").parent.mkdir(parents=True)
        (cls.root / "w/Web.java").write_text(
            "package w;\nimport android.webkit.WebSettings;\npublic class Web { void m(WebSettings s) { s.setJavaScriptEnabled(true); } }\n",
            encoding="utf-8",
        )
        cls.cache_dir = Path(cls.directory.name, "cache")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_parallel_index_equals_serial(self):
        serial = Project(self.root, workers=1)
        pooled = Project(self.root, workers=3)
        self.assertEqual(pooled.workers, 3)
        self.assertEqual(comparable(serial), comparable(pooled))

    def test_cache_hit_and_invalidation(self):
        with mock.patch.dict(os.environ, {"ATLAS_CACHE_DIR": str(self.cache_dir)}):
            first = Project(self.root, cache=True, workers=1)
            self.assertFalse(first.cache_hit)
            second = Project(self.root, cache=True, workers=1)
            self.assertTrue(second.cache_hit)
            self.assertEqual(comparable(first), comparable(second))
            web = self.root / "w/Web.java"
            original = web.read_text(encoding="utf-8")
            try:
                web.write_text(original.replace("true", "false") + "\n// changed\n", encoding="utf-8")
                changed = Project(self.root, cache=True, workers=1)
                self.assertTrue(changed.cache_hit)  # the other files still come from the cache
                self.assertFalse([f for f in changed.findings if f["ruleId"] == "webview-javascript-enabled"])
            finally:
                web.write_text(original, encoding="utf-8")
                time.sleep(0.01)

    def test_corrupt_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as cache:
            with mock.patch.dict(os.environ, {"ATLAS_CACHE_DIR": cache}):
                store = parallel.IndexCache(self.root, True)
                store.file.parent.mkdir(parents=True)
                with gzip.open(store.file, "wt") as handle:
                    handle.write("{not json")
                project = Project(self.root, cache=True, workers=1)
                self.assertFalse(project.cache_hit)
                self.assertEqual(json.loads(gzip.open(store.file, "rt").read())["key"], store.key)  # rewritten cleanly

    def test_cache_inside_analysed_folder_is_refused(self):
        with mock.patch.dict(os.environ, {"ATLAS_CACHE_DIR": str(self.root / "cache")}):
            with self.assertRaisesRegex(ValueError, "inside the analysed folder"):
                Project(self.root, cache=True)

    def test_cancel_during_parallel_parse(self):
        calls = iter(range(10_000))
        with self.assertRaises(Cancelled):
            Project(self.root, workers=2, cancelled=lambda: next(calls) > 3)


class OnDemandRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        generate(root / "app", classes=250, depth=4, obfuscated=0.3, packages=5, seed=3)
        cls.project = Project(root / "app")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_list_pagination_is_stable_and_complete(self):
        first, second = self.project.list(0, 100), self.project.list(1, 100)
        ids = [i["id"] for i in first["items"] + second["items"] + self.project.list(2, 100)["items"]]
        self.assertEqual(first["total"], 250)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 250)
        interfaces = self.project.list(0, 1000, kind="interface")
        self.assertTrue(all(i["kind"] == "interface" for i in interfaces["items"]))

    def test_neighbors_depth_and_limit(self):
        some = next(e["source"] for e in self.project.edges if e["resolution"] == "resolved")
        one, two = self.project.neighbors(some, 1), self.project.neighbors(some, 2)
        self.assertIn(some, {n["id"] for n in one["nodes"]})
        self.assertLessEqual(len(one["nodes"]), len(two["nodes"]))
        for edge in two["edges"]:
            self.assertIn(edge["source"], {n["id"] for n in two["nodes"]})
        limited = self.project.neighbors(some, 3, limit=3)
        self.assertLessEqual(len(limited["nodes"]), 3)
        with self.assertRaises(ValueError):
            self.project.neighbors("nope")

    def test_routes_and_payload_guard(self):
        from atlas import server

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(server.Handler, state=server.State(self.project)))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            with urlopen(base + "/api/summary") as response:
                self.assertEqual(json.load(response)["stats"]["types"], 250)
            with urlopen(base + "/api/list?page=1&size=50") as response:
                self.assertEqual(len(json.load(response)["items"]), 50)
            with mock.patch.object(server, "MAX_PAYLOAD_BYTES", 1000):
                with urlopen(base + "/api/project") as response:
                    body = json.load(response)
                self.assertTrue(body["tooLarge"])
                self.assertEqual(body["summary"]["stats"]["types"], 250)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
