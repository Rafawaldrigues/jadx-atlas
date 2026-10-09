from contextlib import redirect_stderr
from functools import partial
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from atlas.indexer import Project
from atlas.server import BASE, Handler, State

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gen_large_project import generate  # noqa: E402

DEMO = BASE / "examples" / "orders"


class Running:
    def __init__(self, state, **options):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, state=state, **options))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, route, token=None):
        headers = {"X-Atlas-Token": token} if token is not None else {}
        with urlopen(Request(self.base + route, headers=headers)) as response:
            return response.status, response.read()


class TokenTests(unittest.TestCase):
    def test_api_requires_token_but_static_files_do_not(self):
        with Running(State(Project(DEMO, demo=True)), token="s3cret-token") as server:
            for token in (None, "wrong"):
                with self.assertRaises(HTTPError) as error:
                    server.get("/api/status", token)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
            self.assertEqual(server.get("/api/status", "s3cret-token")[0], 200)
            status, body = server.get("/")
            self.assertEqual(status, 200)
            self.assertIn(b"JADX Atlas", body)
            # Host/Origin checks still apply even with a valid token.
            with self.assertRaises(HTTPError) as error:
                urlopen(
                    Request(
                        server.base + "/api/status", headers={"X-Atlas-Token": "s3cret-token", "Host": "evil.example"}
                    )
                )
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_verbose_log_has_no_query_string(self):
        stream = io.StringIO()
        with redirect_stderr(stream), Running(State(Project(DEMO, demo=True)), verbose=True) as server:
            server.get("/api/source?id=com.example.orders.ui.OrderActivity")
        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
        self.assertTrue(lines)
        self.assertEqual(lines[-1]["route"], "/api/source")
        self.assertNotIn("OrderActivity", stream.getvalue())


class StateConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.big = Path(cls.directory.name, "big")
        generate(cls.big, classes=3000, depth=4, seed=9)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def wait(self, state):
        deadline = time.monotonic() + 30
        while state.status["busy"] and time.monotonic() < deadline:
            time.sleep(0.01)

    def test_second_import_while_busy_is_refused_and_cancel_keeps_previous(self):
        original = Project(DEMO, demo=True)
        state = State(original, workers=1)
        state.start(str(self.big))
        with self.assertRaisesRegex(ValueError, "already running"):
            state.start(str(self.big))
        state.cancel.set()
        self.wait(state)
        self.assertIs(state.project, original)
        self.assertIn("cancelled", state.status["error"])

    def test_successful_import_replaces_project_and_drops_comparison(self):
        state = State(Project(DEMO, demo=True), workers=1)
        state.compare = object()
        state.start(str(self.big))
        self.wait(state)
        self.assertEqual(state.project.payload["stats"]["types"], 3000)
        self.assertIsNone(state.compare)
        self.assertEqual(state.status["revision"], 1)

    def test_failed_import_preserves_project(self):
        original = Project(DEMO, demo=True)
        state = State(original)
        state.start(str(Path(self.directory.name, "missing")))
        self.wait(state)
        self.assertIs(state.project, original)
        self.assertIsNotNone(state.status["error"])


if __name__ == "__main__":
    unittest.main()
