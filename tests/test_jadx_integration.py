"""Exercise actual JADX output when Java and JADX are installed."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from atlas.indexer import Project

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_test_apk import build, find_sdk  # noqa: E402

# Expected attack surface of tests/apk/testapp, checked by hand against `aapt dump xmltree` (docs/VALIDATION.md).
EXPECTED_APK = {
    "br.atlas.testapp.MainActivity": (True, "br.atlas.testapp.MainActivity"),
    "br.atlas.testapp.DeepLinkActivity": (True, "br.atlas.testapp.DeepLinkActivity"),
    "br.atlas.testapp.InternalActivity": (False, "br.atlas.testapp.InternalActivity"),
    "br.atlas.testapp.PaymentActivity": (False, "br.atlas.testapp.PaymentActivity"),
    "br.atlas.testapp.ResourceActivity": ("unknown", "br.atlas.testapp.ResourceActivity"),
    "br.atlas.testapp.AliasLauncher": (True, "br.atlas.testapp.InternalActivity"),
    "br.atlas.testapp.SyncService": (True, "br.atlas.testapp.SyncService"),
    "br.atlas.testapp.LocalService": (False, "br.atlas.testapp.LocalService"),
    "br.atlas.testapp.BootReceiver": (True, "br.atlas.testapp.BootReceiver"),
    "br.atlas.testapp.Outer.InnerReceiver": (True, "br.atlas.testapp.Outer.InnerReceiver"),
    "br.atlas.testapp.MissingReceiver": (True, None),
    "br.atlas.testapp.DataProvider": (True, "br.atlas.testapp.DataProvider"),
    "br.atlas.testapp.LegacyProvider": (False, "br.atlas.testapp.LegacyProvider"),
}


@unittest.skipUnless(all(shutil.which(tool) for tool in ("javac", "jar", "jadx")), "Requer javac, jar e jadx")
class JadxIntegrationTests(unittest.TestCase):
    def test_compiled_jar_exported_by_jadx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Example.java"
            source.write_text(
                """package sample;
import java.io.Serializable;
interface Root {}
interface View extends Root {}
class Base<T> {}
public class Example extends Base<String> implements View, Serializable {
    public static class Inner extends Example {}
    public String name() { return "naïve"; }
}
""",
                encoding="utf-8",
            )
            for command in [
                ["javac", "--release", "11", "-d", str(root / "classes"), str(source)],
                ["jar", "cf", str(root / "example.jar"), "-C", str(root / "classes"), "."],
                ["jadx", "--no-res", "-d", str(root / "export"), str(root / "example.jar")],
            ]:
                result = subprocess.run(command, capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            project = Project(root / "export" / "sources")
            edges = {(e["source"], e["kind"], e["target"]) for e in project.edges}
            self.assertIn(("sample.Example", "extends", "sample.Base"), edges)
            self.assertIn(("sample.Example", "implements", "sample.View"), edges)
            self.assertIn(("sample.Example", "implements", "java.io.Serializable"), edges)
            self.assertIn(("sample.Example.Inner", "extends", "sample.Example"), edges)
            self.assertIn(("sample.View", "extends", "sample.Root"), edges)
            code = project.source("sample.Example")
            self.assertIn("class Example", code["code"].splitlines()[code["line"] - 1])


@unittest.skipUnless(shutil.which("javac") and shutil.which("jadx") and find_sdk(), "Requer javac, jadx e Android SDK")
class ApkManifestIntegrationTests(unittest.TestCase):
    def test_synthetic_apk_attack_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = build(root)["apk"]
            result = subprocess.run(
                ["jadx", "-q", "-d", str(root / "export"), apk], capture_output=True, text=True, timeout=180
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            project = Project(root / "export" / "sources")
            manifest = project.payload["manifest"]
            found = {c["name"]: (c["exported"], c["class"]) for c in manifest["components"]}
            self.assertEqual(found, EXPECTED_APK)
            resource = next(c for c in manifest["components"] if c["name"].endswith("ResourceActivity"))
            self.assertIs(resource["exportedGuess"], True)
            self.assertEqual(manifest["targetSdk"], 30)
            self.assertIs(manifest["application"]["debuggable"]["value"], True)
            self.assertEqual(project.payload["stats"]["deepLinks"], 2)
            # Phase 2 acceptance: every declared activity has the activity role with high confidence, and nothing else does.
            activities = [c for c in manifest["components"] if c["type"] in {"activity", "activity-alias"}]
            self.assertTrue(all(c["roleCheck"] == "high" for c in activities))
            with_role = {
                n["id"] for n in project.nodes.values() if any(r["role"] == "activity" for r in n.get("roles", []))
            }
            self.assertEqual(with_role, {c["class"] for c in activities})
            # Phase 5 acceptance: the planted chain from the exported deep link to the TrustManager is found.
            finding = next(f for f in project.findings if f["ruleId"] == "tls-trustmanager-accepts-all")
            self.assertEqual((finding["classId"], finding["inAnonymous"]), ("br.atlas.testapp.InsecureClient", True))
            result = project.paths("br.atlas.testapp.DeepLinkActivity")
            first = [result["entry"], *(s["to"] for s in result["paths"][0]["steps"])]
            self.assertEqual(
                first,
                [
                    "br.atlas.testapp.DeepLinkActivity",
                    "br.atlas.testapp.PaymentActivity",
                    "br.atlas.testapp.InsecureClient",
                ],
            )
            self.assertEqual([s["kind"] for s in result["paths"][0]["steps"]], ["launches", "uses"])


if __name__ == "__main__":
    unittest.main()
