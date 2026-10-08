"""Collect rule-relevant code facts from an already parsed Java tree.

Only calls, object creations, overrides, identifiers and strings that some rule
can match are kept (the rule index decides), so memory stays proportional to
candidate findings rather than to the code size. Tree-sitter queries run in C;
Python only walks upwards from the few matching nodes.

The receiver's *declared* type is looked up lexically (locals declared earlier
in an enclosing block, parameters, catch/for/resource variables, fields of the
enclosing classes). This is not type inference: unknown stays unknown.
"""

from __future__ import annotations

import re

from tree_sitter import Query, QueryCursor

SCOPES = {"block", "constructor_body", "switch_block_statement_group"}
METHODS = {"method_declaration", "constructor_declaration", "lambda_expression", "compact_constructor_declaration"}
CLASS_BODIES = {"class_body", "enum_body", "interface_body", "record_body", "enum_body_declarations"}
NAMED_DECLARATIONS = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
}
MAX_TEXT = 120

_QUERIES = {}


def _query(language):
    if language not in _QUERIES:
        _QUERIES[language] = Query(
            language,
            "(method_invocation name: (identifier) @call) (object_creation_expression) @new "
            "(method_declaration name: (identifier) @override) (string_literal) @string",
        )
    return _QUERIES[language]


def text(node, limit=MAX_TEXT):
    value = node.text.decode("utf-8", "replace") if node else ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def raw_type_text(node):
    """Declared type without generics/annotations/array dimensions."""
    if node is None:
        return None
    if node.type == "generic_type":
        return raw_type_text(node.named_children[0])
    if node.type == "annotated_type":
        return raw_type_text(node.named_children[-1])
    if node.type == "array_type":
        return raw_type_text(node.child_by_field_name("element")) + "[]"
    if node.type == "scoped_type_identifier":
        return ".".join(
            raw_type_text(c) for c in node.named_children if c.type not in {"annotation", "marker_annotation"}
        )
    return text(node)


def string_value(node):
    """Value of a string literal (escape sequences kept as written)."""
    return "".join(
        c.text.decode("utf-8", "replace")
        for c in node.named_children
        if c.type in {"string_fragment", "escape_sequence"}
    )


def _declarator_names(node):
    for child in node.named_children:
        if child.type == "variable_declarator":
            name = child.child_by_field_name("name")
            if name is not None:
                yield text(name), child


def _declared_in(container, name, before):
    """Type declared for `name` directly inside `container` (before byte offset `before`), else None."""
    kind = container.type
    if kind in SCOPES:
        found = None
        for child in container.named_children:
            if child.start_byte >= before:
                break
            if child.type == "local_variable_declaration":
                for declared, _ in _declarator_names(child):
                    if declared == name:
                        found = raw_type_text(child.child_by_field_name("type"))
        return found
    if kind in METHODS:
        parameters = container.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters else ():
            if parameter.type in {"formal_parameter", "spread_parameter"}:
                pname = parameter.child_by_field_name("name") or next(
                    (c for c in parameter.named_children if c.type == "variable_declarator"), None
                )
                if pname is not None and text(pname).split()[0] == name:
                    return raw_type_text(parameter.child_by_field_name("type") or parameter.named_children[0])
            elif parameter.type == "identifier" and text(parameter) == name:
                return "?"  # untyped lambda parameter: known variable, unknown type
        return None
    if kind == "enhanced_for_statement":
        pname = container.child_by_field_name("name")
        if pname is not None and text(pname) == name:
            return raw_type_text(container.child_by_field_name("type"))
    if kind == "catch_clause":
        for parameter in container.named_children:
            if parameter.type == "catch_formal_parameter":
                pname = parameter.child_by_field_name("name")
                if pname is not None and text(pname) == name:
                    types = [c for c in parameter.named_children if c.type == "catch_type"]
                    return raw_type_text(types[0].named_children[0]) if types and types[0].named_children else "?"
    if kind == "try_with_resources_statement":
        resources = container.child_by_field_name("resources")
        for resource in resources.named_children if resources else ():
            rname = resource.child_by_field_name("name")
            if rname is not None and text(rname) == name:
                return raw_type_text(resource.child_by_field_name("type"))
    if kind == "for_statement":
        init = container.child_by_field_name("init")
        if init is not None and init.type == "local_variable_declaration":
            for declared, _ in _declarator_names(init):
                if declared == name:
                    return raw_type_text(init.child_by_field_name("type"))
    return None


def _fields(body):
    fields = {}
    for child in body.named_children:
        if child.type in {"field_declaration", "constant_declaration"}:
            for declared, _ in _declarator_names(child):
                fields.setdefault(declared, raw_type_text(child.child_by_field_name("type")))
    return fields


def variable_type(node, name, field_cache=None):
    """Walk outwards from `node`; return declared type text, "?" (variable of unknown type) or None (not a variable)."""
    field_cache = {} if field_cache is None else field_cache
    current, before = node.parent, node.start_byte
    while current is not None:
        if current.type in CLASS_BODIES:
            # Field maps are built once per class body: large classes would otherwise be quadratic.
            key = (current.start_byte, current.end_byte)
            if key not in field_cache:
                field_cache[key] = _fields(current)
            found = field_cache[key].get(name)
        else:
            found = _declared_in(current, name, before)
        if found:
            return found
        current = current.parent
    return None


class FileFacts:
    """Per-file context: declarations with byte ranges and their constants."""

    def __init__(self, data, declarations):
        self.data = data
        # Innermost declaration wins: sort by start, scan the (few) declarations of the file.
        self.declarations = sorted(declarations, key=lambda d: (d["_start"], -d["_end"]))

    def owner(self, offset):
        found = None
        for declaration in self.declarations:
            if declaration["_start"] <= offset < declaration["_end"]:
                found = declaration
            elif declaration["_start"] > offset:
                break
        return found


def _anonymous_context(node, owner_start):
    """(inAnonymous, anonymous creation type text or None) between node and its named owner."""
    current, anonymous_type, inside = node.parent, None, False
    while current is not None and current.start_byte >= owner_start:
        if (
            current.type == "class_body"
            and current.parent is not None
            and current.parent.type == "object_creation_expression"
        ):
            inside = True
            if anonymous_type is None:
                anonymous_type = raw_type_text(current.parent.child_by_field_name("type"))
        elif current.type in NAMED_DECLARATIONS and current.parent is not None and current.parent.type == "block":
            inside = True  # local class: not indexed, attributed to the enclosing named class
        current = current.parent
    return inside, anonymous_type


def _argument(node, constants):
    kind = node.type
    if kind == "string_literal":
        return {"kind": "string", "value": string_value(node)}
    if kind in {"true", "false"}:
        return {"kind": "bool", "value": kind == "true"}
    if kind in {"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal"}:
        raw = text(node).rstrip("lL").replace("_", "")
        try:
            if raw.lower().startswith(("0x", "0b")):
                value = int(raw, 0)
            elif len(raw) > 1 and raw.startswith("0"):
                value = int(raw, 8)  # Java octal literal
            else:
                value = int(raw)
            return {"kind": "int", "value": value}
        except ValueError:
            return {"kind": "other", "text": text(node, 60)}
    if kind == "null_literal":
        return {"kind": "null"}
    if kind == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and operator.type == "+":
            leaves, stack = [], [node]
            while stack:
                current = stack.pop()
                op = current.child_by_field_name("operator") if current.type == "binary_expression" else None
                if op is not None and op.type == "+":
                    stack.extend((current.child_by_field_name("left"), current.child_by_field_name("right")))
                else:
                    leaves.append(current)
            literal = all(
                leaf.type in {"string_literal", "decimal_integer_literal", "character_literal"} for leaf in leaves
            )
            return {"kind": "string-concat" if literal else "concat", "text": text(node, 60)}
        if operator is not None and operator.type == "|":
            return {"kind": "flags", "text": text(node, 120)}
        return {"kind": "other", "text": text(node, 60)}
    if kind == "method_invocation":
        name = node.child_by_field_name("name")
        target = node.child_by_field_name("object")
        if name is not None and text(name) == "getBytes" and target is not None and target.type == "string_literal":
            return {"kind": "bytes-literal", "value": string_value(target)}
        return {"kind": "call", "text": text(node, 60)}
    if kind in {"array_creation_expression", "array_initializer"}:
        initializer = node if kind == "array_initializer" else node.child_by_field_name("value")
        if (
            initializer is not None
            and initializer.named_children
            and all(
                c.type.endswith("integer_literal") or c.type in {"character_literal", "unary_expression"}
                for c in initializer.named_children
            )
        ):
            return {"kind": "array-literal", "text": text(node, 60)}
        return {"kind": "array", "text": text(node, 60)}
    if kind == "identifier":
        name = text(node)
        if name in constants:
            return {**constants[name], "constant": name}
        return {"kind": "identifier", "text": name}
    if kind == "field_access":
        return {"kind": "field", "text": text(node, 120)}
    return {"kind": "other", "text": text(node, 60)}


def _constants(body):
    """static final fields with literal values, per class body (used to resolve arguments)."""
    found = {}
    for child in body.named_children if body is not None else ():
        if child.type != "field_declaration":
            continue
        modifiers = next((c for c in child.named_children if c.type == "modifiers"), None)
        words = text(modifiers, 400).split() if modifiers is not None else []
        if "static" not in words or "final" not in words:
            continue
        for name, declarator in _declarator_names(child):
            value = declarator.child_by_field_name("value")
            if value is None:
                continue
            argument = _argument(value, {})
            if argument["kind"] in {"string", "int", "bool", "array-literal", "bytes-literal"}:
                found[name] = argument
    return found


def _receiver(call, field_cache=None):
    """Describe the receiver of a method call."""
    target = call.child_by_field_name("object")
    if target is None:
        return {"kind": "implicit"}
    if target.type == "identifier":
        name = text(target)
        declared = variable_type(call, name, field_cache)
        if declared:
            return {"kind": "variable", "name": name, "type": None if declared == "?" else declared}
        return {"kind": "name", "name": name}  # a type name (static call) or an inherited field: unknown
    if target.type == "field_access":
        obj, field = target.child_by_field_name("object"), target.child_by_field_name("field")
        if obj is not None and obj.type == "this" and field is not None:
            declared = variable_type(call, text(field), field_cache)
            return {"kind": "variable", "name": text(field), "type": None if declared in {None, "?"} else declared}
        return {"kind": "name", "name": text(target)}
    if target.type == "scoped_identifier":
        return {"kind": "name", "name": text(target)}
    if target.type == "method_invocation":
        inner = target.child_by_field_name("name")
        return {
            "kind": "call",
            "method": text(inner) if inner is not None else None,
            "inner": _receiver(target, field_cache),
            "text": text(target, 80),
        }
    if target.type == "this":
        return {"kind": "implicit"}
    if target.type == "object_creation_expression":
        return {"kind": "new", "type": raw_type_text(target.child_by_field_name("type"))}
    if target.type == "string_literal":
        return {"kind": "new", "type": "String"}
    return {"kind": "expression", "text": text(target, 80)}


def _body_shape(method):
    body = method.child_by_field_name("body")
    if body is None:
        return "abstract", []
    statements = [c for c in body.named_children if c.type not in {"line_comment", "block_comment"}]
    calls = []
    stack = [body]
    while stack:
        current = stack.pop()
        if current.type == "method_invocation":
            name = current.child_by_field_name("name")
            if name is not None:
                calls.append(text(name))
        if current.type not in {"class_body", "lambda_expression"}:
            stack.extend(current.named_children)
    if not statements:
        return "empty", calls
    if len(statements) == 1 and statements[0].type == "return_statement":
        value = statements[0].named_children
        if value and value[0].type == "true":
            return "returns-true", calls
    if all(s.type == "throw_statement" for s in statements):
        return "throws", calls
    return "other", calls


def collect(language, tree, data, declarations, index):
    """Attach `_facts` (list of events) to each declaration. `index` comes from atlas.rules.RuleIndex."""
    for declaration in declarations:
        declaration["_facts"] = []
    if not declarations:
        return
    facts = FileFacts(data, declarations)
    constants_by_body, field_cache, constants_by_parent = {}, {}, {}

    def constants_for(node):
        """Constants of every enclosing class body (inner first), memoised per innermost body."""
        current = node.parent
        while current is not None and current.type not in CLASS_BODIES:
            current = current.parent
        key = (current.start_byte, current.end_byte) if current is not None else None
        if key in constants_by_parent:
            return constants_by_parent[key]
        merged = {}
        while current is not None:
            if current.type in CLASS_BODIES:
                body_key = current.start_byte
                if body_key not in constants_by_body:
                    constants_by_body[body_key] = _constants(current)
                for name, value in constants_by_body[body_key].items():
                    merged.setdefault(name, value)
            current = current.parent
        constants_by_parent[key] = merged
        return merged

    lines = []

    def emit(node, event):
        owner = facts.owner(node.start_byte)
        if owner is None:
            return
        if not lines:
            lines.extend(data.split(b"\n"))
        row = node.start_point.row
        event["snippet"] = lines[row].decode("utf-8", "replace").strip()[:400] if row < len(lines) else ""
        anonymous, anonymous_type = _anonymous_context(node, owner["_start"])
        event.update(line=node.start_point.row + 1, endLine=node.end_point.row + 1, inAnonymous=anonymous)
        if anonymous_type and event["kind"] == "override":
            event["anonymousType"] = anonymous_type
        owner["_facts"].append(event)

    captures = QueryCursor(_query(language)).captures(tree.root_node)
    for name_node in captures.get("call", ()):
        method = text(name_node)
        if method not in index.call_methods:
            continue
        call = name_node.parent
        arguments = call.child_by_field_name("arguments")
        constants = constants_for(call)
        args = (
            [
                _argument(a, constants)
                for a in arguments.named_children
                if a.type not in {"line_comment", "block_comment"}
            ]
            if arguments
            else []
        )
        emit(call, {"kind": "call", "method": method, "receiver": _receiver(call, field_cache), "args": args})
    for creation in captures.get("new", ()):
        type_text = raw_type_text(creation.child_by_field_name("type"))
        if not type_text or type_text.rsplit(".", 1)[-1] not in index.new_types:
            continue
        arguments = creation.child_by_field_name("arguments")
        constants = constants_for(creation)
        args = (
            [
                _argument(a, constants)
                for a in arguments.named_children
                if a.type not in {"line_comment", "block_comment"}
            ]
            if arguments
            else []
        )
        emit(creation, {"kind": "new", "type": type_text, "args": args})
    for name_node in captures.get("override", ()):
        method = text(name_node)
        if method not in index.override_methods:
            continue
        declaration = name_node.parent
        parameters = declaration.child_by_field_name("parameters")
        shape, calls = _body_shape(declaration)
        emit(
            declaration,
            {
                "kind": "override",
                "method": method,
                "body": shape,
                "calls": sorted(set(calls)),
                "params": len([p for p in parameters.named_children if p.type == "formal_parameter"])
                if parameters
                else 0,
            },
        )
    if index.string_patterns:
        for literal in captures.get("string", ()):
            if _inside_annotation(literal):
                continue  # e.g. Kotlin @Metadata(d1 = ...): compiler data, not app strings
            value = string_value(literal)
            if index.string_prefilter.search(value):
                emit(literal, {"kind": "string", "value": value[:4096]})
    if index.identifiers:
        for match in index.identifier_pattern.finditer(data):
            node = tree.root_node.descendant_for_byte_range(match.start(), match.end())
            if node is not None and node.type == "identifier":
                emit(node, {"kind": "identifier", "name": match.group().decode()})


def _inside_annotation(node):
    current = node.parent
    while current is not None and current.type not in {"class_body", "block", "program"}:
        if current.type in {"annotation", "marker_annotation"}:
            return True
        current = current.parent
    return False


def compile_identifier_pattern(names):
    return re.compile(rb"\b(?:" + b"|".join(re.escape(n.encode()) for n in sorted(names)) + rb")\b")
