from pathlib import Path
import tempfile
import unittest

from atlas.indexer import Project
from atlas.roles import framework, roles

NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'


class RoleTests(unittest.TestCase):
    def project(self, files, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, source in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return Project(root, **kwargs)

    def roles_of(self, project, node_id):
        return {r["role"]: r for r in project.nodes[node_id].get("roles", [])}

    def test_obfuscated_chain_reaches_activity_through_androidx(self):
        p = self.project(
            {
                "a/b/c.java": "package a.b; public class c extends d {}",
                "a/b/d.java": "package a.b; import androidx.appcompat.app.AppCompatActivity; public abstract class d extends AppCompatActivity {}",
            }
        )
        role = self.roles_of(p, "a.b.c")["activity"]
        self.assertEqual(role["confidence"], "high")
        self.assertEqual(
            role["path"],
            [
                "a.b.c",
                "a.b.d",
                "androidx.appcompat.app.AppCompatActivity",
                "androidx.fragment.app.FragmentActivity",
                "androidx.activity.ComponentActivity",
                "androidx.core.app.ComponentActivity",
                "android.app.Activity",
            ],
        )
        self.assertEqual(p.payload["stats"]["roles"]["activity"], 2)

    def test_cycles_terminate_and_still_find_exits(self):
        p = self.project(
            {
                "x/A.java": "package x; class a extends b {} class b extends a {} class c extends a {}",
                "x/S.java": "package x; class s extends t implements java.io.Serializable {} class t extends s {}",
            }
        )
        self.assertEqual(self.roles_of(p, "x.a"), {})
        self.assertEqual(self.roles_of(p, "x.c"), {})
        self.assertIn("serializable", self.roles_of(p, "x.t"))

    def test_ambiguous_edge_lowers_confidence(self):
        two = self.project(
            {
                "p/Base.java": "package p; public class Base extends android.app.Activity {}",
                "q/Base.java": "package q; public class Base {}",
                "app/A.java": "package app; import p.*; import q.*; class A extends Base {}",
            }
        )
        role = self.roles_of(two, "app.A")["activity"]
        self.assertEqual(role["confidence"], "medium")
        self.assertIn("ambíguo", role["via"][0])
        three = self.project(
            {
                "p/Base.java": "package p; public class Base extends android.app.Activity {}",
                "q/Base.java": "package q; public class Base {}",
                "r/Base.java": "package r; public class Base {}",
                "app/A.java": "package app; import p.*; import q.*; import r.*; class A extends Base {}",
            }
        )
        self.assertEqual(self.roles_of(three, "app.A")["activity"]["confidence"], "low")

    def test_interface_inherited_through_parent_class(self):
        p = self.project(
            {
                "t/T.java": "package t; import javax.net.ssl.X509TrustManager; abstract class A implements X509TrustManager {} class B extends A {}"
            }
        )
        role = self.roles_of(p, "t.B")["trust_manager"]
        self.assertEqual(role["path"], ["t.B", "t.A", "javax.net.ssl.X509TrustManager", "javax.net.ssl.TrustManager"])
        self.assertEqual(self.roles_of(p, "t.A")["trust_manager"]["confidence"], "high")

    def test_extended_trust_manager_and_firebase_service(self):
        p = self.project(
            {
                "t/T.java": "package t; class A extends javax.net.ssl.X509ExtendedTrustManager {} "
                "class M extends com.google.firebase.messaging.FirebaseMessagingService {}"
            }
        )
        self.assertIn("trust_manager", self.roles_of(p, "t.A"))
        self.assertEqual(
            self.roles_of(p, "t.M")["service"]["path"][-2:],
            ["com.google.firebase.messaging.EnhancedIntentService", "android.app.Service"],
        )

    def test_class_outside_framework_gets_no_role(self):
        p = self.project(
            {"z/Z.java": "package z; import com.vendor.Thing; class A extends Thing {} class B extends A {}"}
        )
        self.assertEqual(self.roles_of(p, "z.B"), {})
        self.assertNotIn("roles", p.nodes["com.vendor.Thing"])

    def test_short_name_coincidence_never_assigns_a_role(self):
        p = self.project(
            {
                "x/Activity.java": "package x; public class Activity {}",
                "x/Main.java": "package x; public class Main extends Activity {}",
                "y/Other.java": "package y; public class Other extends Activity {}",
            }
        )
        self.assertEqual(self.roles_of(p, "x.Main"), {})
        self.assertEqual(self.roles_of(p, "y.Other"), {})  # unresolved short name

    def test_bundled_library_class_uses_its_own_edges(self):
        p = self.project(
            {
                "androidx/appcompat/app/AppCompatActivity.java": "package androidx.appcompat.app; public class AppCompatActivity {}",
                "app/A.java": "package app; class A extends androidx.appcompat.app.AppCompatActivity {}",
            }
        )
        self.assertEqual(self.roles_of(p, "app.A"), {})

    def test_external_framework_nodes_get_real_kind(self):
        p = self.project(
            {"k/K.java": "package k; class A extends android.os.AsyncTask implements android.os.Parcelable {}"}
        )
        self.assertEqual(p.nodes["android.os.AsyncTask"]["kind"], "class")
        self.assertTrue(p.nodes["android.os.AsyncTask"]["abstract"])
        self.assertEqual(p.nodes["android.os.Parcelable"]["kind"], "interface")
        self.assertIn("android.jar", p.nodes["android.os.Parcelable"]["framework"])

    def test_manifest_cross_check(self):
        manifest = f"""<manifest {NS} package="m"><uses-sdk android:minSdkVersion="21" android:targetSdkVersion="30"/><application>
            <service android:name=".Wrong" android:exported="true"/><activity android:name=".Right" android:exported="false"/>
        </application></manifest>"""
        p = self.project(
            {
                "m/Wrong.java": "package m; public class Wrong extends android.app.Activity {}",
                "m/Right.java": "package m; public class Right extends android.app.Activity {}",
                "m/Base.java": "package m; public abstract class Base extends android.app.Activity {}",
                "AndroidManifest.xml": manifest,
            }
        )
        checks = {c["name"]: c["roleCheck"] for c in p.payload["manifest"]["components"]}
        self.assertEqual(checks, {"m.Wrong": "missing", "m.Right": "high"})
        self.assertTrue(any("possível erro de resolução" in w["message"] for w in p.payload["warnings"]))
        self.assertTrue(p.nodes["m.Base"].get("undeclaredComponent"))
        self.assertNotIn("undeclaredComponent", p.nodes["m.Right"])
        self.assertEqual(p.payload["stats"]["undeclaredComponentClasses"], 1)


class FrameworkDataTests(unittest.TestCase):
    def test_table_is_consistent(self):
        data = framework()
        types = data["types"]
        self.assertTrue(all(info["source"] in data["sources"] for info in types.values()))
        for role in roles():
            for type_id in role["types"]:
                self.assertIn(type_id, types, f"{role['id']}: {type_id} ausente da tabela")
        # No cycles among the table's own types.
        for start in types:
            seen, stack = set(), [start]
            while stack:
                current = stack.pop()
                for parent in types.get(current, {}).get("extends", []) + types.get(current, {}).get("implements", []):
                    self.assertNotEqual(parent, start, f"ciclo em {start}")
                    if parent not in seen:
                        seen.add(parent)
                        stack.append(parent)

    def test_known_chains(self):
        types = framework()["types"]
        self.assertEqual(types["androidx.core.app.ComponentActivity"]["extends"], ["android.app.Activity"])
        self.assertEqual(
            types["androidx.legacy.content.WakefulBroadcastReceiver"]["extends"], ["android.content.BroadcastReceiver"]
        )
        self.assertEqual(types["javax.net.ssl.X509TrustManager"]["implements"], ["javax.net.ssl.TrustManager"])


if __name__ == "__main__":
    unittest.main()
