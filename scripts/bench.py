#!/usr/bin/env python3
"""Benchmark indexing and routes on synthetic projects. Prints JSON lines.

Each size runs in a fresh subprocess so peak memory (ru_maxrss) is per measurement.

    python scripts/bench.py                      # 5k, 20k, 50k classes
    python scripts/bench.py --sizes 2000 --repeat 2
    python scripts/bench.py --path artifacts/bench-androidx/sources   # a real export
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def measure(path, workers=None, cache=False):
    import inspect
    import resource

    from atlas.indexer import Project
    from atlas.server import Handler, State

    supported = inspect.signature(Project).parameters  # older versions have neither option
    options = {k: v for k, v in {"workers": workers, "cache": cache}.items() if k in supported}
    started = time.perf_counter()
    project = Project(path, **options)
    seconds = time.perf_counter() - started
    payload = json.dumps(project.payload, ensure_ascii=False).encode()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, state=State(project)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    some = next(n["id"] for n in project.payload["nodes"] if not n["external"])
    routes = {}
    for route in (
        "/api/project",
        "/api/summary",
        f"/api/search?q={some[-6:]}",
        "/api/list?page=0",
        f"/api/neighbors?id={some}&depth=2",
        f"/api/source?id={some}",
    ):
        t = time.perf_counter()
        try:
            with urlopen(base + route) as response:
                size = len(response.read())
        except OSError as error:  # route missing in the baseline
            routes[route.split("?")[0]] = {"error": str(error)[:60]}
            continue
        routes[route.split("?")[0]] = {"ms": round((time.perf_counter() - t) * 1000, 1), "bytes": size}
    server.shutdown()
    server.server_close()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 1024 / 1024 if sys.platform == "darwin" else peak / 1024
    return {
        "types": project.payload["stats"]["types"],
        "indexSeconds": round(seconds, 2),
        "peakMemoryMB": round(peak_mb, 1),
        "payloadMB": round(len(payload) / 1024 / 1024, 2),
        "workers": getattr(project, "workers", 1),
        "cacheHit": getattr(project, "cache_hit", False),
        "routes": routes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default="5000,20000,50000")
    parser.add_argument("--path", help="Measure an existing export instead of synthetic projects")
    parser.add_argument("--workers", type=int, help="Indexing processes (default: automatic)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--cache", action="store_true", help="Use the disk cache (the 2nd repetition measures a cache hit)"
    )
    parser.add_argument("--child", nargs=2, metavar=("PATH", "WORKERS"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        path, workers = args.child
        print(json.dumps(measure(path, int(workers) or None, args.cache)))
        return 0
    from gen_large_project import generate

    targets = [(args.path, None)] if args.path else []
    for size in [int(s) for s in args.sizes.split(",") if s and not args.path]:
        out = ROOT / "artifacts" / f"bench-{size}"
        if not (out / ".atlas-generated").exists():
            generate(out, classes=size, depth=8, obfuscated=0.5, seed=11)
        targets.append((str(out), size))
    for path, _size in targets:
        for attempt in range(args.repeat):
            command = [sys.executable, __file__, "--child", path, str(args.workers or 0)] + (
                ["--cache"] if args.cache else []
            )
            result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": "0"})
            if result.returncode:
                print(json.dumps({"path": path, "error": result.stderr[-2000:]}))
                continue
            row = json.loads(result.stdout.strip().splitlines()[-1])
            print(json.dumps({"path": Path(path).name, "attempt": attempt + 1, **row}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
