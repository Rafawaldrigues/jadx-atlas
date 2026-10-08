"""Local-only HTTP application. Run with `jadx-atlas [sources-directory]`."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse
import webbrowser

from .indexer import Cancelled, Project

# Package directory: holds web/ (static UI) and examples/ (demo project).
BASE = Path(__file__).resolve().parent


class State:
    def __init__(self, initial=None):
        self.lock = threading.Lock()
        self.project = initial
        self.cancel = threading.Event()
        self.status = {"busy": False, "done": 0, "total": 0, "message": "Pronto", "error": None, "revision": 0}

    def start(self, path, demo=False):
        with self.lock:
            if self.status["busy"]:
                raise ValueError("Uma importação já está em andamento.")
            self.cancel.clear()
            self.status.update(busy=True, done=0, total=0, message="Procurando arquivos .java", error=None)

        def progress(done, total, message):
            with self.lock:
                self.status.update(done=done, total=total, message=message)

        def work():
            try:
                project = Project(path, progress, self.cancel.is_set, demo=demo)
                with self.lock:
                    if self.cancel.is_set():
                        raise Cancelled()
                    self.project = project
                    self.status["revision"] += 1
                    self.status.update(message="Análise concluída", error=None)
            except Cancelled:
                with self.lock:
                    self.status.update(
                        message="Importação cancelada", error="Importação cancelada. O projeto anterior foi mantido."
                    )
            except Exception as error:
                with self.lock:
                    self.status.update(message="Falha na importação", error=str(error))
            finally:
                with self.lock:
                    self.status["busy"] = False

        threading.Thread(target=work, daemon=True).start()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, state, **kwargs):
        self.state = state
        super().__init__(*args, directory=str(BASE / "web"), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def local_request(self, write=False):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            self.json({"error": "Host não permitido."}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed}:
            self.json({"error": "Origem não permitida."}, 403)
            return False
        if write and self.headers.get_content_type() != "application/json":
            self.json({"error": "Envie application/json."}, 415)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        url = urlparse(self.path)
        try:
            if url.path == "/api/status":
                with self.state.lock:
                    status = dict(self.state.status)
                return self.json(status)
            if url.path == "/api/project":
                return self.json(self.state.project.payload if self.state.project else None)
            if url.path == "/api/source":
                if not self.state.project:
                    raise ValueError("Importe um projeto primeiro.")
                class_id = parse_qs(url.query).get("id", [""])[0]
                return self.json(self.state.project.source(class_id))
            if url.path.startswith("/api/"):
                return self.json({"error": "Rota não encontrada."}, 404)
            # Static assets only; never expose source files, directories or secrets.
            if url.path == "/":
                self.path = "/index.html"
            file = Path(self.translate_path(self.path))
            if not file.resolve().is_relative_to(BASE / "web") or not file.is_file():
                return self.json({"error": "Arquivo não encontrado."}, 404)
            super().do_GET()
        except (ValueError, OSError) as error:
            self.json({"error": str(error)}, 400)

    def do_POST(self):
        if not self.local_request(write=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16_384:
                return self.json({"error": "Pedido inválido ou muito grande."}, 413)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Pedido inválido.")
            path = urlparse(self.path).path
            if path == "/api/import":
                root = body.get("path")
                if not isinstance(root, str) or not root.strip():
                    raise ValueError("Informe o caminho da pasta exportada.")
                self.state.start(root.strip())
            elif path == "/api/demo":
                self.state.start(BASE / "examples" / "pedidos", demo=True)
            elif path == "/api/cancel":
                self.state.cancel.set()
            else:
                return self.json({"error": "Rota não encontrada."}, 404)
            self.json({"ok": True}, 202)
        except (ValueError, OSError) as error:
            self.json({"error": str(error)}, 400)


def add_arguments(parser):
    parser.add_argument("path", nargs="?", help="Pasta sources exportada pelo JADX")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")


def serve(args, parser):
    try:
        project = Project(args.path or BASE / "examples" / "pedidos", demo=not args.path)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    state = State(project)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, state=state))
    except OSError as error:
        parser.exit(1, f"Não foi possível iniciar: {error}. Tente --port 8766.\n")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"JADX Atlas disponível em {url}\nCtrl+C para encerrar.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
    finally:
        state.cancel.set()
        server.server_close()
