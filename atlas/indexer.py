"""Parse declarations with Tree-sitter and resolve explicit inheritance edges.

This is deliberately not a Java compiler. Ambiguous and missing symbols are
represented explicitly instead of being joined using a global short-name match.
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import re
import time

from tree_sitter import Language, Parser
import tree_sitter_java

from . import code_facts, parallel
from . import manifest as manifest_module
from . import paths as paths_module
from . import roles as roles_module
from . import rules as rules_module

JAVA = Language(tree_sitter_java.language())
KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
# Implicit java.lang imports, generated from android.jar (scripts/framework_hierarchy.py); the
# original hand-written names stay for non-Android Java sources (e.g. Record).
JAVA_LANG = set(
    json.loads((Path(__file__).resolve().parent / "data" / "java_lang.json").read_text(encoding="utf-8"))["types"]
) | set(
    "Object String Number Boolean Byte Short Integer Long Float Double Character Void Throwable Exception RuntimeException Error Enum Record Class Comparable CharSequence Cloneable Runnable AutoCloseable Iterable Thread StringBuilder StringBuffer Math System Override Deprecated SuppressWarnings FunctionalInterface AssertionError IllegalArgumentException IllegalStateException NullPointerException UnsupportedOperationException".split()
)
MAX_FILE_BYTES = 8 * 1024 * 1024
# 1: original payload. 2: schemaVersion, manifest, node.component, manifest stats.
# 3: node.roles, node.framework, stats.roles, component.roleCheck.
# 4: findings, rules, node.findings, stats.findings.
# 5: intentEdges, node.intents, node.readsIntent, node.deepLinkHandler, stats.intentEdges.
# 6: pathEntries, pathTargets, stats.usesEdges (uses edges stay server-side: /api/uses, /api/paths).
SCHEMA_VERSION = 6
NODE_COMPONENT_KEYS = (
    "type",
    "name",
    "exported",
    "exportedReason",
    "exportedGuess",
    "exportedGuessReason",
    "confidence",
    "permission",
    "permissionSource",
    "protectionLevel",
    "intentFilters",
    "deepLinks",
    "enabled",
    "exposure",
)


def value(node):
    return node.text.decode("utf-8", errors="replace") if node else ""


def raw_type(node):
    """Erase type arguments and type-use annotations, without regex parsing."""
    if node.type == "generic_type":
        return raw_type(node.named_children[0])
    if node.type == "annotated_type":
        return raw_type(node.named_children[-1])
    if node.type == "scoped_type_identifier":
        return ".".join(raw_type(c) for c in node.named_children if c.type not in {"annotation", "marker_annotation"})
    return value(node)


# JADX 1.5.x: "/* JADX INFO: renamed from: a.b, reason: ... */"; older versions omit "JADX INFO:".
RENAMED_FROM = re.compile(r"/\*\s*(?:JADX INFO:\s*)?renamed from:\s*([^,*\s]+)")
COMPILED_FROM = re.compile(r"/\*\s*(?:JADX INFO:\s*)?compiled from:\s*([^*\s]+)")


def jadx_aliases(node):
    """originalName / sourceFile from the JADX comments just above a declaration."""
    found, sibling = {}, node.prev_sibling
    while sibling is not None and sibling.type in {"block_comment", "line_comment"}:
        text = value(sibling)
        renamed, compiled = RENAMED_FROM.search(text), COMPILED_FROM.search(text)
        if renamed:
            found.setdefault("originalName", renamed.group(1))
        if compiled:
            found.setdefault("sourceFile", compiled.group(1))
        sibling = sibling.prev_sibling
    return found


def parse_file(data: bytes, path: str, rule_index=None):
    """Declarations of one file. With `rule_index`, also attach `_facts` for the rules (same tree, no reparse)."""
    tree = Parser(JAVA).parse(data)
    package, imports, wildcards = "", defaultdict(list), []
    for child in tree.root_node.named_children:
        if child.type == "package_declaration":
            package = next(
                (value(c) for c in child.named_children if c.type in {"identifier", "scoped_identifier"}), ""
            )
        elif child.type == "import_declaration":
            name = next((value(c) for c in child.named_children if c.type in {"identifier", "scoped_identifier"}), "")
            if any(c.type == "asterisk" for c in child.named_children):
                wildcards.append(name)
            elif name:
                imports[name.rsplit(".", 1)[-1]].append(name)
    declarations = []

    def visit(container, owners=()):
        for node in container.named_children:
            if node.type in KINDS:
                name = value(node.child_by_field_name("name"))
                if not name:
                    continue
                qualified = ".".join(filter(None, (package, *owners, name)))
                body = node.child_by_field_name("body")
                header = (
                    data[node.start_byte : body.start_byte if body else node.end_byte]
                    .decode("utf-8", "replace")
                    .strip()
                )
                refs = []
                for clause in node.named_children:
                    if clause.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
                        continue
                    kind = "implements" if clause.type == "super_interfaces" else "extends"
                    types = clause.named_children
                    if types and types[0].type == "type_list":
                        types = types[0].named_children
                    refs.extend(
                        {"name": raw_type(t), "kind": kind}
                        for t in types
                        if t.type not in {"line_comment", "block_comment"}
                    )
                declarations.append(
                    {
                        "id": qualified,
                        "name": name,
                        "package": package,
                        "kind": KINDS[node.type],
                        "external": False,
                        "abstract": any(
                            c.type == "modifiers" and "abstract" in value(c).split() for c in node.named_children
                        ),
                        "path": path,
                        "line": node.start_point.row + 1,
                        "endLine": node.end_point.row + 1,
                        "declaration": header,
                        "_owners": owners,
                        "_imports": dict(imports),
                        "_wildcards": wildcards,
                        "_refs": refs,
                        "_start": node.start_byte,
                        "_end": node.end_byte,
                        **jadx_aliases(node),
                    }
                )
                if body:
                    visit(body, (*owners, name))
            elif node.type in {"enum_body_declarations", "ERROR"}:
                visit(node, owners)
            # Method-local and anonymous classes do not have stable source FQNs.

    visit(tree.root_node)
    if rule_index is not None:
        code_facts.collect(JAVA, tree, data, declarations, rule_index)
        if tree.root_node.has_error:
            for declaration in declarations:
                declaration["_partial"] = True
    return declarations, tree.root_node.has_error


def resolve(name, owner, symbols):
    package = owner["package"]
    prefix = f"{package}." if package else ""
    # Lexical members, then enclosing classes, before imports / current package.
    scopes = [*owner["_owners"], owner["name"]]
    while scopes:
        candidate = prefix + ".".join([*scopes, name])
        if candidate in symbols:
            return candidate, "resolved", []
        scopes.pop()
    first, *rest = name.split(".")
    imported = owner["_imports"].get(first, [])
    if imported:
        candidates = sorted(set(".".join([i, *rest]) for i in imported))
        if len(candidates) == 1:
            target = candidates[0]
            return target, "resolved" if target in symbols else "external", []
        return None, "ambiguous", candidates
    if prefix + name in symbols:
        return prefix + name, "resolved", []
    if "." in name and name in symbols:
        return name, "resolved", []
    candidates = sorted(set(f"{p}.{name}" for p in owner["_wildcards"] if f"{p}.{name}" in symbols))
    if name in JAVA_LANG:
        candidates.append(f"java.lang.{name}")
    candidates = sorted(set(candidates))
    if len(candidates) == 1:
        target = candidates[0]
        return target, "resolved" if target in symbols else "external", []
    if len(candidates) > 1:
        return None, "ambiguous", candidates
    # A single wildcard import of a framework package that is known to contain the type (javap table).
    if "." not in name and len(owner["_wildcards"]) == 1:
        candidate = f"{owner['_wildcards'][0]}.{name}"
        if candidate in roles_module.framework()["types"]:
            return candidate, "external", []
    # JADX normally writes imports or fully qualified names for external types.
    if "." in name and first[:1].islower():
        return name, "external", []
    return None, "unresolved", [f"{p}.{name}" for p in owner["_wildcards"]]


class Cancelled(Exception):
    pass


class Project:
    def __init__(
        self,
        root,
        progress=lambda *_: None,
        cancelled=lambda: False,
        demo=False,
        manifest=None,
        findings=True,
        workers=None,
        cache=False,
    ):
        started = time.perf_counter()
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Folder not found. Provide the path of the folder exported by JADX.")
        self.nodes = {}
        self.edges = []
        self.files = {}
        warnings = []
        paths = []
        for folder, dirs, names in os.walk(self.root, followlinks=False):
            if cancelled():
                raise Cancelled()
            dirs[:] = sorted(
                d for d in dirs if d not in {".git", ".venv", "node_modules"} and not (Path(folder) / d).is_symlink()
            )
            paths.extend(Path(folder) / name for name in sorted(names) if name.endswith(".java"))
        if not paths:
            raise ValueError(
                "No .java files found. Export the code with JADX and select the sources folder or its parent."
            )
        progress(0, len(paths), "Reading Java declarations")
        relatives = [file.relative_to(self.root).as_posix() for file in paths]
        self.workers = workers if workers else parallel.default_workers(len(relatives))
        self.cache_hit = False
        cache = parallel.IndexCache(self.root, findings) if cache else None
        cached = cache.load() if cache else {}
        fresh = []
        for relative in relatives:
            entry = cached.get(relative)
            try:
                stat = (self.root / relative).stat()
            except OSError:
                entry = None
            # Reuse a cached parse only for an unchanged regular file (same mtime and size).
            if not (
                entry
                and entry["stat"]
                and entry["stat"] == [stat.st_mtime_ns, stat.st_size]
                and not (self.root / relative).is_symlink()
            ):
                fresh.append(relative)
        self.cache_hit = bool(cached) and len(fresh) < len(relatives)
        parsed = dict.fromkeys(relatives)
        fresh_set = set(fresh)  # a list here would make this loop quadratic on large apps
        for relative in relatives:
            if relative not in fresh_set:
                parsed[relative] = cached[relative]
        done = len(relatives) - len(fresh)
        for result in parallel.iter_parsed(self.root, fresh, findings, self.workers, cancelled):
            parsed[result["relative"]] = result
            done += 1
            if done % 25 == 0 or done == len(relatives):
                progress(done, len(relatives), result["relative"])
        if cache:
            try:
                cache.save(parsed)
            except OSError as error:
                warnings.append({"path": None, "message": f"Disk cache not written: {error}"})
        parse_errors = 0
        for relative in relatives:  # input order: duplicates keep the first occurrence, as before
            result = parsed[relative]
            if result["error"]:
                warnings.append({"path": relative, "message": result["error"]})
                continue
            self.files[relative] = tuple(result["stat"])
            if result["hasError"]:
                parse_errors += 1
                warnings.append(
                    {
                        "path": relative,
                        "message": "Incomplete or invalid Java: recoverable declarations were indexed.",
                    }
                )
            for node in result["classes"]:
                if node["id"] in self.nodes:
                    warnings.append(
                        {
                            "path": relative,
                            "message": f"Duplicate declaration of {node['id']}; the first one was kept.",
                        }
                    )
                else:
                    self.nodes[node["id"]] = node
        if cancelled():
            raise Cancelled()
        if not self.nodes:
            raise ValueError("Could not recover any Java declaration in this folder.")
        progress(len(paths), len(paths), "Resolving inheritance and interfaces")
        symbols = set(self.nodes)
        uncertain = 0
        for node in list(self.nodes.values()):
            if cancelled():
                raise Cancelled()
            for ref in node["_refs"]:
                target, status, candidates = resolve(ref["name"], node, symbols)
                if not target:
                    # Scope-specific ID prevents false joins between unknown names.
                    target = f"?{node['id']}::{ref['name']}"
                    uncertain += 1
                    warnings.append(
                        {
                            "path": node["path"],
                            "message": f"{node['id']}: {ref['name']} "
                            + ("is ambiguous" if status == "ambiguous" else "could not be resolved")
                            + ".",
                        }
                    )
                if target not in self.nodes:
                    self.nodes[target] = {
                        "id": target,
                        "name": ref["name"].rsplit(".", 1)[-1],
                        "package": target.rsplit(".", 1)[0]
                        if status == "external" and "." in target
                        else "Unresolved reference",
                        "kind": "interface" if ref["kind"] == "implements" or node["kind"] == "interface" else "class",
                        "external": True,
                        "abstract": False,
                        "resolution": status,
                        "candidates": candidates,
                        "path": None,
                        "line": None,
                        "declaration": ref["name"],
                    }
                self.edges.append(
                    {
                        "id": f"e{len(self.edges)}",
                        "source": node["id"],
                        "target": target,
                        "kind": ref["kind"],
                        "reference": ref["name"],
                        "resolution": status,
                    }
                )
        for node in self.nodes.values():
            if node["external"]:
                kind, source = roles_module.framework_kind(node["id"])
                if kind:  # the javap-generated table knows the real kind and package
                    node.update(
                        kind="interface" if kind == "interface" else "class",
                        abstract=kind == "abstract class",
                        framework=source,
                    )
        role_counts = roles_module.assign(self.nodes, self.edges)
        manifest_data, manifest_status = self._attach_manifest(manifest, warnings)
        components = manifest_data["components"] if manifest_data else []
        self.secret_values = {}
        self.findings = self._evaluate_rules() if findings else []
        self.intent_edges = self._intent_edges(manifest_data) if findings else []
        self.flow = self._flow_graph() if findings else paths_module.FlowGraph()
        entries, targets = self._path_endpoints()
        self.fingerprints = self._fingerprints()
        self.search_index = self._search_index()
        obfuscation = self._mark_libraries_and_obfuscation(manifest_data)
        for node in self.nodes.values():
            for key in [key for key in node if key.startswith("_")]:
                del node[key]
        severity_counts = {severity: 0 for severity in rules_module.SEVERITIES}
        for finding in self.findings:
            severity_counts[finding["severity"]] += 1
        self.payload = {
            "schemaVersion": SCHEMA_VERSION,
            "name": "Orders · demo" if demo else self.root.name,
            "root": str(self.root),
            "demo": demo,
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
            "warnings": warnings,
            "stats": {
                "files": len(self.files),
                "discoveredFiles": len(paths),
                "types": len(symbols),
                "external": len(self.nodes) - len(symbols),
                "relations": len(self.edges),
                "packages": len({n["package"] for n in self.nodes.values() if not n["external"]}),
                "parseErrors": parse_errors,
                "unresolved": uncertain,
                "manifest": manifest_status,
                "components": len(components),
                "exportedComponents": sum(1 for c in components if c["exported"] is True),
                "potentiallyExported": sum(
                    1 for c in components if c["exported"] in {"unknown", "inconsistent"} and c.get("exportedGuess")
                ),
                "exportedWithoutPermission": sum(
                    1 for c in components if c["exported"] is True and not c["permission"]
                ),
                "deepLinks": sum(len(c["deepLinks"]) for c in components),
                "componentsWithoutClass": sum(1 for c in components if c["class"] is None),
                "roles": role_counts,
                "obfuscation": obfuscation,
                "libraryClasses": sum(1 for n in self.nodes.values() if n.get("library")),
                "findings": severity_counts,
                "intentEdges": {
                    kind: sum(1 for e in self.intent_edges if e["kind"] == kind)
                    for kind in ("launches", "sends_action", "registers_receiver")
                },
                "usesEdges": sum(1 for edges in self.flow.adjacency.values() for e in edges if e[1] == "uses"),
                "unresolvedIntents": sum(len(n.get("intents", {}).get("unresolved", [])) for n in self.nodes.values()),
                "undeclaredComponentClasses": sum(1 for n in self.nodes.values() if n.get("undeclaredComponent")),
                "seconds": round(time.perf_counter() - started, 2),
            },
            "manifest": manifest_data,
            "findings": self.findings,
            "intentEdges": self.intent_edges,
            "pathEntries": entries,
            "pathTargets": targets,
            "rules": [rules_module.summary(rule) for rule in rules_module.load_rules()] if findings else [],
        }

    def _mark_libraries_and_obfuscation(self, manifest):
        """node.library for known third-party prefixes (never the app package) and the obfuscation estimate.

        Obfuscation: share of project classes whose simple name has at most 2 characters (`a`, `ab`, `a$b`)
        or that JADX renamed (originalName). < 10% → low, < 40% → medium, otherwise high.
        """
        prefixes = json.loads(
            (Path(__file__).resolve().parent / "data" / "libraries.json").read_text(encoding="utf-8")
        )["prefixes"]
        app = (manifest or {}).get("package") or ""
        own = [n for n in self.nodes.values() if not n["external"]]
        for node in own:
            name = node["id"] + "."
            if any(name.startswith(prefix) for prefix in prefixes) and not (app and name.startswith(app + ".")):
                node["library"] = True
        short = [n for n in own if len(n["name"].rsplit("$", 1)[-1]) <= 2 or n.get("originalName")]
        fraction = len(short) / len(own) if own else 0.0
        level = "low" if fraction < 0.1 else "medium" if fraction < 0.4 else "high"
        return {"shortNames": len(short), "classes": len(own), "fraction": round(fraction, 3), "level": level}

    def _search_index(self):
        """Server-side search rows per class (not in the payload): strings, roles, rules, aliases."""
        rules_by_class = {}
        for finding in self.findings:
            rules_by_class.setdefault(finding["classId"], set()).add(finding["ruleId"])
        index = {}
        for node in self.nodes.values():
            if node["external"]:
                continue
            index[node["id"]] = {
                "strings": list(dict.fromkeys(node.get("_strings", []))),
                "roles": [r["role"] for r in node.get("roles", ())],
                "rules": sorted(rules_by_class.get(node["id"], ())),
            }
        return index

    def payload_bytes(self):
        """Serialised payload size, computed once (the payload never changes after indexing)."""
        if not hasattr(self, "_payload_bytes"):
            self._payload_bytes = len(json.dumps(self.payload, ensure_ascii=False).encode())
        return self._payload_bytes

    def summary(self):
        """Small overview for large projects: everything except the node/edge/finding lists."""
        payload = self.payload
        manifest = payload.get("manifest")
        return {
            "schemaVersion": payload["schemaVersion"],
            "name": payload["name"],
            "demo": payload["demo"],
            "stats": payload["stats"],
            "warnings": len(payload["warnings"]),
            "manifest": manifest,
            "pathEntries": payload["pathEntries"],
            "pathTargets": payload["pathTargets"],
            "rules": payload["rules"],
        }

    def list(self, page=0, size=200, kind="", role="", severity="", query=""):
        """Paginated, stable (sorted by id) list of project classes with light fields."""
        page, size = max(0, int(page)), max(1, min(int(size), 1000))
        rank = {"info": 0, "low": 1, "medium": 2, "high": 3}
        needle = (query or "").lower()
        rows = []
        for class_id in sorted(k for k, n in self.nodes.items() if not n["external"]):
            node = self.nodes[class_id]
            if kind == "interface" and node["kind"] not in {"interface", "annotation"}:
                continue
            if kind == "class" and node["kind"] in {"interface", "annotation"}:
                continue
            if role and not any(r["role"] == role for r in node.get("roles", ())):
                continue
            if severity and not any(rank[s] >= rank[severity] for s in node.get("findings", {})):
                continue
            if needle and needle not in class_id.lower():
                continue
            rows.append(
                {
                    "id": class_id,
                    "name": node["name"],
                    "package": node["package"],
                    "kind": node["kind"],
                    "roles": [r["role"] for r in node.get("roles", ())],
                    "findings": node.get("findings", {}),
                    "exported": (node.get("component") or {}).get("exported"),
                }
            )
        return {"page": page, "size": size, "total": len(rows), "items": rows[page * size : (page + 1) * size]}

    def neighbors(self, class_id, depth=1, layers=("inheritance", "intents"), limit=300):
        """Nodes and edges around one class (both directions), for drawing only the selected neighbourhood."""
        if class_id not in self.nodes:
            raise ValueError("Unknown class.")
        depth = max(1, min(int(depth), 3))
        edges = []
        if "inheritance" in layers:
            edges += self.edges
        if "intents" in layers:
            edges += self.intent_edges
        if "uses" in layers:
            edges += [
                {"id": f"u{i}", "source": s, "target": e[0], "kind": "uses", "line": e[3]}
                for i, (s, e) in enumerate(
                    (s, e) for s, es in self.flow.adjacency.items() for e in es if e[1] == "uses"
                )
            ]
        around = {}
        for edge in edges:
            around.setdefault(edge["source"], []).append(edge)
            around.setdefault(edge["target"], []).append(edge)
        seen, frontier, chosen, truncated = {class_id}, [class_id], {}, False
        for _ in range(depth):
            following = []
            for node_id in frontier:
                for edge in sorted(around.get(node_id, ()), key=lambda e: e["id"]):
                    other = edge["target"] if edge["source"] == node_id else edge["source"]
                    chosen[edge["id"]] = edge
                    if other not in seen:
                        if len(seen) >= limit:
                            truncated = True
                            continue
                        seen.add(other)
                        following.append(other)
            frontier = following
        nodes = [self.nodes[n] for n in sorted(seen) if n in self.nodes]
        kept = [e for e in chosen.values() if e["source"] in seen and e["target"] in seen]
        return {
            "id": class_id,
            "depth": depth,
            "nodes": nodes,
            "edges": sorted(kept, key=lambda e: e["id"]),
            "truncated": truncated,
        }

    def search(self, query, limit=100):
        """Case-insensitive substring search over names, aliases, packages, roles, rules and strings."""
        needle = (query or "").strip().lower()
        if len(needle) < 2:
            raise ValueError("Type at least 2 characters.")
        try:
            limit = max(1, min(int(limit), 500))
        except ValueError:
            raise ValueError("limit must be a number.") from None
        priority = {"name": 0, "alias": 1, "package": 2, "role": 3, "rule": 4, "string": 5}
        results = []
        for class_id in sorted(self.search_index):
            node, row = self.nodes[class_id], self.search_index[class_id]
            fields = [
                ("name", class_id),
                ("alias", node.get("originalName") or ""),
                ("alias", node.get("sourceFile") or ""),
                ("package", node["package"]),
            ]
            fields += (
                [("role", r) for r in row["roles"]]
                + [("rule", r) for r in row["rules"]]
                + [("string", s) for s in row["strings"]]
            )
            for field, text in fields:
                if text and needle in text.lower():
                    results.append({"id": class_id, "field": field, "text": text[:160]})
                    break  # one row per class: its best field
        results.sort(key=lambda r: (priority[r["field"]], r["id"]))
        return {"query": query, "results": results[:limit], "total": len(results)}

    def _fingerprints(self):
        """Structural fingerprint per project class, used to pair renamed (obfuscated) classes across versions.

        Only rename-stable facts: kind, abstract flag, roles, external (framework/library) parents and the
        set of string constants. Project-internal parent names are left out because they are renamed too.
        """
        prints, external_parents, findings = {}, {}, {}
        for edge in self.edges:
            if self.nodes.get(edge["target"], {}).get("external"):
                external_parents.setdefault(edge["source"], []).append(edge["target"])
        for finding in self.findings:
            findings.setdefault(finding["classId"], []).append(finding["ruleId"])
        for node in self.nodes.values():
            if node["external"]:
                continue
            parents = sorted(external_parents.get(node["id"], []))
            prints[node["id"]] = {
                "kind": node["kind"],
                "abstract": node["abstract"],
                "roles": sorted(r["role"] for r in node.get("roles", ())),
                "externalParents": parents,
                "strings": sorted(
                    set(node.get("_string_constants", {}).values())
                    | {e["value"] for e in node.get("_facts", ()) if e["kind"] == "string"}
                )[:200],
                "findings": sorted(findings.get(node["id"], [])),
            }
        return prints

    def _flow_graph(self):
        """Adjacency lists for path search: Intent edges, type references ("uses") and inheritance."""
        graph = paths_module.FlowGraph()
        for edge in self.intent_edges:
            graph.add(edge["source"], edge["target"], edge["kind"], edge["via"], edge["line"], edge["confidence"])
        symbols = {key for key, node in self.nodes.items() if not node["external"]}
        for node in self.nodes.values():
            if node["external"]:
                continue
            for name, line in node.get("_uses", {}).items():
                target, status, _ = resolve(name, node, symbols)
                if status == "resolved" and target != node["id"]:
                    graph.add(node["id"], target, "uses", "type reference", line, "high")
        for edge in self.edges:
            if edge["kind"] == "extends" and edge["resolution"] == "resolved":
                # Inherited code runs as the subclass: walking up is allowed when the caller asks for it.
                graph.add(
                    edge["source"], edge["target"], "extends", "inheritance", self.nodes[edge["source"]]["line"], "high"
                )
        return graph.freeze()

    def _path_endpoints(self):
        """Default entries (exposed components, deep-link handlers, Application) and targets (findings >= medium)."""
        entries = []
        for node in self.nodes.values():
            component = node.get("component")
            reasons = []
            if component and (
                component["exported"] is True
                or (component["exported"] in {"unknown", "inconsistent"} and component.get("exportedGuess"))
            ):
                reasons.append(
                    f"exported {component['type']}" + ("" if component["exported"] is True else " (potentially)")
                )
            if node.get("deepLinkHandler"):
                reasons.append("deep link")
            if node.get("applicationClass"):
                reasons.append("Application")
            if reasons:
                entries.append({"id": node["id"], "reasons": reasons})
        targets = [
            node["id"]
            for node in self.nodes.values()
            if node.get("findings", {}).get("high") or node.get("findings", {}).get("medium")
        ]
        return sorted(entries, key=lambda e: e["id"]), sorted(targets)

    def paths(self, entry, target=None, max_depth=6, inheritance=True):
        """Possible paths from `entry` to `target` (or to every default target)."""
        if entry not in self.nodes or self.nodes[entry]["external"]:
            raise ValueError("Unknown entry.")
        if target is not None and (target not in self.nodes or self.nodes[target]["external"]):
            raise ValueError("Unknown target.")
        targets = [target] if target else self.payload["pathTargets"]
        kinds = {"launches", "sends_action", "registers_receiver", "uses"} | ({"extends"} if inheritance else set())
        limits = {"maxDepth": max(1, min(int(max_depth), 10))}
        result = paths_module.find_paths(self.flow, entry, targets, kinds, limits)
        for path in result["paths"]:
            for step in path["steps"]:
                step["file"] = self.nodes[step["from"]]["path"]
        result["text"] = paths_module.as_text(result, {k: n["path"] for k, n in self.nodes.items() if n.get("path")})
        return result

    def uses(self, class_id, limit=200):
        """Type-reference neighbours of one class, both directions, for the inspector."""
        if class_id not in self.nodes:
            raise ValueError("Unknown class.")
        out = [
            {"id": t, "line": line} for t, kind, _, line, _ in self.flow.adjacency.get(class_id, ()) if kind == "uses"
        ]
        incoming = [
            {"id": s, "line": e[3]}
            for s, edges in self.flow.adjacency.items()
            for e in edges
            if e[1] == "uses" and e[0] == class_id
        ]
        return {
            "id": class_id,
            "out": out[:limit],
            "in": sorted(incoming, key=lambda i: i["id"])[:limit],
            "truncated": len(out) > limit or len(incoming) > limit,
        }

    def _intent_edges(self, manifest):
        """launches / sends_action / registers_receiver edges from intra-method Intent flow."""
        symbols = {key for key, node in self.nodes.items() if not node["external"]}
        constants = {key: self.nodes[key].get("_string_constants", {}) for key in symbols}
        package = manifest["package"] if manifest else ""
        actions = {}
        for component in manifest["components"] if manifest else ():
            if component["class"]:
                for intent_filter in component["intentFilters"]:
                    for action in intent_filter["actions"]:
                        actions.setdefault(action, []).append(component)

        def type_id(name, owner):
            target, status, _ = resolve(name, owner, symbols)
            return target if status == "resolved" else None

        def value_of(item, owner):
            """(string value or None, description of where it came from)."""
            if not item:
                return None, None
            if "value" in item:
                return item["value"], f"constante {item['constant']}" if "constant" in item else "literal"
            if "reference" in item:
                owner_type, _, name = item["reference"].rpartition(".")
                target = type_id(owner_type, owner) if owner_type else None
                if target and name in constants.get(target, {}):
                    return constants[target][name], f"constante {item['reference']}"
                return None, item["reference"]
            return None, item.get("unresolved")

        edges, seen = [], set()

        def add(source, target, kind, event, confidence, **extra):
            key = (id(event), target, extra.get("action"))  # distinct calls on one line stay distinct
            if key in seen:
                return
            seen.add(key)
            edges.append(
                {
                    "id": f"i{len(edges)}",
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "via": event.get("via", "registerReceiver"),
                    "line": event["line"],
                    "confidence": confidence,
                    "resolution": "resolved",
                    **extra,
                }
            )

        for node in list(self.nodes.values()):
            if node["external"]:
                continue
            unresolved = []
            for event in node.get("_facts", ()):
                if event["kind"] == "reads-intent":
                    if any(r["role"] in {"activity", "service", "receiver"} for r in node.get("roles", ())):
                        node["readsIntent"] = True
                    continue
                if event["kind"] == "register":
                    target = type_id(event["receiverType"], node) if event["receiverType"] else None
                    resolved_actions = [
                        value_of(a, node)[0] or f"? {value_of(a, node)[1]}" for a in (event["actions"] or [])
                    ]
                    if target:
                        add(
                            node["id"],
                            target,
                            "registers_receiver",
                            event,
                            "high" if event["exact"] else "medium",
                            actions=resolved_actions,
                        )
                    else:
                        unresolved.append(
                            {
                                "line": event["line"],
                                "via": "registerReceiver",
                                "reason": "receiver not in the sources or of unknown type",
                                "text": event["text"],
                            }
                        )
                    continue
                if event["kind"] != "intent":
                    continue
                intent = event["intent"]
                if intent is None:
                    unresolved.append(
                        {
                            "line": event["line"],
                            "via": event["via"],
                            "reason": "Intent not traceable within the method",
                            "text": event["argument"],
                        }
                    )
                    continue
                if intent.get("targetType"):
                    target = type_id(intent["targetType"], node)
                    if target:
                        add(node["id"], target, "launches", event, "high")
                    else:
                        unresolved.append(
                            {
                                "line": event["line"],
                                "via": event["via"],
                                "reason": "target class not in the sources",
                                "text": intent["targetType"],
                            }
                        )
                    continue
                if intent.get("targetName"):
                    name, origin = value_of(intent["targetName"], node)
                    name = (package + name if name and name.startswith(".") else name or "").replace("$", ".")
                    if name in symbols:
                        add(node["id"], name, "launches", event, "medium", targetFrom=origin)  # class name as string
                    else:
                        unresolved.append(
                            {
                                "line": event["line"],
                                "via": event["via"],
                                "reason": "class name not resolved",
                                "text": name or origin,
                            }
                        )
                    continue
                if intent.get("action"):
                    action, origin = value_of(intent["action"], node)
                    if action is None:
                        unresolved.append(
                            {
                                "line": event["line"],
                                "via": event["via"],
                                "reason": "action not resolved (dynamic)",
                                "text": origin,
                            }
                        )
                    elif action in actions:
                        for component in actions[action]:
                            add(
                                node["id"],
                                component["class"],
                                "sends_action",
                                event,
                                "medium",
                                action=action,
                                component=component["name"],
                            )
                    else:
                        unresolved.append(
                            {
                                "line": event["line"],
                                "via": event["via"],
                                "reason": "no manifest intent-filter declares this action",
                                "action": action,
                            }
                        )
                    continue
                unresolved.append(
                    {
                        "line": event["line"],
                        "via": event["via"],
                        "reason": "Intent without target or action",
                        "text": event["argument"],
                    }
                )
            if unresolved:
                node["intents"] = {"unresolved": unresolved[:50], "unresolvedTotal": len(unresolved)}
            component = node.get("component")
            if component and component.get("deepLinks"):
                node["deepLinkHandler"] = True
        return edges

    def _evaluate_rules(self):
        """Run the rules on every project class. Findings are candidates, sorted by severity."""
        evaluator = rules_module.Evaluator(self.nodes, resolve, roles_module.Hierarchy(self.nodes, self.edges))
        found = []
        for node in self.nodes.values():
            if node["external"] or not node.get("_facts"):
                continue
            component = node.get("component")
            exposed = bool(component) and (
                component["exported"] is True
                or (component["exported"] in {"unknown", "inconsistent"} and component.get("exportedGuess") is True)
            )
            results = evaluator.evaluate(node, exposed)
            if results:
                counts = {}
                for result in results:
                    counts[result["severity"]] = counts.get(result["severity"], 0) + 1
                node["findings"] = counts
                found.extend(results)
        rank = {severity: index for index, severity in enumerate(rules_module.SEVERITIES)}
        found.sort(key=lambda f: (-rank[f["severity"]], f["classId"], f["line"], f["ruleId"]))
        # Full secret values stay in memory only, for `export --include-secrets`; the payload keeps the mask.
        for index, finding in enumerate(found):
            if "_secretValue" in finding:
                self.secret_values[index] = finding.pop("_secretValue")
        return found

    def _check_roles(self, data, warnings):
        """Manifest says what a class must be; the ancestor chain says what it is. Disagreement = review."""
        expected = {
            "activity": "activity",
            "activity-alias": "activity",
            "service": "service",
            "receiver": "receiver",
            "provider": "provider",
        }
        for component in data["components"]:
            node = self.nodes.get(component["class"]) if component["class"] else None
            if not node:
                continue
            role = next((r for r in node.get("roles", []) if r["role"] == expected[component["type"]]), None)
            component["roleCheck"] = role["confidence"] if role else "missing"
            if not role:
                warnings.append(
                    {
                        "path": data["path"],
                        "message": f"{component['name']} is declared as {component['type']}, but the inheritance chain of {component['class']} does not reach the expected type: possible resolution error or a base class missing from the framework table.",
                    }
                )
        declared = {c["class"] for c in data["components"] if c["class"]} | {data["application"]["name"]}
        component_roles = {"activity", "service", "receiver", "provider"}
        for node in self.nodes.values():
            # Information only: abstract or base classes are often not declared.
            if (
                not node["external"]
                and node["id"] not in declared
                and any(r["role"] in component_roles for r in node.get("roles", []))
            ):
                node["undeclaredComponent"] = True

    def _attach_manifest(self, explicit, warnings):
        """Locate, parse and link the AndroidManifest. Returns (manifest or None, status)."""
        try:
            path = manifest_module.locate(self.root, explicit)
        except manifest_module.ManifestError as error:
            raise ValueError(str(error)) from None  # the user asked for this file explicitly
        if not path:
            # Not a warning: plain Java folders are a supported use. The UI explains stats.manifest == "missing".
            return None, "missing"
        base = self.root if path.is_relative_to(self.root) else self.root.parent
        try:
            data = manifest_module.load(path, base)
        except (manifest_module.ManifestError, OSError) as error:
            if explicit:
                raise ValueError(str(error)) from None
            warnings.append({"path": path.name, "message": f"{error}. The attack-surface layer is off."})
            return None, "invalid"
        for message in data["warnings"]:
            warnings.append({"path": data["path"], "message": message})
        by_original = {n["originalName"]: n for n in self.nodes.values() if n.get("originalName")}
        for component in data["components"]:
            node = self.nodes.get(component["classId"])
            if node is None:
                # R8 can keep `Outer$Inner` as a top-level class (JADX writes the `$`), and JADX --deobf can rename:
                raw = component["classId"]
                candidates = [component.get("rawName", "").replace("/", "."), raw]
                node = (
                    next((self.nodes[c] for c in candidates if c in self.nodes), None)
                    or by_original.get(component.get("rawName", ""))
                    or by_original.get(raw)
                )
                if node is not None:
                    component["classId"] = node["id"]
            # Exact id match only: a short-name coincidence never links a component to a class.
            component["class"] = component["classId"] if node and not node["external"] else None
            if component["class"] is None:
                warnings.append(
                    {
                        "path": data["path"],
                        "message": f"{component['type']} {component['name']} (line {component['line']}) is in the manifest, but class {component['classId']} is not in the sources (JADX failure, deobfuscation or missing library component).",
                    }
                )
                continue
            summary = {key: component[key] for key in NODE_COMPONENT_KEYS if key in component}
            current = node.get("component")
            # Several declarations (an activity and its aliases) can share one class: keep the most exposed.
            if current is None or component["exposure"] < current["exposure"]:
                node["component"] = summary
            node.setdefault("declaredAs", []).append(component["name"])
        self._check_roles(data, warnings)
        app_class = data["application"]["name"]
        if app_class and app_class in self.nodes and not self.nodes[app_class]["external"]:
            self.nodes[app_class]["applicationClass"] = True
        elif app_class:
            warnings.append({"path": data["path"], "message": f"Application class {app_class} is not in the sources."})
        return data, "found"

    def source(self, class_id):
        node = self.nodes.get(class_id)
        if not node or not node["path"]:
            raise ValueError("This reference has no code in the imported project.")
        file = self.root / node["path"]
        if file.is_symlink() or not file.resolve().is_relative_to(self.root):
            raise ValueError("The file path changed. Import the project again.")
        stat = file.stat()
        if (stat.st_mtime_ns, stat.st_size) != self.files[node["path"]]:
            raise ValueError("The file changed since the analysis. Import the project again to refresh the lines.")
        return {
            "path": node["path"],
            "line": node["line"],
            "endLine": node["endLine"],
            "code": file.read_text(encoding="utf-8", errors="replace"),
        }
