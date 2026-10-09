from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from atlas.__main__ import main
from atlas.indexer import Project
from atlas.report import build, render_markdown, to_json
from atlas.server import BASE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_check import errors  # noqa: E402

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "docs" / "schema" / "atlas-index.schema.json").read_text(encoding="utf-8")
)
GOLDEN = Path(__file__).resolve().parent / "golden" / "demo-report.md"
FIXED = "2026-01-01T00:00:00+00:00"
DEMO = BASE / "examples" / "pedidos"


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = Project(DEMO, demo=True)

    def test_json_matches_schema_with_and_without_manifest(self):
        report = build(self.demo, generated=FIXED)
        self.assertEqual(errors(report, SCHEMA), [])
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "A.java").write_text("class A {}", encoding="utf-8")
            bare = build(Project(directory), generated=FIXED)
            self.assertEqual(errors(bare, SCHEMA), [])
            self.assertIsNone(bare["manifest"])

    def test_schema_checker_rejects_broken_reports(self):
        report = build(self.demo, generated=FIXED)
        del report["findings"][0]["snippet"]
        report["intentEdges"][0]["kind"] = "teleports"
        problems = errors(report, SCHEMA)
        self.assertTrue(any("snippet" in p for p in problems))
        self.assertTrue(any("teleports" in p for p in problems))

    def test_no_absolute_path_is_exported(self):
        text = to_json(build(self.demo, generated=FIXED))
        self.assertNotIn('"root"', text)
        self.assertNotIn(str(DEMO), text)

    def test_golden_markdown(self):
        text = render_markdown(build(self.demo, generated=FIXED))
        if os.environ.get("ATLAS_UPDATE_GOLDEN") == "1":
            GOLDEN.write_text(text, encoding="utf-8")
        self.assertEqual(
            text, GOLDEN.read_text(encoding="utf-8"), "rode com ATLAS_UPDATE_GOLDEN=1 se a mudança for intencional"
        )
        for section in (
            "## Rastreabilidade",
            "## Superfície de ataque",
            "## Candidatos a achado",
            "## Caminhos possíveis",
            "## Metodologia e limitações",
            "<!-- PREENCHER",
        ):
            self.assertIn(section, text)

    def test_anonymize_hides_names_and_hosts_and_is_stable(self):
        first = build(self.demo, generated=FIXED, anonymize=True)
        second = build(self.demo, generated=FIXED, anonymize=True)
        self.assertEqual(first, second)
        data = to_json({k: v for k, v in first.items() if k != "rules"}) + render_markdown(first)
        for secret in (
            "br.exemplo.pedidos",
            "PedidoRepository",
            "CheckoutActivity",
            "api.pedidos.example",
            "pagamento.example",
        ):
            self.assertNotIn(secret, data)
        self.assertIn("pkg01", data)
        self.assertIn(".invalid", data)
        self.assertTrue(first["report"]["anonymized"])
        self.assertEqual(errors(first, SCHEMA), [])


class SecretsAndApkTests(unittest.TestCase):
    def test_secrets_masked_unless_included(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "A.java").write_text('class A { String k = "AKIAQWERTYUIOPASDFGH"; }', encoding="utf-8")
            project = Project(directory)
            masked = to_json(build(project, generated=FIXED)) + render_markdown(build(project, generated=FIXED))
            self.assertNotIn("QWERTYUIOPASDFGH", masked)
            included = build(project, generated=FIXED, include_secrets=True)
            self.assertIn("AKIAQWERTYUIOPASDFGH", render_markdown(included))
            self.assertEqual(included["report"]["secrets"], "incluídos (--include-secrets)")

    def test_apk_hash_only(self):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory, "app.apk")
            apk.write_bytes(b"PK\x03\x04 fake")
            Path(directory, "A.java").write_text("class A {}", encoding="utf-8")
            report = build(Project(directory), generated=FIXED, apk=apk)
            self.assertEqual(report["report"]["apkSha256"], hashlib.sha256(b"PK\x03\x04 fake").hexdigest())
            link = Path(directory, "link.apk")
            link.symlink_to(apk)
            with self.assertRaisesRegex(ValueError, "regular"):
                build(Project(directory), apk=link)

    def test_cli_export(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory, "report.json")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["export", str(DEMO), "--format", "json", "--out", str(out)]), 0)
            self.assertEqual(errors(json.loads(out.read_text(encoding="utf-8")), SCHEMA), [])
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                main(["export", str(DEMO), "--anonymize"])
            self.assertIn("projeto anonimizado", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
