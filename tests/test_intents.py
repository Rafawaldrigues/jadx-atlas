from pathlib import Path
import tempfile
import unittest

from atlas.indexer import Project

NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'
HEADER = "package app;\nimport android.app.Activity;\nimport android.app.PendingIntent;\nimport android.content.*;\n"


def activity(name, body, extra=""):
    return f"{HEADER}public class {name} extends Activity {{\n{body}\n}}\n{extra}"


MANIFEST = f"""<manifest {NS} package="app"><uses-sdk android:minSdkVersion="21" android:targetSdkVersion="30"/><application>
  <activity android:name=".A" android:exported="true"/>
  <activity android:name=".B" android:exported="false"/>
  <receiver android:name=".Sync" android:exported="true"><intent-filter><action android:name="app.action.SYNC"/></intent-filter></receiver>
  <activity android:name=".Viewer" android:exported="true"><intent-filter>
    <action android:name="android.intent.action.VIEW"/><category android:name="android.intent.category.BROWSABLE"/>
    <category android:name="android.intent.category.DEFAULT"/><data android:scheme="app"/></intent-filter></activity>
</application></manifest>"""

SUPPORT = {
    "app/B.java": activity("B", "void x() {}"),
    "app/C.java": activity("C", "void x() {}"),
    "app/Sync.java": HEADER
    + "public class Sync extends BroadcastReceiver { public void onReceive(Context c, Intent i) {} }\n",
    "app/Viewer.java": activity("Viewer", "void x() { getIntent().getData(); }"),
    "app/Actions.java": 'package app;\npublic class Actions { public static final String SYNC = "app.action.SYNC"; }\n',
    "AndroidManifest.xml": MANIFEST,
}


class IntentEdgeTests(unittest.TestCase):
    def project(self, body):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, source in {**SUPPORT, "app/A.java": activity("A", body)}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return Project(root)

    def edges(self, project):
        return {
            (e["source"].rsplit(".", 1)[-1], e["kind"], e["target"].rsplit(".", 1)[-1], e["via"], e["confidence"])
            for e in project.intent_edges
        }

    def unresolved(self, project):
        return project.nodes["app.A"].get("intents", {}).get("unresolved", [])

    def test_explicit_target_inline_and_variable(self):
        p = self.project(
            'void m() { startActivity(new Intent(this, B.class)); Intent i = new Intent(this, C.class); i.putExtra("k", 1); startActivityForResult(i, 3); }'
        )
        self.assertEqual(
            self.edges(p),
            {("A", "launches", "B", "startActivity", "high"), ("A", "launches", "C", "startActivityForResult", "high")},
        )

    def test_jadx_cast_on_class_literal(self):
        # Regression: JADX output is `new Intent(this, (Class<?>) B.class)`.
        p = self.project(
            "void m() { Intent i = new Intent(this, (Class<?>) B.class); startActivity(i); i.setClass(this, (Class<?>) C.class); startService(i); }"
        )
        self.assertEqual(
            self.edges(p),
            {("A", "launches", "B", "startActivity", "high"), ("A", "launches", "C", "startService", "high")},
        )

    def test_set_class_name_and_component(self):
        p = self.project(
            'void m() { Intent i = new Intent(); i.setClassName("app", "app.B"); startActivity(i);'
            " Intent j = new Intent(); j.setComponent(new ComponentName(this, C.class)); startService(j); }"
        )
        self.assertEqual(
            self.edges(p),
            {("A", "launches", "B", "startActivity", "medium"), ("A", "launches", "C", "startService", "high")},
        )

    def test_implicit_action_with_and_without_matching_filter(self):
        p = self.project(
            'void m() { sendBroadcast(new Intent("app.action.SYNC")); startActivity(new Intent("app.action.NOBODY")); }'
        )
        self.assertEqual(self.edges(p), {("A", "sends_action", "Sync", "sendBroadcast", "medium")})
        self.assertEqual([u["reason"] for u in self.unresolved(p)], ["no manifest intent-filter declares this action"])
        edge = p.intent_edges[0]
        self.assertEqual((edge["action"], edge["component"]), ("app.action.SYNC", "app.Sync"))

    def test_view_action_reaches_deep_link_handler(self):
        p = self.project('void m() { startActivity(new Intent("android.intent.action.VIEW")); }')
        self.assertIn(("A", "sends_action", "Viewer", "startActivity", "medium"), self.edges(p))
        self.assertTrue(p.nodes["app.Viewer"]["deepLinkHandler"])
        self.assertTrue(p.nodes["app.Viewer"]["readsIntent"])

    def test_constants_resolved_and_unresolved(self):
        p = self.project(
            'static final String LOCAL = "app.action.SYNC";\n'
            "void m(String dynamic) { sendBroadcast(new Intent(LOCAL)); Intent i = new Intent(); i.setAction(Actions.SYNC); sendBroadcast(i);"
            " sendBroadcast(new Intent(dynamic)); sendBroadcast(new Intent(Other.MISSING)); }"
        )
        self.assertEqual(len(p.intent_edges), 2)
        reasons = sorted(u["reason"] for u in self.unresolved(p))
        self.assertEqual(reasons, ["action not resolved (dynamic)", "action not resolved (dynamic)"])

    def test_two_intents_in_one_method_do_not_mix(self):
        p = self.project(
            'void m() { Intent a = new Intent(this, B.class); Intent b = new Intent(this, C.class); a.putExtra("x", 1); startActivity(b); startActivity(a); }'
        )
        lines = {e["target"]: e["line"] for e in p.intent_edges}
        self.assertEqual(set(lines), {"app.B", "app.C"})
        self.assertLess(lines["app.C"], lines["app.B"] + 1)
        reassigned = self.project(
            "void m() { Intent a = new Intent(this, B.class); a = new Intent(this, C.class); startActivity(a); }"
        )
        self.assertEqual(self.edges(reassigned), {("A", "launches", "C", "startActivity", "high")})

    def test_untraceable_intent_is_recorded_without_inventing_a_target(self):
        p = self.project(
            "Intent build() { return null; }\nvoid m(Intent fromCaller) { startActivity(build()); startActivity(fromCaller); }"
        )
        self.assertEqual(p.intent_edges, [])
        self.assertEqual([u["reason"] for u in self.unresolved(p)], ["Intent not traceable within the method"] * 2)

    def test_pending_intent_and_register_receiver(self):
        p = self.project(
            "void m(Context c) { PendingIntent.getActivity(c, 0, new Intent(c, B.class), 67108864);"
            ' IntentFilter f = new IntentFilter("app.REFRESH"); f.addAction(Actions.SYNC); registerReceiver(new Sync(), f); }'
        )
        self.assertEqual(
            self.edges(p),
            {
                ("A", "launches", "B", "PendingIntent.getActivity", "high"),
                ("A", "registers_receiver", "Sync", "registerReceiver", "high"),
            },
        )
        register = next(e for e in p.intent_edges if e["kind"] == "registers_receiver")
        self.assertEqual(register["actions"], ["app.REFRESH", "app.action.SYNC"])

    def test_fragment_get_activity_is_not_a_pending_intent(self):
        p = self.project("void m() { Object o = getActivity(); }\nObject getActivity() { return null; }")
        self.assertEqual(p.intent_edges, [])
        self.assertEqual(self.unresolved(p), [])

    def test_inheritance_edges_and_roles_are_untouched(self):
        p = self.project("void m() { startActivity(new Intent(this, B.class)); }")
        self.assertTrue(all(e["kind"] in {"extends", "implements"} for e in p.edges))
        self.assertEqual(p.payload["stats"]["intentEdges"]["launches"], 1)
        self.assertEqual(p.payload["intentEdges"], p.intent_edges)


if __name__ == "__main__":
    unittest.main()
