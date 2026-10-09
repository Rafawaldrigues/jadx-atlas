"""Compare the attack surface of two versions of the same app (phase 6).

Order of trust: manifest facts (stable names) first, then findings, then the
hierarchy of declared components. Obfuscated classes are renamed between
builds; they are paired only by a structural fingerprint and always labelled
"possível correspondência" with a confidence, never asserted as equivalent.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

from .roles import framework

FAIL_ON = {
    "exported-added": "componente novo exportado ou exported false → true",
    "permission-added": "uses-permission nova",
    "component-added": "componente novo no Manifest",
    "deeplink-added": "deep link novo",
    "finding-added": "candidato a achado novo com severidade medium ou high",
    "app-flag": "debuggable/allowBackup/usesCleartextTraffic/testOnly passou a true",
}
APP_FLAGS = ("debuggable", "allowBackup", "usesCleartextTraffic", "testOnly")


def snippet_hash(snippet):
    """Line numbers move between versions; the normalised snippet text does not."""
    return hashlib.sha256(re.sub(r"\s+", " ", snippet or "").strip().encode()).hexdigest()[:12]


def looks_obfuscated(class_id):
    """Simple name of one to three characters (`a`, `bc`, `a$b`), the typical R8/ProGuard output."""
    simple = class_id.rsplit(".", 1)[-1]
    return len(simple) <= 3 or bool(re.fullmatch(r"[a-z]{1,3}(\$[a-z0-9]{1,3})*", simple))


def match_classes(old, new):
    """Pair classes that disappeared from `old` with classes that appeared in `new`.

    Returns {old id: {"id": new id, "confidence": ..., "reason": ...}} for unique fingerprint matches.
    """
    # Manifest names survive obfuscation (they are the anchors); only short, obfuscated-looking names are paired.
    anchors = set(_components(old)) | set(_components(new))
    removed = sorted(
        c for c in set(old.fingerprints) - set(new.fingerprints) if looks_obfuscated(c) and c not in anchors
    )
    added = sorted(c for c in set(new.fingerprints) - set(old.fingerprints) if looks_obfuscated(c) and c not in anchors)

    def key(fingerprint):
        return (
            fingerprint["kind"],
            fingerprint["abstract"],
            tuple(fingerprint["roles"]),
            tuple(fingerprint["externalParents"]),
            tuple(fingerprint["strings"]),
            tuple(fingerprint["findings"]),
        )

    candidates = {}
    for class_id in added:
        candidates.setdefault(key(new.fingerprints[class_id]), []).append(class_id)
    olds = {}
    for class_id in removed:
        olds.setdefault(key(old.fingerprints[class_id]), []).append(class_id)
    pairs = {}
    for fingerprint, old_ids in olds.items():
        new_ids = candidates.get(fingerprint, [])
        if len(old_ids) != 1 or len(new_ids) != 1:
            continue  # ambiguous: several classes look the same, so no pairing is claimed
        strings, roles = fingerprint[4], fingerprint[2]
        # Shared string constants are the strongest rename-stable evidence; bare shape is weak.
        confidence = "medium" if strings else "low"
        if not strings and not roles and not fingerprint[3]:
            continue  # an empty class matches anything
        pairs[old_ids[0]] = {
            "id": new_ids[0],
            "confidence": confidence,
            "reason": f"impressão estrutural igual (tipo, papéis, pais externos{', constantes de string' if strings else ''})",
        }
    return pairs


def _components(project):
    manifest = project.payload.get("manifest") or {}
    return {c["name"]: c for c in manifest.get("components", [])}


def _filters(component):
    return sorted({action for f in component["intentFilters"] for action in f["actions"]})


def _exposure(component):
    if component["exported"] is True:
        return "true"
    if component["exported"] in {"unknown", "inconsistent"}:
        return f"{component['exported']} (palpite {component.get('exportedGuess')})"
    return "false"


def diff(old, new):
    """Structured differences between two indexed projects (old → new)."""
    result = {
        "manifest": [],
        "findingsAdded": [],
        "findingsRemoved": [],
        "hierarchy": [],
        "renames": [],
        "triggers": set(),
    }
    old_manifest, new_manifest = old.payload.get("manifest"), new.payload.get("manifest")
    if old_manifest and new_manifest:
        for key in ("package", "versionName", "versionCode", "minSdk", "targetSdk"):
            if old_manifest.get(key) != new_manifest.get(key):
                result["manifest"].append(
                    {"kind": "app", "field": key, "old": old_manifest.get(key), "new": new_manifest.get(key)}
                )
        for flag in APP_FLAGS:
            before, after = old_manifest["application"][flag]["value"], new_manifest["application"][flag]["value"]
            if before != after:
                result["manifest"].append({"kind": "app-flag", "field": flag, "old": before, "new": after})
                if after is True:
                    result["triggers"].add("app-flag")
        before, after = set(old_manifest["usesPermissions"]), set(new_manifest["usesPermissions"])
        for permission in sorted(after - before):
            result["manifest"].append({"kind": "permission-added", "name": permission})
            result["triggers"].add("permission-added")
        for permission in sorted(before - after):
            result["manifest"].append({"kind": "permission-removed", "name": permission})
        old_components, new_components = _components(old), _components(new)
        for name in sorted(set(new_components) - set(old_components)):
            component = new_components[name]
            result["manifest"].append(
                {"kind": "component-added", "name": name, "type": component["type"], "exported": _exposure(component)}
            )
            result["triggers"].add("component-added")
            if component["exported"] is True:
                result["triggers"].add("exported-added")
            if component["deepLinks"]:
                result["triggers"].add("deeplink-added")
        for name in sorted(set(old_components) - set(new_components)):
            result["manifest"].append({"kind": "component-removed", "name": name, "type": old_components[name]["type"]})
        for name in sorted(set(old_components) & set(new_components)):
            a, b = old_components[name], new_components[name]
            if _exposure(a) != _exposure(b):
                became_exported = b["exported"] is True and a["exported"] is not True
                result["manifest"].append(
                    {
                        "kind": "exported-changed",
                        "name": name,
                        "old": _exposure(a),
                        "new": _exposure(b),
                        "highlight": became_exported,
                    }
                )
                if became_exported:
                    result["triggers"].add("exported-added")
            if (a["permission"], a["protectionLevel"]) != (b["permission"], b["protectionLevel"]):
                result["manifest"].append(
                    {
                        "kind": "permission-changed",
                        "name": name,
                        "old": a["permission"],
                        "new": b["permission"],
                        "oldLevel": a["protectionLevel"],
                        "newLevel": b["protectionLevel"],
                    }
                )
            old_links, new_links = {d["uri"] for d in a["deepLinks"]}, {d["uri"] for d in b["deepLinks"]}
            for uri in sorted(new_links - old_links):
                result["manifest"].append({"kind": "deeplink-added", "name": name, "uri": uri})
                result["triggers"].add("deeplink-added")
            for uri in sorted(old_links - new_links):
                result["manifest"].append({"kind": "deeplink-removed", "name": name, "uri": uri})
            if _filters(a) != _filters(b):
                result["manifest"].append(
                    {"kind": "filters-changed", "name": name, "old": _filters(a), "new": _filters(b)}
                )
    elif old_manifest or new_manifest:
        result["manifest"].append({"kind": "manifest-presence", "old": bool(old_manifest), "new": bool(new_manifest)})

    pairs = match_classes(old, new)
    result["renames"] = [
        {"old": a, "new": b["id"], "confidence": b["confidence"], "reason": b["reason"]}
        for a, b in sorted(pairs.items())
    ]
    renamed = {a: b["id"] for a, b in pairs.items()}

    def identity(finding, mapping=None):
        class_id = (mapping or {}).get(finding["classId"], finding["classId"])
        return (finding["ruleId"], class_id, snippet_hash(finding["snippet"]))

    old_ids = Counter(identity(f, renamed) for f in old.findings)
    new_ids = Counter(identity(f) for f in new.findings)
    by_identity_new = {identity(f): f for f in new.findings}
    by_identity_old = {identity(f, renamed): f for f in old.findings}
    for key, count in sorted((new_ids - old_ids).items()):
        finding = by_identity_new[key]
        result["findingsAdded"].append({**_finding_summary(finding), "count": count})
        if finding["severity"] in {"medium", "high"}:
            result["triggers"].add("finding-added")
    for key, count in sorted((old_ids - new_ids).items()):
        result["findingsRemoved"].append({**_finding_summary(by_identity_old[key]), "count": count})

    # Hierarchy of components present in both versions: the role chain that justifies the component.
    for name, component in sorted(_components(new).items()) if old_manifest and new_manifest else ():
        before = _components(old).get(name)
        if not before or not before.get("class") or not component.get("class"):
            continue
        chain_old = _chain(old, before["class"])
        chain_new = _chain(new, component["class"])
        if chain_old != chain_new:
            result["hierarchy"].append({"name": name, "old": chain_old, "new": chain_new})
    result["triggers"] = sorted(result["triggers"])
    result["empty"] = not any(result[k] for k in ("manifest", "findingsAdded", "findingsRemoved", "hierarchy"))
    return result


def _chain(project, class_id):
    """Superclass chain (extends only) of a class, as ids; framework part included."""
    chain, current, seen = [class_id], class_id, {class_id}
    parents = {
        e["source"]: e["target"]
        for e in project.edges
        if e["kind"] == "extends" and e["resolution"] in {"resolved", "external"}
    }
    types = framework()["types"]
    while True:
        parent = parents.get(current) or next(iter(types.get(current, {}).get("extends", [])), None)
        if not parent or parent in seen:
            return chain
        chain.append(parent)
        seen.add(parent)
        current = parent


def _finding_summary(finding):
    return {key: finding[key] for key in ("ruleId", "severity", "confidence", "classId", "file", "line", "snippet")}


def render_markdown(result, old_name, new_name):
    lines = [f"# Diferença de superfície de ataque: {old_name} → {new_name}", ""]
    if result["empty"]:
        lines += [
            "Nenhuma diferença na superfície de ataque, nos candidatos a achado ou na herança dos componentes.",
            "",
        ]
        return "\n".join(lines)
    labels = {
        "app": "Aplicativo",
        "app-flag": "Atributo de <application>",
        "permission-added": "Permissão nova",
        "permission-removed": "Permissão removida",
        "component-added": "Componente novo",
        "component-removed": "Componente removido",
        "exported-changed": "Exportação mudou",
        "permission-changed": "Permissão do componente mudou",
        "deeplink-added": "Deep link novo",
        "deeplink-removed": "Deep link removido",
        "filters-changed": "Ações dos intent-filters mudaram",
        "manifest-presence": "Presença do Manifest",
    }
    if result["manifest"]:
        lines += ["## Manifest", "", "| Mudança | Item | Antes | Depois |", "|---|---|---|---|"]
        for item in result["manifest"]:
            label = labels[item["kind"]] + (" **(false → true)**" if item.get("highlight") else "")
            name = item.get("name") or item.get("field") or ""
            before = item.get("old", "")
            after = item.get("new", item.get("uri", item.get("type", "")))
            if item["kind"] == "component-added":
                after = f"{item['type']}, exported={item['exported']}"
            lines.append(f"| {label} | `{name}` | {_cell(before)} | {_cell(after)} |")
        lines.append("")
    for title, key in (
        ("Candidatos a achado novos", "findingsAdded"),
        ("Candidatos a achado removidos", "findingsRemoved"),
    ):
        if result[key]:
            lines += [f"## {title}", "", "| Severidade | Regra | Classe | Linha | Trecho |", "|---|---|---|---|---|"]
            for f in result[key]:
                lines.append(
                    f"| {f['severity']} ({f['confidence']}) | `{f['ruleId']}` | `{f['classId']}` | {f['line']} | `{_cell(f['snippet'])}` |"
                )
            lines.append("")
    if result["hierarchy"]:
        lines += ["## Herança dos componentes", ""]
        for item in result["hierarchy"]:
            lines.append(f"- `{item['name']}`: {' → '.join(item['old'])} **⇒** {' → '.join(item['new'])}")
        lines.append("")
    if result["renames"]:
        lines += [
            "## Possíveis correspondências de classes renomeadas",
            "",
            "Pareamento por impressão estrutural; **não** é prova de equivalência.",
            "",
        ]
        for item in result["renames"]:
            lines.append(f"- `{item['old']}` → `{item['new']}` (confiança {item['confidence']}: {item['reason']})")
        lines.append("")
    if result["triggers"]:
        lines += ["Categorias para `--fail-on`: " + ", ".join(f"`{t}`" for t in result["triggers"]), ""]
    return "\n".join(lines)


def _cell(value):
    text = ", ".join(map(str, value)) if isinstance(value, list) else str(value if value is not None else "—")
    return text.replace("|", "\\|").replace("\n", " ")[:160]


def add_arguments(parser):
    parser.add_argument("old", help="Exportação do JADX da versão antiga (pasta sources ou a pasta de saída)")
    parser.add_argument("new", help="Exportação do JADX da versão nova")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--out", type=Path, help="Arquivo de saída (padrão: saída padrão)")
    parser.add_argument(
        "--fail-on",
        default="",
        help="Categorias separadas por vírgula; código de saída 2 se houver: " + ", ".join(FAIL_ON),
    )


def run(args, parser):
    from . import __version__
    from .indexer import Project

    wanted = {c.strip() for c in args.fail_on.split(",") if c.strip()}
    unknown = wanted - set(FAIL_ON)
    if unknown:
        parser.error(f"categorias desconhecidas em --fail-on: {', '.join(sorted(unknown))}")
    try:
        old, new = Project(args.old), Project(args.new)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    result = diff(old, new)
    if args.format == "json":
        text = (
            json.dumps(
                {
                    "schemaVersion": 1,
                    "tool": f"jadx-atlas {__version__}",
                    "old": old.payload["name"],
                    "new": new.payload["name"],
                    "approximateRenames": True,
                    **result,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    else:
        text = render_markdown(result, old.payload["name"], new.payload["name"])
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    hit = wanted & set(result["triggers"])
    if hit:
        print(f"--fail-on: {', '.join(sorted(hit))}", file=sys.stderr)
        return 2
    return 0
