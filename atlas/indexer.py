"""Parse declarations with Tree-sitter and resolve explicit inheritance edges.

This is deliberately not a Java compiler. Ambiguous and missing symbols are
represented explicitly instead of being joined using a global short-name match.
"""

from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
import time

from tree_sitter import Language, Parser
import tree_sitter_java

from . import manifest as manifest_module
from . import roles as roles_module

JAVA = Language(tree_sitter_java.language())
KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
JAVA_LANG = set(
    "Object String Number Boolean Byte Short Integer Long Float Double Character Void Throwable Exception RuntimeException Error Enum Record Class Comparable CharSequence Cloneable Runnable AutoCloseable Iterable Thread StringBuilder StringBuffer Math System Override Deprecated SuppressWarnings FunctionalInterface AssertionError IllegalArgumentException IllegalStateException NullPointerException UnsupportedOperationException".split()
)
MAX_FILE_BYTES = 8 * 1024 * 1024
# 1: original payload. 2: schemaVersion, manifest, node.component, manifest stats.
# 3: node.roles, node.framework, stats.roles, component.roleCheck.
SCHEMA_VERSION = 3
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


def parse_file(data: bytes, path: str):
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
                    }
                )
                if body:
                    visit(body, (*owners, name))
            elif node.type in {"enum_body_declarations", "ERROR"}:
                visit(node, owners)
            # Method-local and anonymous classes do not have stable source FQNs.

    visit(tree.root_node)
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
    # JADX normally writes imports or fully qualified names for external types.
    if "." in name and first[:1].islower():
        return name, "external", []
    return None, "unresolved", [f"{p}.{name}" for p in owner["_wildcards"]]


class Cancelled(Exception):
    pass


class Project:
    def __init__(self, root, progress=lambda *_: None, cancelled=lambda: False, demo=False, manifest=None):
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
                classes, has_error = parse_file(data, relative)
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
            for key in list(node):
                if key.startswith("_"):
                    del node[key]
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
                "undeclaredComponentClasses": sum(1 for n in self.nodes.values() if n.get("undeclaredComponent")),
                "seconds": round(time.perf_counter() - started, 2),
            },
            "manifest": manifest_data,
        }

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
