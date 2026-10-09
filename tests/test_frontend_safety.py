"""Static guards for the browser code: no HTML injection sinks, no inline scripts/handlers (CSP-compatible)."""

from pathlib import Path
import re
import unittest

WEB = Path(__file__).resolve().parents[1] / "atlas" / "web"
OWN_SCRIPTS = [WEB / "app.js", WEB / "logic.js"]


class FrontendSafetyTests(unittest.TestCase):
    def test_no_html_injection_sinks(self):
        sinks = re.compile(
            r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|\beval\(|new Function\(|setTimeout\(\s*['\"`]"
        )
        for script in OWN_SCRIPTS:
            with self.subTest(script=script.name):
                self.assertEqual(sinks.findall(script.read_text(encoding="utf-8")), [])

    def test_html_has_no_inline_scripts_styles_or_handlers(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
        self.assertEqual(re.findall(r"\son[a-z]+\s*=", html), [])
        self.assertEqual(re.findall(r"\sstyle\s*=", html), [])
        self.assertNotIn("<style", html)

    def test_csp_has_no_unsafe_inline(self):
        server = (WEB.parent / "server.py").read_text(encoding="utf-8")
        policy = re.search(r'"Content-Security-Policy",\s*"([^"]+)"', server.replace("\n", " "))
        csp = policy.group(1) if policy else re.search(r"default-src[^\"]+", server).group(0)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn("script-src 'self'", csp)


if __name__ == "__main__":
    unittest.main()
