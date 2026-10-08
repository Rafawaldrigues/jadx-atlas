from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest

from atlas.indexer import Project

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_large_project import generate  # noqa: E402


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_generated_project_is_fully_resolvable_and_respects_depth(self):
        summary = generate(self.root / "out", classes=400, depth=4, obfuscated=0.5, packages=6, seed=7)
        project = Project(self.root / "out")
        self.assertEqual(project.payload["stats"]["types"], 400)
        self.assertEqual(project.payload["stats"]["unresolved"], 0)
        self.assertLessEqual(summary["maxDepth"], 4)
        self.assertGreater(summary["shortNameFraction"], 0)
        parents = {
            e["source"]: e["target"]
            for e in project.edges
            if e["kind"] == "extends" and not project.nodes[e["source"]]["kind"] == "interface"
        }
        for start in parents:
            length, current = 0, start
            while current in parents:
                current, length = parents[current], length + 1
            self.assertLessEqual(length, 4)

    def test_deterministic_for_same_seed(self):
        generate(self.root / "a", classes=120, seed=3)
        generate(self.root / "b", classes=120, seed=3)

        def files(root):
            return {p.relative_to(root): p.read_bytes() for p in root.rglob("*.java")}

        self.assertEqual(files(self.root / "a"), files(self.root / "b"))

    def test_refuses_to_delete_foreign_directories(self):
        (self.root / "mine").mkdir()
        (self.root / "mine" / "keep.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "nada foi apagado"):
            generate(self.root / "mine", classes=10, force=True)
        self.assertTrue((self.root / "mine" / "keep.txt").exists())
        generate(self.root / "gen", classes=10)
        with self.assertRaisesRegex(ValueError, "--force"):
            generate(self.root / "gen", classes=10)
        generate(self.root / "gen", classes=10, force=True)


class CommandLineTests(unittest.TestCase):
    def test_bare_path_defaults_to_serve(self):
        from atlas.__main__ import build_parser, main

        args = build_parser().parse_args(["serve", "/tmp/x", "--port", "9000"])
        self.assertEqual((args.command, args.path, args.port), ("serve", "/tmp/x", 9000))
        with self.assertRaises(SystemExit) as exit_, redirect_stdout(io.StringIO()) as out:
            main(["--version"])
        self.assertIn("0.1.0", out.getvalue())
        self.assertEqual(exit_.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
