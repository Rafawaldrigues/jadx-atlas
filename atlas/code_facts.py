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

import bisect
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
MAX_SEARCH_STRINGS = 60

_QUERIES = {}

# Phase 5 "uses": object creation, static calls and the declared type of locals/fields.
# Parameters, return types and casts are left out on purpose (volume; see docs/DECISIONS.md D-020).
TYPE_REFERENCE_QUERY = """
(object_creation_expression type: (_) @type)
(local_variable_declaration type: (_) @type)
(field_declaration type: (_) @type)
(method_invocation object: (identifier) @static)
(method_invocation object: (field_access) @static)
"""


def _query(language, index):
    """One query (one tree traversal) for rule facts, Intent calls and type references.

    `#any-of?` lets tree-sitter drop irrelevant method names before Python sees them.
    """
    key = (language, id(index))
    if key not in _QUERIES:
        calls = sorted(index.call_methods | set(INTENT_SINKS) | {"registerReceiver"} | INTENT_READS)
        overrides = sorted(index.override_methods) or ["__none__"]
        quoted = lambda names: " ".join(f'"{name}"' for name in names)  # noqa: E731
        _QUERIES[key] = Query(
            language,
            f"((method_invocation name: (identifier) @call) (#any-of? @call {quoted(calls)})) "
            f"((method_declaration name: (identifier) @override) (#any-of? @override {quoted(overrides)})) "
            "(object_creation_expression) @new (string_literal) @string " + TYPE_REFERENCE_QUERY,
        )
    return _QUERIES[key]


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
        # Innermost declaration wins: sorted by start, the innermost container is the closest one before `offset`.
        self.declarations = sorted(declarations, key=lambda d: (d["_start"], -d["_end"]))
        self.starts = [d["_start"] for d in self.declarations]

    def owner(self, offset):
        index = bisect.bisect_right(self.starts, offset) - 1
        while index >= 0:
            declaration = self.declarations[index]
            if offset < declaration["_end"]:
                return declaration
            index -= 1
        return None


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


def _string_constants(body):
    """`static final String X = "..."` of one class body; a cheaper subset of _constants()."""
    found = {}
    for child in body.named_children:
        if child.type != "field_declaration":
            continue
        raw = child.text
        if b"static" not in raw or b"final" not in raw or b'"' not in raw:
            continue
        for declarator in child.named_children:
            if declarator.type == "variable_declarator":
                value = declarator.child_by_field_name("value")
                name = declarator.child_by_field_name("name")
                if value is not None and name is not None and value.type == "string_literal":
                    found[name.text.decode()] = string_value(value)
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

    captures = QueryCursor(_query(language, index)).captures(tree.root_node)
    flows = {}

    def flow_for(node):
        body = enclosing_method_body(node)
        if body is None:
            return None
        key = (body.start_byte, body.end_byte)
        if key not in flows:
            flows[key] = _MethodFlow(body)
        return flows[key]

    # Compare raw bytes first: most method calls in a file are irrelevant and should cost one set lookup.
    wanted = index.call_method_bytes | INTENT_METHOD_BYTES
    for name_node in captures.get("call", ()):
        if name_node.text not in wanted:
            continue
        method = name_node.text.decode()
        if method in INTENT_SINKS or method == "registerReceiver" or method in INTENT_READS:
            call = name_node.parent
            arguments = call.child_by_field_name("arguments")
            args = (
                [a for a in arguments.named_children if a.type not in {"line_comment", "block_comment"}]
                if arguments
                else []
            )
            if method in INTENT_READS:
                emit(call, {"kind": "reads-intent"})
            elif method == "registerReceiver" and len(args) >= 2:
                constants = constants_for(call)
                receiver_arg, flow = args[0], flow_for(call)
                receiver_type = (
                    raw_type_text(receiver_arg.child_by_field_name("type"))
                    if receiver_arg.type == "object_creation_expression"
                    else None
                )
                if receiver_type is None and receiver_arg.type == "identifier":
                    declared = variable_type(call, text(receiver_arg), field_cache)
                    receiver_type = None if declared in {None, "?"} else declared
                    exact = False
                else:
                    exact = receiver_type is not None
                emit(
                    call,
                    {
                        "kind": "register",
                        "receiverType": receiver_type,
                        "exact": exact,
                        "actions": _filter_actions(args[1], call.start_byte, flow, constants),
                        "text": text(receiver_arg, 60),
                    },
                )
            elif method in INTENT_SINKS and len(args) > INTENT_SINKS[method]:
                receiver = call.child_by_field_name("object")
                pending = method in PENDING_INTENT_SINKS
                if pending and (receiver is None or text(receiver).rsplit(".", 1)[-1] != "PendingIntent"):
                    pass  # Fragment.getActivity() and friends are not Intent sinks
                else:
                    constants = constants_for(call)
                    intent = _describe_intent(args[INTENT_SINKS[method]], call.start_byte, flow_for(call), constants)
                    via = f"PendingIntent.{method}" if pending else method
                    emit(
                        call,
                        {
                            "kind": "intent",
                            "via": via,
                            "intent": intent,
                            "argument": text(args[INTENT_SINKS[method]], 60),
                        },
                    )
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
        if name_node.text not in index.override_method_bytes:
            continue
        method = name_node.text.decode()
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
    # Type references for "uses" edges: {type text: first line}, resolved per owner after indexing.
    for declaration in declarations:
        declaration["_uses"] = {}
    references = {name: captures.get(name, ()) for name in ("type", "static")}
    primitives = {
        b"int",
        b"long",
        b"short",
        b"byte",
        b"char",
        b"boolean",
        b"float",
        b"double",
        b"void",
        b"var",
        b"String",
        b"Object",
    }
    seen = {}
    for capture, nodes in references.items():
        for node in nodes:
            raw = node.text
            # Fast path: plain identifiers (the vast majority) need no tree walk; lowercase ones are variables.
            # Obfuscated classes are lowercase (`new a()`), so case only matters for static-call receivers,
            # where a lowercase identifier is almost always a variable (`list.add(...)`).
            if node.type in {"type_identifier", "identifier"}:
                if raw in primitives or (capture == "static" and not raw[:1].isupper()):
                    continue
                name = raw
            else:
                if capture == "static" and not raw[:1].isupper() and b"." not in raw:
                    continue
                name = (raw_type_text(node) or "").replace("[]", "").encode()
                if not name or name in primitives:
                    continue
            owner = facts.owner(node.start_byte)
            if owner is None:
                continue
            key = (id(owner), name)
            if key not in seen:
                seen[key] = True
                decoded = name.decode("utf-8", "replace")
                if decoded != owner["name"]:
                    owner["_uses"].setdefault(decoded, node.start_point.row + 1)
    # Project-wide string constants (for `Actions.GO` style references resolved after indexing).
    for declaration in declarations:
        node = tree.root_node.descendant_for_byte_range(declaration["_start"], declaration["_end"])
        body = node.child_by_field_name("body") if node is not None else None
        declaration["_string_constants"] = _string_constants(body) if body is not None else {}
    # Search index (phase 8): a bounded sample of each class's string literals, raw text, no escapes decoded.
    for declaration in declarations:
        declaration["_strings"] = []
    for literal in captures.get("string", ()):
        raw = literal.text.strip(b'"')
        if 2 <= len(raw) <= 300:
            owner = facts.owner(literal.start_byte)
            if owner is not None and len(owner["_strings"]) < MAX_SEARCH_STRINGS:
                owner["_strings"].append(raw[:120])
    if index.string_patterns:
        for literal in captures.get("string", ()):
            if not index.string_prefilter_bytes.search(literal.text.strip(b'"')):
                continue  # most literals: rejected on raw bytes, never decoded
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


# --- Intents (phase 4): simple intra-method flow, no interprocedural analysis -------------------
INTENT_SINKS = {
    "startActivity": 0,
    "startActivityForResult": 0,
    "startActivityIfNeeded": 0,
    "startService": 0,
    "startForegroundService": 0,
    "bindService": 0,
    "sendBroadcast": 0,
    "sendOrderedBroadcast": 0,
    "sendStickyBroadcast": 0,
    "getActivity": 2,
    "getService": 2,
    "getBroadcast": 2,
    "getForegroundService": 2,
}
PENDING_INTENT_SINKS = {"getActivity", "getService", "getBroadcast", "getForegroundService"}
INTENT_MUTATORS = {"setClass", "setClassName", "setComponent", "setAction", "setPackage"}
INTENT_READS = {"getIntent"}
INTENT_METHOD_BYTES = {name.encode() for name in (*INTENT_SINKS, "registerReceiver", *INTENT_READS)}


def _class_literal(node):
    """`X.class` → "X" (type text) or None. JADX writes `(Class<?>) X.class`, so casts are unwrapped."""
    while node is not None and node.type in {"cast_expression", "parenthesized_expression"}:
        node = (
            node.child_by_field_name("value")
            if node.type == "cast_expression"
            else (node.named_children[0] if node.named_children else None)
        )
    if node is not None and node.type == "class_literal":
        return raw_type_text(node.named_children[0]) if node.named_children else None
    return None


def _value(node, constants):
    """String value (or a reference to resolve later) for an action/class-name argument."""
    argument = _argument(node, constants)
    if argument["kind"] == "string":
        return {"value": argument["value"], **({"constant": argument["constant"]} if "constant" in argument else {})}
    if argument["kind"] == "field":
        return {"reference": argument["text"]}  # e.g. Actions.GO: resolved against project constants later
    return {"unresolved": argument.get("text", argument["kind"])}


def _apply(intent, method, args, constants):
    """Apply an Intent constructor/mutator call to the description."""
    if method == "setClass" and len(args) == 2:
        intent["targetType"] = _class_literal(args[1]) or intent.get("targetType")
    elif method == "setClassName" and len(args) == 2:
        intent["targetName"] = _value(args[1], constants)
    elif method == "setComponent" and args:
        component = args[0]
        if component.type == "object_creation_expression" and raw_type_text(
            component.child_by_field_name("type")
        ).endswith("ComponentName"):
            inner = component.child_by_field_name("arguments")
            inner = (
                [a for a in inner.named_children if a.type not in {"line_comment", "block_comment"}] if inner else []
            )
            if len(inner) == 2:
                literal = _class_literal(inner[1])
                if literal:
                    intent["targetType"] = literal
                else:
                    intent["targetName"] = _value(inner[1], constants)
        else:
            intent["targetName"] = {"unresolved": text(component, 60)}
    elif method == "setAction" and args:
        intent["action"] = _value(args[0], constants)
    elif method == "setPackage" and args:
        intent["package"] = _value(args[0], constants)


def _intent_from_creation(node, constants):
    args = node.child_by_field_name("arguments")
    args = [a for a in args.named_children if a.type not in {"line_comment", "block_comment"}] if args else []
    intent = {}
    if len(args) == 2 and _class_literal(args[1]):
        intent["targetType"] = _class_literal(args[1])  # Intent(Context, Class)
    elif len(args) == 4 and _class_literal(args[3]):
        intent["targetType"] = _class_literal(args[3])  # Intent(String action, Uri, Context, Class)
        intent["action"] = _value(args[0], constants)
    elif args:
        # Intent(String action[, Uri]); a plain variable may also be Intent(Intent): the value stays unresolved.
        intent["action"] = _value(args[0], constants)
    return intent


class _MethodFlow:
    """Bindings and mutations of Intent/IntentFilter variables in one method, in document order."""

    def __init__(self, body):
        self.events = []  # (start_byte, kind, name, node)
        stack = [body]
        while stack:
            node = stack.pop()
            if node.type == "local_variable_declaration":
                for name, declarator in _declarator_names(node):
                    value = declarator.child_by_field_name("value")
                    if value is not None:
                        self.events.append((node.start_byte, "bind", name, value))
            elif node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    self.events.append((node.start_byte, "bind", text(left), node.child_by_field_name("right")))
            elif node.type == "method_invocation":
                target, name = node.child_by_field_name("object"), node.child_by_field_name("name")
                if target is not None and target.type == "identifier" and name is not None:
                    self.events.append((node.start_byte, "call", text(target), node))
            if node.type not in {"class_body"}:
                stack.extend(node.named_children)
        self.events.sort(key=lambda e: e[0])

    def history(self, name, before):
        """Latest binding of `name` before `before`, then the calls on it until `before`."""
        bound, calls = None, []
        for start, kind, variable, node in self.events:
            if start >= before:
                break
            if variable != name:
                continue
            if kind == "bind":
                bound, calls = node, []
            elif bound is not None:
                calls.append(node)
        return bound, calls


def _describe_intent(expression, before, flow, constants, depth=0):
    """Intent description for an argument expression, or None when it is not traceable here."""
    if expression is None or depth > 8:
        return None
    if expression.type == "parenthesized_expression" and expression.named_children:
        return _describe_intent(expression.named_children[0], before, flow, constants, depth + 1)
    if expression.type == "cast_expression":
        return _describe_intent(expression.child_by_field_name("value"), before, flow, constants, depth + 1)
    if expression.type == "object_creation_expression":
        if (raw_type_text(expression.child_by_field_name("type")) or "").rsplit(".", 1)[-1] != "Intent":
            return None
        return _intent_from_creation(expression, constants)
    if expression.type == "method_invocation":
        # Chained builder: new Intent(...).setAction(...).putExtra(...)
        name = text(expression.child_by_field_name("name"))
        base = _describe_intent(expression.child_by_field_name("object"), before, flow, constants, depth + 1)
        if base is not None and name in INTENT_MUTATORS | {
            "putExtra",
            "putExtras",
            "addFlags",
            "setFlags",
            "setData",
            "setDataAndType",
            "addCategory",
            "setType",
        }:
            arguments = expression.child_by_field_name("arguments")
            _apply(
                base,
                name,
                [a for a in arguments.named_children if a.type not in {"line_comment", "block_comment"}]
                if arguments
                else [],
                constants,
            )
            return base
        return None
    if expression.type == "identifier" and flow is not None:
        bound, calls = flow.history(text(expression), before)
        intent = (
            _describe_intent(bound, bound.start_byte if bound is not None else before, flow, constants, depth + 1)
            if bound is not None
            else None
        )
        if intent is None:
            return None
        for call in calls:
            name = text(call.child_by_field_name("name"))
            arguments = call.child_by_field_name("arguments")
            _apply(
                intent,
                name,
                [a for a in arguments.named_children if a.type not in {"line_comment", "block_comment"}]
                if arguments
                else [],
                constants,
            )
        return intent
    return None


def _filter_actions(expression, before, flow, constants):
    """Actions of an IntentFilter argument: new IntentFilter("a") and filter.addAction("b") in the method."""
    if expression is None:
        return None
    if expression.type == "object_creation_expression":
        args = expression.child_by_field_name("arguments")
        args = [a for a in args.named_children if a.type not in {"line_comment", "block_comment"}] if args else []
        return [_value(args[0], constants)] if args else []
    if expression.type == "identifier" and flow is not None:
        bound, calls = flow.history(text(expression), before)
        actions = _filter_actions(bound, before, flow, constants) if bound is not None else None
        if actions is None:
            return None
        for call in calls:
            if text(call.child_by_field_name("name")) == "addAction":
                arguments = call.child_by_field_name("arguments")
                if arguments is not None and arguments.named_children:
                    actions.append(_value(arguments.named_children[0], constants))
        return actions
    return None


def enclosing_method_body(node):
    current = node.parent
    while current is not None:
        if current.type in {"method_declaration", "constructor_declaration", "lambda_expression"}:
            return current.child_by_field_name("body")
        if current.type in CLASS_BODIES:
            return None  # field initialiser: no method flow
        current = current.parent
    return None
