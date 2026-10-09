"""Parallel parsing and an on-disk cache of per-file parse results (phase 9).

Workers re-create their own tree-sitter parser (parsers are not picklable).
Results come back in submission order, so the index is identical to a serial
run. The cache is gzipped JSON (never pickle: a cache file must not be able to
execute code) in the user's cache directory, never inside the analysed folder.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

CACHE_FORMAT = 1
PARALLEL_THRESHOLD = 300  # below this, process start-up costs more than it saves


def parse_one(root, relative, findings):
    """Parse one file; the same checks as the serial path (symlink, size)."""
    from . import rules as rules_module
    from .indexer import MAX_FILE_BYTES, parse_file

    file = Path(root) / relative
    try:
        if file.is_symlink():
            raise ValueError("Symbolic link ignored")
        stat = file.stat()
        if stat.st_size > MAX_FILE_BYTES:
            raise ValueError("File larger than 8 MiB; ignored")
        classes, has_error = parse_file(file.read_bytes(), relative, rules_module.default_index() if findings else None)
        return {
            "relative": relative,
            "stat": [stat.st_mtime_ns, stat.st_size],
            "classes": classes,
            "hasError": has_error,
            "error": None,
        }
    except (OSError, ValueError) as error:
        return {"relative": relative, "stat": None, "classes": [], "hasError": False, "error": str(error)}


def _parse_chunk(root, relatives, findings):
    return [parse_one(root, relative, findings) for relative in relatives]


def default_workers(count):
    if count < PARALLEL_THRESHOLD:
        return 1
    return max(1, min(os.cpu_count() or 1, 8))


def iter_parsed(root, relatives, findings, workers, cancelled):
    """Yield parse results in input order, serially or with a process pool."""
    if workers <= 1 or len(relatives) < 2:
        for relative in relatives:
            if cancelled():
                raise _cancelled()
            yield parse_one(root, relative, findings)
        return
    size = max(16, len(relatives) // (workers * 8))
    chunks = [relatives[i : i + size] for i in range(0, len(relatives), size)]
    executor = ProcessPoolExecutor(max_workers=workers)
    try:
        futures = [executor.submit(_parse_chunk, str(root), chunk, findings) for chunk in chunks]
        for future in futures:  # submission order → deterministic index
            if cancelled():
                raise _cancelled()
            yield from future.result()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def _cancelled():
    from .indexer import Cancelled

    return Cancelled()


def cache_dir():
    if os.environ.get("ATLAS_CACHE_DIR"):
        return Path(os.environ["ATLAS_CACHE_DIR"]).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "jadx-atlas" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "jadx-atlas"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "jadx-atlas"


def global_key(findings):
    """Changes whenever the tool, the rules or the framework data change: the cache is then ignored."""
    from . import __version__

    data = Path(__file__).resolve().parent / "data"
    digest = hashlib.sha256(f"{CACHE_FORMAT}|{__version__}|{findings}".encode())
    for file in sorted(data.rglob("*.json")):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


class IndexCache:
    def __init__(self, root, findings):
        self.root = Path(root).resolve()
        directory = cache_dir().resolve()
        if directory == self.root or directory.is_relative_to(self.root):
            raise ValueError("The cache directory cannot be inside the analysed folder.")
        self.file = directory / "index" / f"{hashlib.sha256(str(self.root).encode()).hexdigest()[:32]}.json.gz"
        self.key = global_key(findings)

    def load(self):
        """{relative: result} for a cache written with the same global key, else {}."""
        try:
            with gzip.open(self.file, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, EOFError):
            return {}
        if not isinstance(data, dict) or data.get("key") != self.key or not isinstance(data.get("files"), dict):
            return {}
        return data["files"]

    def save(self, results):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.file.parent, delete=False, suffix=".tmp") as raw:
            with gzip.open(raw, "wt", encoding="utf-8", compresslevel=3) as handle:
                json.dump({"key": self.key, "files": results}, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(raw.name, self.file)
