import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from atlas.indexer import Project
from atlas.paths import NOTE, FlowGraph, find_paths

ALL = {"launches", "sends_action", "registers_receiver", "uses", "extends"}


def graph(edges):
    g = FlowGraph()
    for source, target, kind, confidence in edges:
        g.add(source, target, kind, kind, 1, confidence)
    return g.freeze()


def routes(result):
    return [[result["entry"], *(s["to"] for s in p["steps"])] for p in result["paths"]]


class FindPathsTests(unittest.TestCase):
    def test_known_paths_shortest_first(self):
        g = graph(
            [
                ("E", "A", "launches", "high"),
                ("A", "T", "uses", "high"),
                ("E", "B", "uses", "high"),
                ("B", "C", "uses", "high"),
                ("C", "T", "uses", "high"),
            ]
        )
        result = find_paths(g, "E", ["T"], ALL)
        self.assertEqual(routes(result), [["E", "A", "T"], ["E", "B", "C", "T"]])
        self.assertTrue(result["approximate"])
        self.assertEqual(result["note"], NOTE)

    def test_cycles_terminate(self):
        g = graph(
            [
                ("E", "A", "uses", "high"),
                ("A", "B", "uses", "high"),
                ("B", "A", "uses", "high"),
                ("B", "E", "uses", "high"),
                ("B", "T", "uses", "high"),
            ]
        )
        self.assertEqual(routes(find_paths(g, "E", ["T"], ALL)), [["E", "A", "B", "T"]])

    def test_depth_limit(self):
        chain = [(f"N{i}", f"N{i + 1}", "uses", "high") for i in range(8)]
        g = graph(chain)
        self.assertEqual(find_paths(g, "N0", ["N8"], ALL, {"maxDepth": 7})["paths"], [])
        self.assertEqual(len(find_paths(g, "N0", ["N8"], ALL, {"maxDepth": 8})["paths"]), 1)

    def test_confidence_is_the_weakest_edge(self):
        g = graph([("E", "A", "sends_action", "medium"), ("A", "T", "uses", "high")])
        self.assertEqual(find_paths(g, "E", ["T"], ALL)["paths"][0]["confidence"], "medium")

    def test_no_path(self):
        g = graph([("E", "A", "uses", "high"), ("T", "E", "uses", "high")])
        self.assertEqual(find_paths(g, "E", ["T"], ALL)["paths"], [])

    def test_kind_filter_excludes_inheritance(self):
        g = graph([("E", "Base", "extends", "high"), ("Base", "T", "uses", "high")])
        self.assertEqual(find_paths(g, "E", ["T"], ALL - {"extends"})["paths"], [])
        self.assertEqual(routes(find_paths(g, "E", ["T"], ALL)), [["E", "Base", "T"]])

    def test_stable_order_regardless_of_insertion(self):
        edges = [
            ("E", "B", "uses", "high"),
            ("E", "A", "uses", "high"),
            ("A", "T", "uses", "high"),
            ("B", "T", "uses", "high"),
        ]
        first = find_paths(graph(edges), "E", ["T"], ALL)
        second = find_paths(graph(list(reversed(edges))), "E", ["T"], ALL)
        self.assertEqual(routes(first), routes(second))
        self.assertEqual(routes(first), [["E", "A", "T"], ["E", "B", "T"]])

    def test_same_classes_through_weaker_edge_is_not_repeated(self):
        g = graph([("E", "T", "launches", "high"), ("E", "T", "uses", "high")])
        result = find_paths(g, "E", ["T"], ALL)
        self.assertEqual([p["steps"][0]["kind"] for p in result["paths"]], ["launches"])

    def test_limits_total_and_time(self):
        g = graph(
            [("E", f"M{i}", "uses", "high") for i in range(10)] + [(f"M{i}", "T", "uses", "high") for i in range(10)]
        )
        self.assertEqual(len(find_paths(g, "E", ["T"], ALL, {"maxPathsPerTarget": 4})["paths"]), 4)
        ticks = iter(range(100))
        result = find_paths(g, "E", ["T"], ALL, {"maxSeconds": 2}, clock=lambda: next(ticks))
        self.assertEqual(result["truncated"], "time")


NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'
FILES = {
    "app/Entry.java": "package app;\nimport android.app.Activity;\nimport android.content.Intent;\n"
    "public class Entry extends Base { void go() { startActivity(new Intent(this, Middle.class)); } }\n",
    "app/Base.java": "package app;\npublic abstract class Base extends android.app.Activity { Crypto helper = null; }\n",
    "app/Middle.java": "package app;\npublic class Middle extends android.app.Activity { void m() { Crypto c = new Crypto(); } }\n",
    "app/Crypto.java": 'package app;\nimport javax.crypto.Cipher;\npublic class Crypto { void m() throws Exception { Cipher.getInstance("AES"); } }\n',
    "AndroidManifest.xml": f'<manifest {NS} package="app"><uses-sdk android:minSdkVersion="21" android:targetSdkVersion="30"/><application>'
    '<activity android:name=".Entry" android:exported="true"/><activity android:name=".Middle" android:exported="false"/></application></manifest>',
}


class ProjectPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        for name, source in FILES.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(source, encoding="utf-8")
        cls.project = Project(root)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_entries_and_targets(self):
        self.assertEqual([e["id"] for e in self.project.payload["pathEntries"]], ["app.Entry"])
        self.assertEqual(self.project.payload["pathTargets"], ["app.Crypto"])

    def test_path_with_evidence_and_inheritance_toggle(self):
        result = self.project.paths("app.Entry")
        self.assertEqual(routes(result)[0], ["app.Entry", "app.Base", "app.Crypto"])
        self.assertIn(["app.Entry", "app.Middle", "app.Crypto"], routes(result))
        launch = next(s for p in result["paths"] for s in p["steps"] if s["kind"] == "launches")
        self.assertEqual((launch["file"], launch["line"], launch["via"]), ("app/Entry.java", 4, "startActivity"))
        self.assertIn("not proof", result["text"])
        without = self.project.paths("app.Entry", inheritance=False)
        self.assertEqual(routes(without), [["app.Entry", "app.Middle", "app.Crypto"]])

    def test_explicit_target_and_errors(self):
        self.assertEqual(routes(self.project.paths("app.Entry", "app.Middle")), [["app.Entry", "app.Middle"]])
        with self.assertRaises(ValueError):
            self.project.paths("app.Nope")
        with self.assertRaises(ValueError):
            self.project.paths("app.Entry", "android.app.Activity")

    def test_uses_neighbours(self):
        uses = self.project.uses("app.Crypto")
        self.assertEqual({i["id"] for i in uses["in"]}, {"app.Base", "app.Middle"})
        self.assertEqual(self.project.payload["stats"]["usesEdges"], 2)

    def test_server_routes(self):
        from functools import partial
        from http.server import ThreadingHTTPServer
        import threading
        from urllib.request import urlopen

        from atlas.server import Handler, State

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, state=State(self.project)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/api/paths?entry=app.Entry&maxDepth=4&inheritance=0") as response:
                body = json.load(response)
            self.assertTrue(body["approximate"])
            self.assertEqual(body["limits"]["maxDepth"], 4)
            with urlopen(base + "/api/uses?id=app.Crypto") as response:
                self.assertEqual(len(json.load(response)["in"]), 2)
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/api/paths?entry=app.Nope")
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
