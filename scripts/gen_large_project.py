#!/usr/bin/env python3
"""Generate a synthetic JADX-like Java tree for performance and scale tests.

The output is deterministic for a given set of arguments. Every reference is
resolvable (same package, explicit import, fully qualified name or a known
Android framework import), so a correct index reports zero unresolved edges.

    python scripts/gen_large_project.py --classes 20000 --depth 8 --obfuscated 0.6
"""
from __future__ import annotations

import argparse
from itertools import count
import json
from pathlib import Path
import random
import shutil
import string
import sys

MARKER = ".atlas-generated"
FRAMEWORK = ["android.app.Activity", "android.app.Service", "android.content.BroadcastReceiver",
             "android.content.ContentProvider", "android.app.Application"]


def short_names():
    """a, b, ..., z, aa, ab, ...: lowercase only, so case-insensitive file systems never collide."""
    for length in count(1):
        for index in range(26 ** length):
            name, value = "", index
            for _ in range(length):
                value, digit = divmod(value, 26)
                name = string.ascii_lowercase[digit] + name
            # Skip Java keywords and "com", the first segment of readable packages.
            if name not in {"do", "if", "for", "int", "new", "try", "var", "com"}:
                yield name


def generate(out: Path, classes=5000, depth=6, obfuscated=0.3, packages=None, interfaces=0.1, seed=1, force=False):
    if not 0 <= obfuscated <= 1 or not 0 <= interfaces < 1:
        raise ValueError("--obfuscated e --interfaces devem estar entre 0 e 1")
    if classes < 1 or depth < 1:
        raise ValueError("--classes e --depth devem ser positivos")
    if out.exists() and any(out.iterdir()):
        if not (out / MARKER).is_file():
            raise ValueError(f"{out} não está vazia e não foi criada por este gerador; nada foi apagado")
        if not force:
            raise ValueError(f"{out} já existe; use --force para recriar")
        shutil.rmtree(out)
    rng = random.Random(seed)
    package_count = packages or max(1, classes // 200)
    obfuscated_packages = round(package_count * obfuscated)
    package_names = []
    obfuscated_package_names = short_names()
    for index in range(package_count):
        if index < obfuscated_packages:
            # The digit keeps package segments distinct from (letter-only) class names, avoiding obscured FQNs.
            package_names.append(f"{next(obfuscated_package_names)}{index % 10}.{rng.choice(string.ascii_lowercase)}")
        else:
            package_names.append(f"com.example.feature{index}")
    names_per_package = {p: short_names() for p in package_names}

    types = []  # {"package", "name", "interface", "depth", "parent", "implements", "framework"}
    class_pool, interface_pool = [], []
    for index in range(classes):
        package = package_names[index % package_count]
        is_obfuscated = package_names.index(package) < obfuscated_packages
        is_interface = bool(interface_pool or index) and rng.random() < interfaces
        name = next(names_per_package[package]) if is_obfuscated else f"{'Contract' if is_interface else 'Type'}{index:05d}"
        item = {"package": package, "name": name, "interface": is_interface, "depth": 0,
                "parent": None, "implements": [], "framework": None}
        if is_interface:
            if interface_pool and rng.random() < 0.3:
                item["implements"] = [rng.choice(interface_pool)]
            interface_pool.append(item)
        else:
            candidates = [c for c in class_pool[-500:] if c["depth"] < depth]
            if candidates and rng.random() < 0.75:
                item["parent"] = rng.choice(candidates)
                item["depth"] = item["parent"]["depth"] + 1
            elif rng.random() < 0.1:
                item["framework"] = rng.choice(FRAMEWORK)
                item["depth"] = 1
            if interface_pool:
                item["implements"] = rng.sample(interface_pool[-200:], k=min(len(interface_pool[-200:]), rng.choice((0, 0, 1, 2))))
            class_pool.append(item)
        types.append(item)

    by_package = {}
    for item in types:
        by_package.setdefault(item["package"], set()).add(item["name"])
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text(json.dumps({"seed": seed}) + "\n", encoding="utf-8")
    for item in types:
        imports, local_names = {}, by_package[item["package"]]

        def ref(target, item=item, imports=imports, local_names=local_names):
            if isinstance(target, str):
                package, name = target.rsplit(".", 1)
            else:
                package, name = target["package"], target["name"]
            if package == item["package"]:
                return name
            qualified = f"{package}.{name}"
            # Import only when the short name cannot shadow or clash; otherwise write the FQN, like JADX does.
            if name not in local_names and imports.get(name, qualified) == qualified:
                imports[name] = qualified
                return name
            return qualified

        keyword = "interface" if item["interface"] else "class"
        header = f"public {keyword} {item['name']}"
        parent = item["parent"] or item["framework"]
        if parent:
            header += f" extends {ref(parent)}"
        if item["implements"]:
            header += (" extends " if item["interface"] else " implements ") + ", ".join(ref(i) for i in item["implements"])
        lines = [f"package {item['package']};", ""]
        lines += [f"import {qualified};" for qualified in sorted(imports.values())]
        lines += ["", f"{header} {{", "}", ""]
        folder = out.joinpath(*item["package"].split("."))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{item['name']}.java").write_text("\n".join(lines), encoding="utf-8")

    short = sum(1 for item in types if len(item["name"]) <= 2)
    return {"out": str(out), "types": len(types), "interfaces": len(interface_pool), "packages": package_count,
            "maxDepth": max(item["depth"] for item in types), "shortNameFraction": round(short / len(types), 3), "seed": seed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("artifacts/large-project"))
    parser.add_argument("--classes", type=int, default=5000, help="Total de tipos (classes + interfaces)")
    parser.add_argument("--depth", type=int, default=6, help="Profundidade máxima de herança")
    parser.add_argument("--obfuscated", type=float, default=0.3, help="Fração de pacotes com nomes ofuscados (0 a 1)")
    parser.add_argument("--packages", type=int, help="Número de pacotes (padrão: classes / 200)")
    parser.add_argument("--interfaces", type=float, default=0.1, help="Fração aproximada de interfaces")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="Recria a pasta se ela foi gerada por este script")
    args = parser.parse_args(argv)
    try:
        summary = generate(args.out, args.classes, args.depth, args.obfuscated, args.packages, args.interfaces, args.seed, args.force)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    json.dump(summary, sys.stdout)
    print()


if __name__ == "__main__":
    main()
