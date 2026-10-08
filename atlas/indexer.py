"""Parse declarations with Tree-sitter and resolve explicit inheritance edges.

This is deliberately not a Java compiler. Ambiguous and missing symbols are
represented explicitly instead of being joined using a global short-name match.
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import time

from tree_sitter import Language, Parser
import tree_sitter_java

from . import code_facts
from . import manifest as manifest_module
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
SCHEMA_VERSION = 5
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
        self, root, progress=lambda *_: None, cancelled=lambda: False, demo=False, manifest=None, findings=True
    ):
        started = time.perf_counter()
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Pasta não encontrada. Informe o caminho da pasta exportada pelo JADX.")
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
                "Nenhum arquivo .java encontrado. Exporte o código no JADX e selecione a pasta sources ou sua pasta principal."
            )
        progress(0, len(paths), "Lendo declarações Java")
        parse_errors = 0
        for index, file in enumerate(paths):
            if cancelled():
                raise Cancelled()
            relative = file.relative_to(self.root).as_posix()
            try:
                if file.is_symlink():
                    raise ValueError("Link simbólico ignorado")
                stat = file.stat()
                if stat.st_size > MAX_FILE_BYTES:
                    raise ValueError("Arquivo maior que 8 MiB; ignorado")
                data = file.read_bytes()
                classes, has_error = parse_file(data, relative, rules_module.default_index() if findings else None)
                self.files[relative] = (stat.st_mtime_ns, stat.st_size)
                if has_error:
                    parse_errors += 1
                    warnings.append(
                        {
                            "path": relative,
                            "message": "Java incompleto ou inválido: as declarações recuperáveis foram indexadas.",
                        }
                    )
                for node in classes:
                    if node["id"] in self.nodes:
                        warnings.append(
                            {
                                "path": relative,
                                "message": f"Declaração duplicada de {node['id']}; mantida a primeira ocorrência.",
                            }
                        )
                    else:
                        self.nodes[node["id"]] = node
            except (OSError, ValueError) as error:
                warnings.append({"path": relative, "message": str(error)})
            if index % 25 == 0 or index + 1 == len(paths):
                progress(index + 1, len(paths), relative)
        if not self.nodes:
            raise ValueError("Não foi possível recuperar nenhuma declaração Java nesta pasta.")
        progress(len(paths), len(paths), "Resolvendo herança e interfaces")
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
                            + ("é ambíguo" if status == "ambiguous" else "não pôde ser resolvido")
                            + ".",
                        }
                    )
                if target not in self.nodes:
                    self.nodes[target] = {
                        "id": target,
                        "name": ref["name"].rsplit(".", 1)[-1],
                        "package": target.rsplit(".", 1)[0]
                        if status == "external" and "." in target
                        else "Referência não resolvida",
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
        self.findings = self._evaluate_rules() if findings else []
        self.intent_edges = self._intent_edges(manifest_data) if findings else []
        for node in self.nodes.values():
            for key in [key for key in node if key.startswith("_")]:
                del node[key]
        severity_counts = {severity: 0 for severity in rules_module.SEVERITIES}
        for finding in self.findings:
            severity_counts[finding["severity"]] += 1
        self.payload = {
            "schemaVersion": SCHEMA_VERSION,
            "name": "Pedidos · demonstração" if demo else self.root.name,
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
                "findings": severity_counts,
                "intentEdges": {
                    kind: sum(1 for e in self.intent_edges if e["kind"] == kind)
                    for kind in ("launches", "sends_action", "registers_receiver")
                },
                "unresolvedIntents": sum(len(n.get("intents", {}).get("unresolved", [])) for n in self.nodes.values()),
                "undeclaredComponentClasses": sum(1 for n in self.nodes.values() if n.get("undeclaredComponent")),
                "seconds": round(time.perf_counter() - started, 2),
            },
            "manifest": manifest_data,
            "findings": self.findings,
            "intentEdges": self.intent_edges,
            "rules": [rules_module.summary(rule) for rule in rules_module.load_rules()] if findings else [],
        }

    def _intent_edges(self, manifest):
        """launches / sends_action / registers_receiver edges from intra-method Intent flow (phase 4)."""
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
                                "reason": "receptor fora das fontes ou de tipo desconhecido",
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
                            "reason": "Intent não rastreável dentro do método",
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
                                "reason": "classe alvo fora das fontes",
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
                                "reason": "nome de classe não resolvido",
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
                                "reason": "ação não resolvida (dinâmica)",
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
                                "reason": "nenhum intent-filter do Manifest declara esta ação",
                                "action": action,
                            }
                        )
                    continue
                unresolved.append(
                    {
                        "line": event["line"],
                        "via": event["via"],
                        "reason": "Intent sem alvo nem ação",
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
                        "message": f"{component['name']} é declarado como {component['type']}, mas a cadeia de herança de {component['class']} não chega ao tipo esperado: possível erro de resolução ou classe base fora da tabela de framework.",
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
            warnings.append(
                {"path": path.name, "message": f"{error}. A camada de superfície de ataque está desligada."}
            )
            return None, "invalid"
        for message in data["warnings"]:
            warnings.append({"path": data["path"], "message": message})
        for component in data["components"]:
            node = self.nodes.get(component["classId"])
            # Exact id match only: a short-name coincidence never links a component to a class.
            component["class"] = component["classId"] if node and not node["external"] else None
            if component["class"] is None:
                warnings.append(
                    {
                        "path": data["path"],
                        "message": f"{component['type']} {component['name']} (linha {component['line']}) está no Manifest, mas a classe {component['classId']} não está nas fontes (falha do JADX, desofuscação ou componente de biblioteca ausente).",
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
            warnings.append({"path": data["path"], "message": f"Classe Application {app_class} não está nas fontes."})
        return data, "found"

    def source(self, class_id):
        node = self.nodes.get(class_id)
        if not node or not node["path"]:
            raise ValueError("Esta referência não possui código no projeto importado.")
        file = self.root / node["path"]
        if file.is_symlink() or not file.resolve().is_relative_to(self.root):
            raise ValueError("O caminho do arquivo mudou. Importe o projeto novamente.")
        stat = file.stat()
        if (stat.st_mtime_ns, stat.st_size) != self.files[node["path"]]:
            raise ValueError("O arquivo mudou desde a análise. Importe o projeto novamente para atualizar as linhas.")
        return {
            "path": node["path"],
            "line": node["line"],
            "endLine": node["endLine"],
            "code": file.read_text(encoding="utf-8", errors="replace"),
        }
