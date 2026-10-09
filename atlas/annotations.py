"""Analyst annotations (alias, tags, note) stored outside the analysed folder (phase 8).

One JSON file per analysed root, keyed by a hash of its resolved path, in the user's
data directory. The exported JADX folder is never written to.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

MAX_ALIAS, MAX_TAGS, MAX_TAG, MAX_NOTE, MAX_CLASSES = 80, 10, 30, 2000, 5000
_LOCK = threading.Lock()


def data_dir():
    if os.environ.get("ATLAS_DATA_DIR"):
        return Path(os.environ["ATLAS_DATA_DIR"]).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "jadx-atlas"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "jadx-atlas"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "jadx-atlas"


class Annotations:
    def __init__(self, root):
        self.root = Path(root).resolve()
        directory = data_dir().resolve()
        if directory == self.root or directory.is_relative_to(self.root):
            raise ValueError("The annotations directory cannot be inside the analysed folder.")
        key = hashlib.sha256(str(self.root).encode()).hexdigest()[:32]
        self.file = directory / "annotations" / f"{key}.json"

    def load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data.get("classes", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def update(self, class_id, alias=None, tags=None, note=None):
        """Set (or clear, when everything is empty) the annotation of one class."""
        if not isinstance(class_id, str) or not class_id or len(class_id) > 1000:
            raise ValueError("Invalid class.")
        alias = (alias or "").strip()[:MAX_ALIAS]
        tags = [t.strip()[:MAX_TAG] for t in (tags or []) if isinstance(t, str) and t.strip()][:MAX_TAGS]
        note = (note or "").strip()[:MAX_NOTE]
        with _LOCK:
            classes = self.load()
            if alias or tags or note:
                if class_id not in classes and len(classes) >= MAX_CLASSES:
                    raise ValueError("Annotation limit reached.")
                classes[class_id] = {"alias": alias, "tags": tags, "note": note}
            else:
                classes.pop(class_id, None)
            self.file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic replace: a crash never leaves a half-written file.
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.file.parent, delete=False, suffix=".tmp"
            ) as handle:
                json.dump({"version": 1, "classes": classes}, handle, ensure_ascii=False, indent=1)
            os.replace(handle.name, self.file)
            return classes
