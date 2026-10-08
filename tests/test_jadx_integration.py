"""Exercise actual JADX output when Java and JADX are installed."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from atlas.indexer import Project


@unittest.skipUnless(all(shutil.which(tool) for tool in ("javac", "jar", "jadx")), "Requer javac, jar e jadx")
class JadxIntegrationTests(unittest.TestCase):
    def test_compiled_jar_exported_by_jadx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Example.java"
            source.write_text('''package sample;
import java.io.Serializable;
interface Root {}
interface View extends Root {}
class Base<T> {}
public class Example extends Base<String> implements View, Serializable {
    public static class Inner extends Example {}
    public String name() { return "ação"; }
}
''', encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
