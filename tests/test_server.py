from functools import partial
from http.server import ThreadingHTTPServer
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import BASE, Handler, State
from atlas.indexer import Project


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = State(Project(BASE / "examples" / "pedidos", demo=True))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, state=cls.state))
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def get(self, route):
        with urlopen(self.url + route) as response:
            return json.load(response)

    def test_project_and_source_navigation(self):
        project = self.get("/api/project")
        self.assertEqual(project["stats"]["types"], 14)
        source = self.get("/api/source?id=br.exemplo.pedidos.ui.PedidoActivity")
        self.assertIn("public class PedidoActivity", source["code"].splitlines()[source["line"] - 1])

    def test_unknown_source_cannot_read_arbitrary_files(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/api/source?id=../../app.py")
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_cross_origin_import_rejected(self):
        request = Request(
            self.url + "/api/demo",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "https://example.org"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_invalid_host_rejected(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + "/api/project", headers={"Host": "malicious.example"}))
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_failed_import_preserves_project(self):
        old = self.state.project
        request = Request(
            self.url + "/api/import",
            data=json.dumps({"path": str(BASE / "does-not-exist")}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 202)
        deadline = time.monotonic() + 5
        while self.state.status["busy"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIs(self.state.project, old)
        self.assertIn("não encontrada", self.state.status["error"])

    def test_static_app_and_no_directory_listing(self):
        with urlopen(self.url) as response:
            self.assertIn(b"JADX Atlas", response.read())
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/vendor/")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_project_payload_has_schema_version_and_demo_manifest(self):
        project = self.get("/api/project")
        self.assertGreaterEqual(project["schemaVersion"], 2)
        self.assertEqual(project["manifest"]["package"], "br.exemplo.pedidos")

    def test_import_rejects_non_string_manifest(self):
        request = Request(
            self.url + "/api/import",
            data=json.dumps({"path": "/tmp", "manifest": ["x"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
