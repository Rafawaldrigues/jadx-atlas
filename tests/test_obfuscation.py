import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from atlas.annotations import Annotations
from atlas.indexer import Project, parse_file

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_large_project import generate  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jadx-1.5.6"
NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'


class ObfuscationMetricTests(unittest.TestCase):
    def level(self, obfuscated):
        with tempfile.TemporaryDirectory() as directory:
            generate(Path(directory) / "out", classes=300, depth=4, obfuscated=obfuscated, packages=10, seed=5)
            return Project(Path(directory) / "out").payload["stats"]["obfuscation"]

    def test_low_and_high(self):
        low, high = self.level(0.0), self.level(0.9)
        self.assertEqual((low["level"], low["shortNames"]), ("low", 0))
        self.assertEqual(high["level"], "high")
        self.assertGreater(high["fraction"], 0.4)


class JadxCommentTests(unittest.TestCase):
    """Real JADX 1.5.6 output (see tests/fixtures/jadx-1.5.6/README.md)."""

    def parse(self, name):
        declarations, _ = parse_file((FIXTURES / name).read_bytes(), name)
        return declarations[0]

    def test_compiled_from(self):
        node = self.parse("compiled_from.java")
        self.assertEqual((node["id"], node["sourceFile"]), ("p.b", "Helper.java"))
        self.assertNotIn("originalName", node)

    def test_renamed_with_reason_and_compiled_from(self):
        node = self.parse("renamed_with_reason.java")
        self.assertEqual((node["id"], node["originalName"], node["sourceFile"]), ("p.Helper2", "p.b", "Helper.java"))

    def test_renamed_by_deobfuscation(self):
        node = self.parse("renamed_deobf.java")
        self.assertEqual((node["id"], node["originalName"]), ("p000a.C0000a", "a.a"))

    def test_older_jadx_format_without_prefix(self):
        declarations, _ = parse_file(
            b"package q;\n/* renamed from: q.a */\n/* compiled from: Net.java */\nclass C0001a {}", "C.java"
        )
        self.assertEqual((declarations[0]["originalName"], declarations[0]["sourceFile"]), ("q.a", "Net.java"))

    def test_r8_top_level_dollar_class_links_to_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "br/atlas/testapp").mkdir(parents=True)
            (root / "br/atlas/testapp/Outer$InnerReceiver.java").write_bytes(
                (FIXTURES / "r8_toplevel_dollar.java").read_bytes()
            )
            (root / "AndroidManifest.xml").write_text(
                f'<manifest {NS} package="br.atlas.testapp"><application><receiver android:name="br.atlas.testapp.Outer$InnerReceiver" android:exported="true"/></application></manifest>',
                encoding="utf-8",
            )
            project = Project(root)
            component = project.payload["manifest"]["components"][0]
            self.assertEqual(component["class"], "br.atlas.testapp.Outer$InnerReceiver")
            self.assertEqual(component["roleCheck"], "high")

    def test_deobfuscated_component_links_through_original_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "p").mkdir()
            (root / "p/C0001a.java").write_text(
                "package p;\n/* JADX INFO: renamed from: p.a */\npublic class C0001a extends android.app.Activity {}\n",
                encoding="utf-8",
            )
            (root / "AndroidManifest.xml").write_text(
                f'<manifest {NS} package="p"><application><activity android:name=".a" android:exported="true"/></application></manifest>',
                encoding="utf-8",
            )
            self.assertEqual(Project(root).payload["manifest"]["components"][0]["class"], "p.C0001a")


class LibraryAndSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        files = {
            "com/google/app/Main.java": 'package com.google.app;\npublic class Main extends android.app.Activity { String u = "https://api.example.com/v2"; }\n',
            "com/google/gson/Gson.java": "package com.google.gson;\npublic class Gson {}\n",
            "okhttp3/Call.java": 'package okhttp3;\npublic class Call { void m() throws Exception { javax.crypto.Cipher.getInstance("AES"); } }\n',
            "AndroidManifest.xml": f'<manifest {NS} package="com.google.app"><application><activity android:name=".Main" android:exported="true"/></application></manifest>',
        }
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        cls.project = Project(root)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_libraries_marked_but_never_the_app_package(self):
        nodes = self.project.nodes
        self.assertTrue(nodes["com.google.gson.Gson"].get("library"))
        self.assertTrue(nodes["okhttp3.Call"].get("library"))
        self.assertNotIn("library", nodes["com.google.app.Main"])  # app package wins over the com.google. prefix

    def test_search_fields(self):
        def first(query):
            return [(r["id"], r["field"]) for r in self.project.search(query)["results"]][:1]

        self.assertEqual(first("gson"), [("com.google.gson.Gson", "name")])
        self.assertEqual(first("api.example"), [("com.google.app.Main", "string")])
        self.assertEqual(first("crypto-ecb"), [("okhttp3.Call", "rule")])
        self.assertEqual(first("activity"), [("com.google.app.Main", "role")])
        with self.assertRaises(ValueError):
            self.project.search("a")


class AnnotationTests(unittest.TestCase):
    def test_persist_outside_folder_and_never_touch_it(self):
        with tempfile.TemporaryDirectory() as analysed, tempfile.TemporaryDirectory() as data:
            Path(analysed, "A.java").write_text("class A {}", encoding="utf-8")
            before = sorted(p.name for p in Path(analysed).rglob("*"))
            with mock.patch.dict(os.environ, {"ATLAS_DATA_DIR": data}):
                store = Annotations(analysed)
                store.update("A", alias="Login", tags=["auth", " "], note="ver token")
                self.assertEqual(
                    Annotations(analysed).load()["A"], {"alias": "Login", "tags": ["auth"], "note": "ver token"}
                )
                store.update("A")  # all empty: removes the entry
                self.assertEqual(Annotations(analysed).load(), {})
                self.assertTrue(str(store.file).startswith(data))
            self.assertEqual(sorted(p.name for p in Path(analysed).rglob("*")), before)

    def test_refuses_data_dir_inside_analysed_folder(self):
        with tempfile.TemporaryDirectory() as analysed:
            with mock.patch.dict(os.environ, {"ATLAS_DATA_DIR": str(Path(analysed, "notes"))}):
                with self.assertRaisesRegex(ValueError, "inside the analysed folder"):
                    Annotations(analysed)

    def test_limits(self):
        with tempfile.TemporaryDirectory() as analysed, tempfile.TemporaryDirectory() as data:
            with mock.patch.dict(os.environ, {"ATLAS_DATA_DIR": data}):
                saved = Annotations(analysed).update(
                    "A", alias="x" * 500, tags=[f"t{i}" for i in range(50)], note="n" * 9000
                )["A"]
                self.assertEqual((len(saved["alias"]), len(saved["tags"]), len(saved["note"])), (80, 10, 2000))
                self.assertTrue(json.loads(Annotations(analysed).file.read_text(encoding="utf-8"))["classes"])


if __name__ == "__main__":
    unittest.main()
