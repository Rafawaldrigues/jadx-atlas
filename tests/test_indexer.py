from pathlib import Path
import tempfile
import unittest

from atlas.indexer import Project, parse_file, Cancelled, MAX_FILE_BYTES


class IndexerTests(unittest.TestCase):
    def project(self, files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, source in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return Project(root)

    def edges(self, project):
        return {(e["source"], e["kind"], e["target"]) for e in project.edges}

    def test_explicit_imports_same_package_and_transitive_interface(self):
        p = self.project({
            "A.java": "package app; import api.View; class A extends Base implements View {} class Base {}",
            "View.java": "package api; interface View extends Root {} interface Root {}",
        })
        self.assertEqual(self.edges(p), {("app.A", "extends", "app.Base"), ("app.A", "implements", "api.View"), ("api.View", "extends", "api.Root")})
        self.assertEqual(p.payload["stats"]["unresolved"], 0)

    def test_generics_annotations_and_comments_do_not_create_false_edges(self):
        p = self.project({"A.java": '''package app;
        // class Fake extends Missing {}
        class Base<T> {} interface I<T> {}
        class A<T extends Number> extends @Tag Base<java.util.Map<String, T>> implements I<T> {
            String text = "class Bogus extends Bad {}";
        }
        '''})
        self.assertEqual(set(p.nodes), {"app.Base", "app.I", "app.A"})
        self.assertEqual(self.edges(p), {("app.A", "extends", "app.Base"), ("app.A", "implements", "app.I")})

    def test_nested_classes_lexical_scope_and_nested_import(self):
        p = self.project({
            "Outer.java": "package one; class Outer { interface Contract {} class Inner implements Contract {} }",
            "Use.java": "package two; import one.Outer; import one.Outer.Contract; class Use extends Outer.Inner implements Contract {}",
        })
        self.assertIn(("one.Outer.Inner", "implements", "one.Outer.Contract"), self.edges(p))
        self.assertIn(("two.Use", "extends", "one.Outer.Inner"), self.edges(p))
        self.assertIn(("two.Use", "implements", "one.Outer.Contract"), self.edges(p))

    def test_explicit_import_wins_over_same_package(self):
        p = self.project({"A.java": "package p; import q.Base; class A extends Base {}", "B.java": "package p; class Base {}", "C.java": "package q; class Base {}"})
        self.assertIn(("p.A", "extends", "q.Base"), self.edges(p))

    def test_wildcard_resolution_and_ambiguity(self):
        p = self.project({
            "A.java": "package p; import a.*; class A implements I {}",
            "B.java": "package p; import a.*; import b.*; class B implements I {}",
            "I.java": "package a; interface I {}", "J.java": "package b; interface I {}",
        })
        self.assertIn(("p.A", "implements", "a.I"), self.edges(p))
        edge = next(e for e in p.edges if e["source"] == "p.B")
        self.assertEqual(edge["resolution"], "ambiguous")
        self.assertEqual(p.nodes[edge["target"]]["candidates"], ["a.I", "b.I"])

    def test_missing_short_names_do_not_join_unrelated_classes_or_default_package(self):
        p = self.project({"A.java": "package p; class A extends Missing {}", "B.java": "package q; class Missing {}", "C.java": "class Missing {}"})
        self.assertEqual(p.edges[0]["resolution"], "unresolved")
        self.assertNotEqual(p.edges[0]["target"], "q.Missing")
        self.assertNotEqual(p.edges[0]["target"], "Missing")

    def test_external_fully_qualified_and_explicit_imports(self):
        p = self.project({"A.java": "package p; import android.app.Activity; class A extends Activity implements java.io.Serializable {}"})
        self.assertEqual(self.edges(p), {("p.A", "extends", "android.app.Activity"), ("p.A", "implements", "java.io.Serializable")})
        self.assertTrue(p.nodes["android.app.Activity"]["external"])

    def test_java_lang_and_same_package_shadowing(self):
        p = self.project({"A.java": "package p; class A extends Exception {} class Exception {}", "B.java": "package q; class B extends Exception {}"})
        self.assertIn(("p.A", "extends", "p.Exception"), self.edges(p))
        self.assertIn(("q.B", "extends", "java.lang.Exception"), self.edges(p))

    def test_records_enums_annotations_and_static_nested_imports(self):
        p = self.project({"A.java": "package p; interface I {} record R(int x) implements I {} enum E implements I { A; class Nested implements I {} } @interface Tag {}", "B.java": "package q; import static p.E.Nested; class B extends Nested {}"})
        self.assertEqual(p.nodes["p.R"]["kind"], "record")
        self.assertEqual(p.nodes["p.E"]["kind"], "enum")
        self.assertEqual(p.nodes["p.Tag"]["kind"], "annotation")
        self.assertIn(("q.B", "extends", "p.E.Nested"), self.edges(p))

    def test_recovery_does_not_discard_good_declarations(self):
        p = self.project({"A.java": "package p; class Base {} class A extends Base { void broken( { ??? } }"})
        self.assertIn(("p.A", "extends", "p.Base"), self.edges(p))
        self.assertEqual(p.payload["stats"]["parseErrors"], 1)

    def test_local_and_anonymous_classes_are_not_misattributed(self):
        classes, _ = parse_file(b'class A { void m() { class Local {} Object a = new Object() {}; } class Member {} }', "A.java")
        self.assertEqual({n["id"] for n in classes}, {"A", "A.Member"})

    def test_utf8_source_locations_and_changed_file_detection(self):
        p = self.project({"A.java": "// ação e coração\npackage p;\n\npublic class A {}\n"})
        source = p.source("p.A")
        self.assertEqual(source["line"], 4)
        self.assertIn("coração", source["code"])
        (p.root / "A.java").write_text("class Changed {}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mudou"):
            p.source("p.A")

    def test_duplicate_types_reported_and_first_retained(self):
        p = self.project({"A.java": "package p; class A {}", "B.java": "package p; class A extends Missing {}"})
        self.assertEqual(p.nodes["p.A"]["path"], "A.java")
        self.assertEqual(len(p.edges), 0)
        self.assertTrue(any("duplicada" in w["message"] for w in p.payload["warnings"]))

    def test_empty_directory_is_actionable_error(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "Nenhum arquivo"):
                Project(root)

    def test_cancelled_index(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(Cancelled):
                Project(root, cancelled=lambda: True)

    def test_oversized_and_symlinked_sources_are_skipped(self):
        p = self.project({"A.java": "class A {}"})
        (p.root / "Link.java").symlink_to(p.root / "A.java")
        with (p.root / "Big.java").open("wb") as file:
            file.truncate(MAX_FILE_BYTES + 1)
        p = Project(p.root)
        self.assertEqual(p.payload["stats"]["types"], 1)
        self.assertEqual(len(p.payload["warnings"]), 2)

    def test_realistic_obfuscated_dollar_names_and_unicode(self):
        p = self.project({"a.java": "package p123a; class a$b {} class c extends a$b {} class Ação extends c {}"})
        self.assertIn(("p123a.c", "extends", "p123a.a$b"), self.edges(p))
        self.assertIn(("p123a.Ação", "extends", "p123a.c"), self.edges(p))


if __name__ == "__main__":
    unittest.main()
