from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from atlas.__main__ import main
from atlas.diff import diff, render_markdown
from atlas.indexer import Project

NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'


def manifest(components, permissions=("android.permission.INTERNET",), app=""):
    perms = "".join(f'<uses-permission android:name="{p}"/>' for p in permissions)
    return (
        f'<manifest {NS} package="app"><uses-sdk android:minSdkVersion="21" android:targetSdkVersion="30"/>{perms}'
        f"<application {app}>{components}</application></manifest>"
    )


OLD = {
    "app/A.java": "package app;\npublic class A extends android.app.Activity {}\n",
    "app/B.java": "package app;\npublic class B extends android.app.Activity {}\n",
    "app/Crypto.java": 'package app;\nimport javax.crypto.Cipher;\npublic class Crypto {\n  void m() throws Exception { Cipher.getInstance("AES"); }\n}\n',
    "o/a.java": 'package o;\npublic class a { static final String U = "https://tokens.example/v1"; static final String K = "x-auth"; }\n',
    "AndroidManifest.xml": manifest(
        '<activity android:name=".A" android:exported="false"/><activity android:name=".B" android:exported="true"/>'
    ),
}
NEW = {
    "app/A.java": "package app;\nimport androidx.appcompat.app.AppCompatActivity;\npublic class A extends AppCompatActivity {}\n",
    "app/C.java": "package app;\npublic class C extends android.app.Activity {}\n",
    "app/Crypto.java": 'package app;\nimport javax.crypto.Cipher;\nimport java.security.MessageDigest;\npublic class Crypto {\n\n  // moved down\n  void m() throws Exception { Cipher.getInstance("AES"); }\n  void h() throws Exception { MessageDigest.getInstance("MD5"); }\n}\n',
    "o/b.java": 'package o;\npublic class b { static final String U = "https://tokens.example/v1"; static final String K = "x-auth"; }\n',
    "AndroidManifest.xml": manifest(
        '<activity android:name=".A" android:exported="true"/>'
        '<activity android:name=".C" android:exported="true"><intent-filter><action android:name="android.intent.action.VIEW"/>'
        '<category android:name="android.intent.category.BROWSABLE"/><data android:scheme="app" android:host="pay"/></intent-filter></activity>',
        ("android.permission.INTERNET", "android.permission.CAMERA"),
        'android:debuggable="true"',
    ),
}


class DiffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.roots = {}
        for name, files in (("old", OLD), ("new", NEW)):
            root = Path(cls.directory.name, name)
            for path, source in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(source, encoding="utf-8")
            cls.roots[name] = root
        cls.result = diff(Project(cls.roots["old"]), Project(cls.roots["new"]))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def kinds(self):
        return {(item["kind"], item.get("name") or item.get("field")) for item in self.result["manifest"]}

    def test_manifest_changes(self):
        kinds = self.kinds()
        self.assertIn(("permission-added", "android.permission.CAMERA"), kinds)
        self.assertIn(("component-added", "app.C"), kinds)
        self.assertIn(("component-removed", "app.B"), kinds)
        self.assertIn(("exported-changed", "app.A"), kinds)
        self.assertIn(("app-flag", "debuggable"), kinds)
        changed = next(i for i in self.result["manifest"] if i["kind"] == "exported-changed")
        self.assertTrue(changed["highlight"])
        self.assertEqual(
            set(self.result["triggers"]),
            {"permission-added", "component-added", "exported-added", "deeplink-added", "app-flag"},
        )

    def test_findings_use_stable_identity(self):
        added = [(f["ruleId"], f["classId"]) for f in self.result["findingsAdded"]]
        self.assertEqual(added, [("crypto-weak-hash", "app.Crypto")])
        self.assertEqual(self.result["findingsRemoved"], [])  # the ECB call only moved lines

    def test_hierarchy_change_of_component(self):
        item = next(i for i in self.result["hierarchy"] if i["name"] == "app.A")
        self.assertEqual(
            item["old"],
            [
                "app.A",
                "android.app.Activity",
                "android.view.ContextThemeWrapper",
                "android.content.ContextWrapper",
                "android.content.Context",
            ],
        )
        self.assertEqual(
            item["new"][:3],
            ["app.A", "androidx.appcompat.app.AppCompatActivity", "androidx.fragment.app.FragmentActivity"],
        )

    def test_obfuscated_rename_is_only_a_possible_match(self):
        self.assertEqual(
            [(r["old"], r["new"], r["confidence"]) for r in self.result["renames"]], [("o.a", "o.b", "medium")]
        )
        text = render_markdown(self.result, "v1", "v2")
        self.assertIn("Possible matches", text)
        self.assertIn("not** proof of equivalence", text)
        self.assertIn("(false → true)", text)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["diff", *map(str, args)])
        return code, out.getvalue(), err.getvalue()

    def test_cli_formats_and_fail_on(self):
        code, out, _ = self.run_cli(self.roots["old"], self.roots["new"], "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schemaVersion"], 1)
        code, _, err = self.run_cli(
            self.roots["old"], self.roots["new"], "--fail-on", "exported-added,permission-added"
        )
        self.assertEqual(code, 2)
        self.assertIn("exported-added", err)
        with self.assertRaises(SystemExit):
            self.run_cli(self.roots["old"], self.roots["new"], "--fail-on", "nonsense")

    def test_no_differences_gives_empty_report_and_zero(self):
        code, out, _ = self.run_cli(
            self.roots["old"], self.roots["old"], "--fail-on", ",".join(["exported-added", "finding-added"])
        )
        self.assertEqual(code, 0)
        self.assertIn("No difference", out)
        report = Path(self.directory.name, "report.md")
        self.assertEqual(self.run_cli(self.roots["new"], self.roots["new"], "--out", report)[0], 0)
        self.assertIn("No difference", report.read_text(encoding="utf-8"))


class CompareServerTests(unittest.TestCase):
    def test_compare_route_and_diff(self):
        from functools import partial
        from http.server import ThreadingHTTPServer
        import threading
        import time
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen

        from atlas.server import Handler, State

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        roots = {}
        for name, files in (("old", OLD), ("new", NEW)):
            for path, source in files.items():
                target = Path(directory.name, name, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            roots[name] = Path(directory.name, name)
        state = State(Project(roots["new"]))
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, state=state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/api/diff")
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            request = Request(
                base + "/api/compare",
                data=json.dumps({"path": str(roots["old"])}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 202)
            deadline = time.monotonic() + 10
            while state.status["busy"] and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsNotNone(state.compare)
            with urlopen(base + "/api/diff?otherIs=old") as response:
                body = json.load(response)
            self.assertIn("exported-added", body["triggers"])
            with urlopen(base + "/api/diff?otherIs=old&format=md") as response:
                self.assertIn("# Attack-surface diff", json.load(response)["markdown"])
            with urlopen(base + "/api/diff?otherIs=new") as response:
                self.assertIn("component-removed", {i["kind"] for i in json.load(response)["manifest"]})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class FingerprintTests(unittest.TestCase):
    def test_ambiguous_fingerprints_are_not_paired(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for version, names in (("old", ("a", "b")), ("new", ("c", "d"))):
            for name in names:
                path = root / version / "o" / f"{name}.java"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f'package o;\npublic class {name} {{ static final String S = "same"; }}\n', encoding="utf-8"
                )
        result = diff(Project(root / "old"), Project(root / "new"))
        self.assertEqual(result["renames"], [])


if __name__ == "__main__":
    unittest.main()
