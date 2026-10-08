from pathlib import Path
import tempfile
import time
import unittest

from atlas.indexer import Project
from atlas.manifest import ManifestError, class_id, load, locate, parse_manifest, qualify

NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'


def manifest(body, sdk='<uses-sdk android:minSdkVersion="21" android:targetSdkVersion="30"/>', app_attrs="", extra=""):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest {NS} package="com.app">
    {sdk}
    {extra}
    <application {app_attrs}>
        {body}
    </application>
</manifest>""".encode()


def one(body, resources=None, **kwargs):
    return parse_manifest(manifest(body, **kwargs), resources)["components"][0]


FILTER = '<intent-filter><action android:name="com.app.GO"/></intent-filter>'


class ExportedRuleTests(unittest.TestCase):
    def test_explicit_true_and_false(self):
        self.assertIs(one('<activity android:name=".A" android:exported="true"/>')["exported"], True)
        result = one(f'<activity android:name=".A" android:exported="false">{FILTER}</activity>')
        self.assertIs(result["exported"], False)
        self.assertEqual(result["exportedReason"], "explícito")

    def test_resource_reference_is_unknown_with_guess_from_bools(self):
        result = one('<activity android:name=".A" android:exported="@bool/exp"/>', {"exp": {"default": "true"}})
        self.assertEqual(result["exported"], "unknown")
        self.assertIs(result["exportedGuess"], True)
        self.assertIn("res/values/bools.xml", result["exportedGuessReason"])
        self.assertEqual(result["confidence"], "low")

    def test_resource_varying_by_configuration_guesses_the_exposed_value(self):
        result = one(
            '<activity android:name=".A" android:exported="@bool/exp"/>', {"exp": {"default": "false", "v31": "true"}}
        )
        self.assertEqual(result["exported"], "unknown")
        self.assertIs(result["exportedGuess"], True)
        self.assertIn("varia por configuração", result["exportedGuessReason"])

    def test_unresolved_resource_guesses_with_implicit_rule(self):
        with_filter = one(f'<activity android:name=".A" android:exported="@bool/missing">{FILTER}</activity>')
        self.assertEqual(with_filter["exported"], "unknown")
        self.assertIs(with_filter["exportedGuess"], True)
        self.assertIn("regra implícita", with_filter["exportedGuessReason"])
        without = one('<activity android:name=".A" android:exported="@bool/missing"/>')
        self.assertIs(without["exportedGuess"], False)

    def test_implicit_by_intent_filter_below_31(self):
        result = one(f'<receiver android:name=".R">{FILTER}</receiver>')
        self.assertIs(result["exported"], True)
        self.assertIn("implícito por intent-filter", result["exportedReason"])

    def test_missing_exported_with_filter_at_31_is_inconsistent_with_guess(self):
        old_devices = one(
            f'<activity android:name=".A">{FILTER}</activity>',
            sdk='<uses-sdk android:minSdkVersion="23" android:targetSdkVersion="33"/>',
        )
        self.assertEqual(old_devices["exported"], "inconsistent")
        self.assertIs(old_devices["exportedGuess"], True)
        self.assertIn("API < 31", old_devices["exportedGuessReason"])
        new_only = one(
            f'<activity android:name=".A">{FILTER}</activity>',
            sdk='<uses-sdk android:minSdkVersion="31" android:targetSdkVersion="33"/>',
        )
        self.assertEqual(new_only["exported"], "inconsistent")
        self.assertIsNone(new_only["exportedGuess"])

    def test_provider_default_depends_on_target_sdk_17(self):
        old = one(
            '<provider android:name=".P" android:authorities="a"/>',
            sdk='<uses-sdk android:minSdkVersion="9" android:targetSdkVersion="16"/>',
        )
        self.assertIs(old["exported"], True)
        new = one(
            '<provider android:name=".P" android:authorities="a"/>',
            sdk='<uses-sdk android:minSdkVersion="9" android:targetSdkVersion="17"/>',
        )
        self.assertIs(new["exported"], False)

    def test_no_filter_no_attribute_is_not_exported(self):
        result = one('<service android:name=".S"/>')
        self.assertIs(result["exported"], False)
        self.assertEqual(result["confidence"], "high")

    def test_missing_target_sdk_uses_min_sdk_with_medium_confidence(self):
        parsed = parse_manifest(
            manifest(f'<activity android:name=".A">{FILTER}</activity>', sdk='<uses-sdk android:minSdkVersion="19"/>')
        )
        self.assertIs(parsed["components"][0]["exported"], True)
        self.assertEqual(parsed["components"][0]["confidence"], "medium")
        self.assertIsNone(parsed["targetSdk"])
        self.assertTrue(any("minSdkVersion" in w for w in parsed["warnings"]))

    def test_filter_without_action_counts_for_services_only(self):
        empty = '<intent-filter><category android:name="android.intent.category.DEFAULT"/></intent-filter>'
        self.assertIs(one(f'<activity android:name=".A">{empty}</activity>')["exported"], False)
        self.assertIs(one(f'<receiver android:name=".R">{empty}</receiver>')["exported"], False)
        self.assertIs(one(f'<service android:name=".S">{empty}</service>')["exported"], True)

    def test_enabled_resource_reference_is_unknown_with_guess(self):
        result = one(
            '<activity android:name=".A" android:exported="true" android:enabled="@bool/on"/>',
            {"on": {"default": "false"}},
        )
        self.assertEqual(result["enabled"], "unknown")
        self.assertIs(result["enabledGuess"], False)

    def test_application_disabled_disables_components(self):
        result = one('<activity android:name=".A" android:exported="true"/>', app_attrs='android:enabled="false"')
        self.assertIs(result["enabled"], False)
        self.assertGreaterEqual(result["exposure"], 10)


class NameAndPermissionTests(unittest.TestCase):
    def test_relative_names(self):
        self.assertEqual(qualify(".ui.Main", "com.app"), "com.app.ui.Main")
        self.assertEqual(qualify("Main", "com.app"), "com.app.Main")
        self.assertEqual(qualify("org.lib.Main", "com.app"), "org.lib.Main")
        self.assertEqual(class_id("com.app.Outer$Inner"), "com.app.Outer.Inner")

    def test_activity_alias_maps_to_target_and_does_not_inherit_application_permission(self):
        parsed = parse_manifest(
            manifest(
                '<activity android:name=".Real" android:exported="false"/>'
                '<activity-alias android:name=".Alias" android:targetActivity=".Real" android:exported="true"/>',
                app_attrs='android:permission="com.app.P"',
            )
        )
        alias = next(c for c in parsed["components"] if c["type"] == "activity-alias")
        real = next(c for c in parsed["components"] if c["type"] == "activity")
        self.assertEqual(alias["classId"], "com.app.Real")
        self.assertIs(alias["exported"], True)
        self.assertIsNone(alias["permission"])
        self.assertEqual(real["permission"], "com.app.P")
        self.assertEqual(real["permissionSource"], "application")

    def test_protection_levels(self):
        parsed = parse_manifest(
            manifest(
                '<service android:name=".Own" android:exported="true" android:permission="com.app.SIG"/>'
                '<service android:name=".Sys" android:exported="true" android:permission="android.permission.BIND_JOB_SERVICE"/>',
                extra='<permission android:name="com.app.SIG" android:protectionLevel="signature|privileged"/>',
            )
        )
        levels = {c["name"]: c["protectionLevel"] for c in parsed["components"]}
        self.assertEqual(levels["com.app.Own"], "signature|privileged")
        self.assertEqual(levels["com.app.Sys"], "unknown")

    def test_provider_weakest_side_decides(self):
        result = one(
            '<provider android:name=".P" android:authorities="a;b" android:exported="true" android:readPermission="com.app.R"/>'
        )
        self.assertIsNone(result["permission"])
        self.assertIsNone(result["permissionSource"])
        self.assertEqual(result["provider"]["authorities"], ["a", "b"])
        self.assertEqual(result["provider"]["readPermission"], "com.app.R")
        self.assertIsNone(result["provider"]["writePermission"])
        self.assertEqual(result["exposure"], 0)

    def test_exposure_order(self):
        parsed = parse_manifest(
            manifest(
                '<activity android:name=".Hidden" android:exported="false"/>'
                '<activity android:name=".Sig" android:exported="true" android:permission="com.app.SIG"/>'
                '<activity android:name=".Maybe" android:exported="@bool/x"/>'
                '<activity android:name=".Open" android:exported="true"/>',
                extra='<permission android:name="com.app.SIG" android:protectionLevel="signature"/>',
            ),
            {"x": {"default": "true"}},
        )
        self.assertEqual(
            [c["name"].rsplit(".", 1)[1] for c in parsed["components"]], ["Open", "Sig", "Maybe", "Hidden"]
        )


class DeepLinkTests(unittest.TestCase):
    def test_deep_links_with_and_without_auto_verify(self):
        result = one(
            '<activity android:name=".D" android:exported="true">'
            '<intent-filter android:autoVerify="true"><action android:name="android.intent.action.VIEW"/>'
            '<category android:name="android.intent.category.BROWSABLE"/>'
            '<data android:scheme="https" android:host="a.example"/><data android:scheme="http"/><data android:pathPrefix="/x"/></intent-filter>'
            '<intent-filter><action android:name="android.intent.action.VIEW"/><category android:name="android.intent.category.BROWSABLE"/>'
            '<data android:scheme="app"/></intent-filter></activity>'
        )
        uris = {(link["uri"], link["autoVerify"]) for link in result["deepLinks"]}
        self.assertEqual(uris, {("http://a.example/x", True), ("https://a.example/x", True), ("app:", False)})

    def test_view_without_browsable_is_not_a_deep_link(self):
        result = one(
            '<activity android:name=".D" android:exported="true"><intent-filter><action android:name="android.intent.action.VIEW"/>'
            '<data android:scheme="https"/></intent-filter></activity>'
        )
        self.assertEqual(result["deepLinks"], [])


class HostileInputTests(unittest.TestCase):
    def assertRefusedQuickly(self, data, pattern):
        started = time.perf_counter()
        with self.assertRaisesRegex(ManifestError, pattern):
            parse_manifest(data)
        self.assertLess(time.perf_counter() - started, 2)

    def test_xxe_is_refused(self):
        self.assertRefusedQuickly(
            b'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x SYSTEM "file:///etc/passwd">]><manifest package="&x;"/>',
            "DTD ou entidades",
        )

    def test_billion_laughs_is_refused(self):
        entities = "".join(f'<!ENTITY l{i} "{f"&l{i - 1};" * 10}">' for i in range(1, 10))
        data = f'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY l0 "lol">{entities}]><manifest package="&l9;"/>'.encode()
        self.assertRefusedQuickly(data, "DTD ou entidades")

    def test_huge_deep_and_binary_manifests(self):
        self.assertRefusedQuickly(b"<manifest>" + b" " * (4 * 1024 * 1024) + b"</manifest>", "maior que 4 MiB")
        self.assertRefusedQuickly(b"<manifest>" + b"<a>" * 70 + b"</a>" * 70 + b"</manifest>", "níveis")
        self.assertRefusedQuickly(b"\x03\x00\x08\x00" + b"\x00" * 64, "binário")
        self.assertRefusedQuickly(b"<resources/>", "não é <manifest>")
        self.assertRefusedQuickly(b"<manifest", "inválido")


class ProjectIntegrationTests(unittest.TestCase):
    def make(self, files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return root

    def test_jadx_layout_links_components_and_reports_missing_classes(self):
        root = self.make(
            {
                "sources/com/app/Main.java": "package com.app; import android.app.Activity; public class Main extends Activity {}",
                "sources/other/Main.java": "package other; public class Main {}",
                "resources/AndroidManifest.xml": manifest(
                    '<activity android:name=".Main" android:exported="true"/>'
                    '<activity android:name="x.Main" android:exported="true"/>'
                    '<activity android:name=".Gone" android:exported="@bool/e"/>'
                ),
                "resources/res/values/bools.xml": '<resources><bool name="e">true</bool></resources>',
            }
        )
        project = Project(root / "sources")
        payload = project.payload
        self.assertGreaterEqual(payload["schemaVersion"], 2)
        self.assertEqual(payload["stats"]["manifest"], "found")
        self.assertEqual(payload["manifest"]["path"], "resources/AndroidManifest.xml")
        self.assertEqual(project.nodes["com.app.Main"]["component"]["type"], "activity")
        self.assertNotIn("component", project.nodes["other.Main"])  # exact match only, never short name
        classes = {c["name"]: c["class"] for c in payload["manifest"]["components"]}
        self.assertEqual(classes, {"com.app.Main": "com.app.Main", "x.Main": None, "com.app.Gone": None})
        gone = next(c for c in payload["manifest"]["components"] if c["name"] == "com.app.Gone")
        self.assertEqual((gone["exported"], gone["exportedGuess"]), ("unknown", True))
        self.assertEqual(payload["stats"]["componentsWithoutClass"], 2)
        self.assertEqual(payload["stats"]["potentiallyExported"], 1)
        self.assertTrue(any("não está nas fontes" in w["message"] for w in payload["warnings"]))

    def test_missing_manifest_keeps_previous_payload_shape(self):
        project = Project(self.make({"A.java": "class A {}"}))
        self.assertEqual(project.payload["stats"]["manifest"], "missing")
        self.assertIsNone(project.payload["manifest"])
        for key in ("name", "root", "demo", "nodes", "edges", "warnings", "stats"):
            self.assertIn(key, project.payload)
        self.assertEqual(project.payload["warnings"], [])

    def test_invalid_located_manifest_is_a_warning_but_explicit_one_is_an_error(self):
        root = self.make({"A.java": "class A {}", "AndroidManifest.xml": "<!DOCTYPE x [<!ENTITY a 'b'>]><manifest/>"})
        project = Project(root)
        self.assertEqual(project.payload["stats"]["manifest"], "invalid")
        self.assertTrue(any("DTD" in w["message"] for w in project.payload["warnings"]))
        with self.assertRaisesRegex(ValueError, "DTD"):
            Project(root, manifest=root / "AndroidManifest.xml")
        with self.assertRaisesRegex(ValueError, "não encontrado"):
            Project(root, manifest=root / "nope.xml")

    def test_symlinked_manifest_is_ignored(self):
        root = self.make({"A.java": "class A {}", "real.xml": manifest("")})
        (root / "AndroidManifest.xml").symlink_to(root / "real.xml")
        self.assertIsNone(locate(root))
        with self.assertRaises(ManifestError):
            load(root / "AndroidManifest.xml")

    def test_demo_has_attack_surface(self):
        from atlas.server import BASE

        project = Project(BASE / "examples" / "pedidos", demo=True)
        self.assertEqual(project.payload["stats"]["manifest"], "found")
        self.assertIs(project.nodes["br.exemplo.pedidos.ui.CheckoutActivity"]["component"]["exported"], True)
        self.assertEqual(project.payload["stats"]["deepLinks"], 1)


if __name__ == "__main__":
    unittest.main()
