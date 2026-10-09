from pathlib import Path
import tempfile
import time
import unittest

from atlas.indexer import Project
from atlas.rules import load_rules

NS = 'xmlns:android="http://schemas.android.com/apk/res/android"'


def java(body, imports="", extra=""):
    lines = "".join(f"import {i};\n" for i in imports.split()) if imports else ""
    return f"package app;\n{lines}\npublic class A {{\n{body}\n}}\n{extra}"


# rule id -> (positive source, negative source, expected confidence of the positive)
CASES = {
    "webview-javascript-enabled": (
        java("void m(WebSettings s) { s.setJavaScriptEnabled(true); }", "android.webkit.WebSettings"),
        java("void m(WebSettings s) { s.setJavaScriptEnabled(false); }", "android.webkit.WebSettings"),
        "high",
    ),
    "webview-javascript-interface": (
        java('void m(WebView w, Object o) { w.addJavascriptInterface(o, "bridge"); }', "android.webkit.WebView"),
        java(
            'void m(Other w, Object o) { w.addJavascriptInterface(o, "bridge"); }',
            "android.webkit.WebView",
            "class Other { void addJavascriptInterface(Object o, String n) {} }",
        ),
        "high",
    ),
    "webview-file-access": (
        java("void m(WebSettings s) { s.setAllowFileAccess(true); }", "android.webkit.WebSettings"),
        java("void m(WebSettings s) { s.setAllowFileAccess(false); }", "android.webkit.WebSettings"),
        "high",
    ),
    "webview-file-access-from-file-urls": (
        java("void m(WebSettings s) { s.setAllowFileAccessFromFileURLs(true); }", "android.webkit.WebSettings"),
        java("void m(WebSettings s) { s.setAllowFileAccessFromFileURLs(false); }", "android.webkit.WebSettings"),
        "high",
    ),
    "webview-universal-access-from-file-urls": (
        java("void m(WebSettings s) { s.setAllowUniversalAccessFromFileURLs(true); }", "android.webkit.WebSettings"),
        java("void m(WebSettings s) { s.setAllowUniversalAccessFromFileURLs(false); }", "android.webkit.WebSettings"),
        "high",
    ),
    "webview-debugging-enabled": (
        java("void m() { WebView.setWebContentsDebuggingEnabled(true); }", "android.webkit.WebView"),
        java("void m() { WebView.setWebContentsDebuggingEnabled(false); }", "android.webkit.WebView"),
        "high",
    ),
    "webview-loadurl-dynamic": (
        java("void m(WebView w, String url) { w.loadUrl(url); }", "android.webkit.WebView"),
        java('void m(WebView w) { w.loadUrl("https://app.example/index.html"); }', "android.webkit.WebView"),
        "high",
    ),
    "tls-trustmanager-accepts-all": (
        java(
            "TrustManager[] m() { return new TrustManager[]{ new X509TrustManager() {\n"
            "  public void checkClientTrusted(X509Certificate[] c, String a) {}\n"
            "  public void checkServerTrusted(X509Certificate[] c, String a) {}\n"
            "  public X509Certificate[] getAcceptedIssuers() { return null; } } }; }",
            "javax.net.ssl.TrustManager javax.net.ssl.X509TrustManager java.security.cert.X509Certificate",
        ),
        java(
            "TrustManager[] m() { return new TrustManager[]{ new X509TrustManager() {\n"
            "  public void checkServerTrusted(X509Certificate[] c, String a) throws CertificateException { throw new CertificateException(); }\n"
            "  public void checkClientTrusted(X509Certificate[] c, String a) {}\n"
            "  public X509Certificate[] getAcceptedIssuers() { return null; } } }; }",
            "javax.net.ssl.TrustManager javax.net.ssl.X509TrustManager java.security.cert.X509Certificate java.security.cert.CertificateException",
        ),
        "high",
    ),
    "tls-hostname-verifier-accepts-all": (
        "package app;\nimport javax.net.ssl.HostnameVerifier;\nimport javax.net.ssl.SSLSession;\n"
        "public class A implements HostnameVerifier { public boolean verify(String h, SSLSession s) { return true; } }\n",
        "package app;\nimport javax.net.ssl.HostnameVerifier;\nimport javax.net.ssl.SSLSession;\n"
        'public class A implements HostnameVerifier { public boolean verify(String h, SSLSession s) { return h.equals("api.example"); } }\n',
        "high",
    ),
    "tls-webview-ssl-error-proceed": (
        "package app;\nimport android.webkit.*;\nimport android.net.http.SslError;\n"
        "public class A extends WebViewClient { public void onReceivedSslError(WebView v, SslErrorHandler handler, SslError e) { handler.proceed(); } }\n",
        "package app;\nimport android.webkit.*;\nimport android.net.http.SslError;\n"
        "public class A extends WebViewClient { public void onReceivedSslError(WebView v, SslErrorHandler handler, SslError e) { handler.cancel(); } }\n",
        "high",
    ),
    "tls-legacy-protocol": (
        java('void m() throws Exception { SSLContext.getInstance("SSLv3"); }', "javax.net.ssl.SSLContext"),
        java('void m() throws Exception { SSLContext.getInstance("TLS"); }', "javax.net.ssl.SSLContext"),
        "high",
    ),
    "crypto-ecb-mode": (
        java('void m() throws Exception { Cipher.getInstance("AES"); }', "javax.crypto.Cipher"),
        java('void m() throws Exception { Cipher.getInstance("AES/GCM/NoPadding"); }', "javax.crypto.Cipher"),
        "high",
    ),
    "crypto-weak-cipher": (
        java('void m() throws Exception { Cipher.getInstance("DES/CBC/PKCS5Padding"); }', "javax.crypto.Cipher"),
        java('void m() throws Exception { Cipher.getInstance("AES/CBC/PKCS5Padding"); }', "javax.crypto.Cipher"),
        "high",
    ),
    "crypto-weak-hash": (
        java('void m() throws Exception { MessageDigest.getInstance("MD5"); }', "java.security.MessageDigest"),
        java('void m() throws Exception { MessageDigest.getInstance("SHA-256"); }', "java.security.MessageDigest"),
        "high",
    ),
    "crypto-hardcoded-key": (
        java(
            'private static final String KEY = "0123456789abcdef";\nvoid m() { new SecretKeySpec(KEY.getBytes(), "AES"); new SecretKeySpec("k".getBytes(), "AES"); }',
            "javax.crypto.spec.SecretKeySpec",
        ),
        java('void m(byte[] key) { new SecretKeySpec(key, "AES"); }', "javax.crypto.spec.SecretKeySpec"),
        "high",
    ),
    "crypto-hardcoded-iv": (
        java("void m() { new IvParameterSpec(new byte[]{1, 2, 3, 4}); }", "javax.crypto.spec.IvParameterSpec"),
        java("void m(byte[] iv) { new IvParameterSpec(iv); }", "javax.crypto.spec.IvParameterSpec"),
        "high",
    ),
    "crypto-fixed-seed": (
        java('void m() { new SecureRandom("seed".getBytes()); }', "java.security.SecureRandom"),
        java("void m() { new SecureRandom(); }", "java.security.SecureRandom"),
        "high",
    ),
    "exec-runtime": (
        java('void m() throws Exception { Runtime.getRuntime().exec("id"); }'),
        java('void m(Shell shell) { shell.exec("id"); }', "", "class Shell { void exec(String c) {} }"),
        "high",
    ),
    "exec-process-builder": (
        java('void m() { new ProcessBuilder("sh"); }'),
        java('void m() { new ProcessBuilder("sh"); }', "", "class ProcessBuilder { ProcessBuilder(String s) {} }"),
        "high",
    ),
    "exec-dynamic-code-loading": (
        java(
            'void m(ClassLoader p) { new DexClassLoader("/sdcard/x.dex", "/data", null, p); }',
            "dalvik.system.DexClassLoader",
        ),
        java(
            'void m(ClassLoader p) { new DexClassLoader("/sdcard/x.dex", "/data", null, p); }',
            "",
            "class DexClassLoader { DexClassLoader(String a, String b, String c, ClassLoader d) {} }",
        ),
        "high",
    ),
    "exec-reflection": (
        java('void m() throws Exception { Class.forName("x.Y"); }'),
        java('void m(Loader l) { l.forName("x.Y"); }', "", "class Loader { void forName(String s) {} }"),
        "high",
    ),
    "storage-world-mode-constant": (
        java(
            'void m(android.content.Context c) { c.getSharedPreferences("p", android.content.Context.MODE_WORLD_READABLE); }'
        ),
        java(
            '// MODE_WORLD_READABLE is deprecated\nvoid m(android.content.Context c) { c.getSharedPreferences("p", 0); }'
        ),
        "medium",
    ),
    "storage-world-mode-literal": (
        'package app;\nimport android.app.Activity;\npublic class A extends Activity { void m() { getSharedPreferences("p", 1); } }\n',
        'package app;\nimport android.app.Activity;\npublic class A extends Activity { void m() { getSharedPreferences("p", 0); } }\n',
        "high",
    ),
    "sql-concatenation": (
        java(
            'void m(SQLiteDatabase db, String id) { db.rawQuery("select * from t where id=" + id, null); }',
            "android.database.sqlite.SQLiteDatabase",
        ),
        java(
            'void m(SQLiteDatabase db, String id) { db.rawQuery("select * from t where id=?", new String[]{id}); db.execSQL("delete from t" + " where 1"); }',
            "android.database.sqlite.SQLiteDatabase",
        ),
        "high",
    ),
    "pendingintent-mutable": (
        java(
            "void m(Context c, Intent i) { PendingIntent.getActivity(c, 0, i, 134217728); }",
            "android.app.PendingIntent android.content.Context android.content.Intent",
        ),
        java(
            "void m(Context c, Intent i) { PendingIntent.getActivity(c, 0, i, 201326592); PendingIntent.getBroadcast(c, 0, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE); }",
            "android.app.PendingIntent android.content.Context android.content.Intent",
        ),
        "high",
    ),
    "broadcast-without-permission": (
        "package app;\nimport android.app.Activity;\nimport android.content.Intent;\npublic class A extends Activity { void m(Intent i) { sendBroadcast(i); } }\n",
        'package app;\nimport android.app.Activity;\nimport android.content.Intent;\npublic class A extends Activity { void m(Intent i) { sendBroadcast(i, "app.PERMISSION"); } }\n',
        "high",
    ),
    "secret-known-format": (
        java('String k = "AKIAQWERTYUIOPASDFGH";'),
        java('String k = "AKIA_YOUR_KEY_HERE";'),
        "high",
    ),
    "secret-high-entropy": (
        java('String k = "q8Zr2LmX9vT4kP7wN1sB6yH3cF5gJ0dQaWeRtYuI";'),
        java(
            'String k = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"; String t = "a long sentence that has spaces and words";'
        ),
        "low",
    ),
    "cleartext-http-url": (
        java('String u = "http://api.example.com/v1";'),
        java('String u = "https://api.example.com/v1"; String ns = "http://schemas.android.com/apk/res/android";'),
        "medium",
    ),
}


class RuleCaseTests(unittest.TestCase):
    def project(self, files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, source in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return Project(root)

    def findings(self, source, rule_id=None, extra=None):
        project = self.project({"app/A.java": source, **(extra or {})})
        return [f for f in project.findings if rule_id is None or f["ruleId"] == rule_id]

    def test_every_rule_has_a_case(self):
        rules = {r["id"] for r in load_rules()}
        self.assertEqual(rules - set(CASES) - {"untrusted-intent-input"}, set())

    def test_positive_and_negative_cases(self):
        for rule_id, (positive, negative, confidence) in CASES.items():
            with self.subTest(rule=rule_id):
                found = self.findings(positive, rule_id)
                self.assertTrue(found, f"{rule_id}: positive case not found")
                self.assertEqual(found[0]["confidence"], confidence)
                self.assertEqual(
                    self.findings(negative, rule_id), [], f"{rule_id}: false positive in the negative case"
                )

    def test_rule_metadata_is_complete(self):
        for rule in load_rules():
            with self.subTest(rule=rule["id"]):
                for key in (
                    "title",
                    "severity",
                    "category",
                    "description",
                    "remediation",
                    "falsePositives",
                    "references",
                ):
                    self.assertTrue(rule[key] or key == "references", key)
                self.assertIn(rule["severity"], {"info", "low", "medium", "high"})
                self.assertTrue(all(r.startswith("https://developer.android.com/") for r in rule["references"]))


class ConfidenceAndContextTests(RuleCaseTests):
    def test_chained_getter_is_medium(self):
        found = self.findings(
            java("void m(WebView w) { w.getSettings().setJavaScriptEnabled(true); }", "android.webkit.WebView"),
            "webview-javascript-enabled",
        )
        self.assertEqual(found[0]["confidence"], "medium")

    def test_method_name_only_is_low(self):
        found = self.findings(
            java("void m(Object o) { java.util.function.Consumer<Object> f = x -> x.setJavaScriptEnabled(true); }"),
            "webview-javascript-enabled",
        )
        self.assertEqual([f["confidence"] for f in found], ["low"])

    def test_concatenated_argument_is_low(self):
        found = self.findings(
            java('void m() throws Exception { Cipher.getInstance("AE" + "S"); }', "javax.crypto.Cipher"),
            "crypto-ecb-mode",
        )
        self.assertEqual(found[0]["confidence"], "low")

    def test_flags_in_variable_are_low(self):
        found = self.findings(
            java(
                "void m(Context c, Intent i, int flags) { PendingIntent.getActivity(c, 0, i, flags); }",
                "android.app.PendingIntent android.content.Context android.content.Intent",
            ),
            "pendingintent-mutable",
        )
        self.assertEqual(found[0]["confidence"], "low")

    def test_implicit_call_in_class_without_superclass_is_not_the_api(self):
        source = java('static Object getInstance(Object o) { return o; }\nvoid m() { getInstance("AES"); }')
        self.assertEqual(self.findings(source, "crypto-ecb-mode"), [])

    def test_implicit_call_from_inner_class_uses_outer_activity(self):
        source = (
            "package app;\nimport android.app.Activity;\nimport android.content.Intent;\n"
            "public class A extends Activity { class Inner { void m(Intent i) { sendBroadcast(i); } } }\n"
        )
        found = self.findings(source, "broadcast-without-permission")
        self.assertEqual([(f["classId"], f["confidence"]) for f in found], [("app.A.Inner", "high")])

    def test_strings_inside_annotations_are_ignored(self):
        source = java('@Deprecated(since = "q8Zr2LmX9vT4kP7wN1sB6yH3cF5gJ0dQaWeRtYuI")\nvoid m() {}')
        self.assertEqual(self.findings(source, "secret-high-entropy"), [])

    def test_shadowing_uses_innermost_declaration(self):
        extra = {"app/Foo.java": "package app; class Foo { void setJavaScriptEnabled(boolean b) {} }"}
        shadowed = java(
            "WebSettings s;\nvoid m() { Foo s = new Foo(); s.setJavaScriptEnabled(true); }",
            "android.webkit.WebSettings",
        )
        self.assertEqual(self.findings(shadowed, "webview-javascript-enabled", extra), [])
        field_only = java(
            "Foo s;\nvoid m(WebSettings s) { s.setJavaScriptEnabled(true); }", "android.webkit.WebSettings"
        )
        self.assertEqual(self.findings(field_only, "webview-javascript-enabled", extra)[0]["confidence"], "high")
        later = java(
            "void m(WebSettings w) { { Foo s = null; } WebSettings s = w; s.setJavaScriptEnabled(true); }",
            "android.webkit.WebSettings",
        )
        self.assertEqual(self.findings(later, "webview-javascript-enabled", extra)[0]["confidence"], "high")

    def test_anonymous_code_is_attributed_to_named_class(self):
        positive = CASES["tls-trustmanager-accepts-all"][0]
        project = self.project({"app/A.java": positive})
        finding = next(f for f in project.findings if f["ruleId"] == "tls-trustmanager-accepts-all")
        self.assertEqual(finding["classId"], "app.A")
        self.assertTrue(finding["inAnonymous"])
        self.assertEqual(finding["line"], 9)
        self.assertEqual(project.nodes["app.A"]["findings"]["high"], 1)
        self.assertNotIn("app.A.1", project.nodes)

    def test_named_trust_manager_through_parent_class(self):
        source = (
            "package app;\nimport javax.net.ssl.X509TrustManager;\nimport java.security.cert.X509Certificate;\n"
            "abstract class Base implements X509TrustManager {}\n"
            "public class A extends Base { public void checkServerTrusted(X509Certificate[] c, String a) {}\n"
            "public void checkClientTrusted(X509Certificate[] c, String a) {} public X509Certificate[] getAcceptedIssuers() { return null; } }\n"
        )
        found = self.findings(source, "tls-trustmanager-accepts-all")
        self.assertEqual([(f["classId"], f["inAnonymous"]) for f in found], [("app.A", False)])

    def test_override_name_in_unrelated_class_is_ignored(self):
        found = self.findings(
            java("public void checkServerTrusted(Object[] c, String a) {}"), "tls-trustmanager-accepts-all"
        )
        self.assertEqual(found, [])

    def test_secret_is_masked_everywhere(self):
        project = self.project({"app/A.java": java('String k = "AKIAQWERTYUIOPASDFGH";')})
        finding = next(f for f in project.findings if f["ruleId"] == "secret-known-format")
        self.assertEqual(finding["secret"], "AKIA…[20 chars]")
        self.assertNotIn("QWERTYUIOPASDFGH", str(project.payload))
        self.assertIn("AKIA…[20 chars]", finding["snippet"])

    def test_untrusted_intent_input_only_in_exported_components(self):
        def make(exported):
            manifest = (
                f'<manifest {NS} package="app"><uses-sdk android:minSdkVersion="21" android:targetSdkVersion="30"/>'
                f'<application><activity android:name=".A" android:exported="{exported}"/></application></manifest>'
            )
            source = (
                "package app;\nimport android.app.Activity;\npublic class A extends Activity {\n"
                '  void m() { String url = getIntent().getStringExtra("url"); getIntent().getData(); } }\n'
            )
            return self.project({"app/A.java": source, "AndroidManifest.xml": manifest})

        exported = [f for f in make("true").findings if f["ruleId"] == "untrusted-intent-input"]
        self.assertEqual(len(exported), 2)
        self.assertEqual(exported[0]["confidence"], "medium")
        self.assertEqual([f for f in make("false").findings if f["ruleId"] == "untrusted-intent-input"], [])

    def test_syntax_error_file_is_partial_and_does_not_crash(self):
        source = java(
            "void m(WebSettings s) { s.setJavaScriptEnabled(true); }\nvoid broken( { ??? }",
            "android.webkit.WebSettings",
        )
        project = self.project({"app/A.java": source})
        found = [f for f in project.findings if f["ruleId"] == "webview-javascript-enabled"]
        self.assertTrue(found)
        self.assertTrue(found[0]["partial"])

    def test_payload_shape_and_stats(self):
        project = self.project({"app/A.java": CASES["crypto-ecb-mode"][0]})
        self.assertGreaterEqual(project.payload["schemaVersion"], 4)
        self.assertEqual(project.payload["stats"]["findings"]["medium"], 1)
        finding = project.payload["findings"][0]
        self.assertEqual(
            set(finding)
            >= {"ruleId", "severity", "confidence", "classId", "file", "line", "endLine", "snippet", "inAnonymous"},
            True,
        )
        self.assertLessEqual(len(finding["snippet"]), 160)
        self.assertEqual(len(project.payload["rules"]), len(load_rules()))
        for node in project.payload["nodes"]:
            self.assertFalse(any(key.startswith("_") for key in node))

    def test_findings_can_be_disabled(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        Path(directory.name, "A.java").write_text(CASES["crypto-ecb-mode"][0], encoding="utf-8")
        project = Project(directory.name, findings=False)
        self.assertEqual(project.findings, [])

    def test_collection_cost_grows_linearly(self):
        # Machine-independent tripwire: doubling the files must roughly double the time. A quadratic step
        # (like a list membership test over all files) makes the ratio explode. Absolute numbers live in
        # the README (Performance) and scripts/bench.py.
        body = "\n".join(
            f'void m{i}(WebSettings s, String x) {{ s.setJavaScriptEnabled(x != null); Cipher.getInstance("AES/GCM/NoPadding"); String u = "text {i}"; }}'
            for i in range(150)
        )
        source = java(body, "android.webkit.WebSettings javax.crypto.Cipher")

        def timed(count):
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            for index in range(count):
                Path(directory.name, f"A{index}.java").write_text(
                    source.replace("class A", f"class A{index}"), encoding="utf-8"
                )
            best = float("inf")
            for _ in range(2):  # best of two smooths out a busy machine
                started = time.perf_counter()
                Project(directory.name, workers=1)
                best = min(best, time.perf_counter() - started)
            return best

        small, large = timed(20), timed(40)
        self.assertLess(large, small * 3, (small, large))


if __name__ == "__main__":
    unittest.main()
