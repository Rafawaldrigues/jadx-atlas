"""Minimal JSON Schema checker for the subset used by docs/schema (no extra dependency).

Supports: type (incl. lists and "integer"), required, properties, items, enum,
minimum, maxLength, oneOf and local $ref ("#/$defs/...").
"""

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, kind):
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[kind])


def errors(value, schema, root=None, where="$"):
    root = root or schema
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            target = target[part]
        return errors(value, target, root, where)
    found = []
    if "oneOf" in schema:
        if sum(not errors(value, option, root, where) for option in schema["oneOf"]) != 1:
            found.append(f"{where}: does not match exactly one oneOf")
        return found
    kinds = schema.get("type")
    if kinds and not any(_is(value, k) for k in ([kinds] if isinstance(kinds, str) else kinds)):
        return [f"{where}: type {type(value).__name__} is not {kinds}"]
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{where}: {value!r} not in {schema['enum']}")
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        found.append(f"{where}: {value} < {schema['minimum']}")
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        found.append(f"{where}: text longer than {schema['maxLength']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                found.append(f"{where}: missing '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                found.extend(errors(value[key], sub, root, f"{where}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            found.extend(errors(item, schema["items"], root, f"{where}[{index}]"))
    return found
