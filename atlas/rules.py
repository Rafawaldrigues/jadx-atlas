"""Evaluate data-driven rules (atlas/data/rules/*.json) against collected code facts.

Results are *candidates*. Confidence (documented in the README):

- high: the receiver's declared type (or the created type) resolves through the
  file's imports/package to the API type, and the method matches;
- medium: the method matches and the file imports the API (or a hint type), but
  the receiver's type could not be determined (chained call, inherited field, lambda);
- low: only the method name matches, or an argument could not be evaluated.

A receiver whose type resolves to something else never matches.
"""

from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import re

from . import code_facts
from .roles import CONFIDENCE, NAMES

RULES_DIR = Path(__file__).resolve().parent / "data" / "rules"
SEVERITIES = ["info", "low", "medium", "high"]
SNIPPET = 160
MAX_PER_RULE_AND_CLASS = 20
# Argument shapes whose value cannot be known statically: the finding is kept with low confidence.
UNKNOWN_ARGUMENTS = {"identifier", "field", "call", "other", "string-concat", "concat", "flags"}


@lru_cache(maxsize=1)
def load_rules():
    rules = []
    for file in sorted(RULES_DIR.glob("*.json")):
        rules.extend(json.loads(file.read_text(encoding="utf-8")))
    seen = set()
    for rule in rules:
        if rule["id"] in seen:
            raise ValueError(f"duplicate rule: {rule['id']}")
        seen.add(rule["id"])
    return tuple(rules)


class RuleIndex:
    """What the extractor must keep, derived from the rules."""

    def __init__(self, rules=None):
        self.rules = rules or load_rules()
        self.call_methods, self.new_types, self.override_methods, self.identifiers = set(), set(), set(), set()
        self.string_patterns = []
        for rule in self.rules:
            match = rule["match"]
            if match["kind"] == "call":
                self.call_methods.update(match["methods"])
            elif match["kind"] == "new":
                self.new_types.update(t.rsplit(".", 1)[-1] for t in match["types"])
            elif match["kind"] == "override":
                self.override_methods.update(match["methods"])
            elif match["kind"] == "identifier":
                self.identifiers.update(match["names"])
            elif match["kind"] == "string":
                rule["_patterns"] = [(p["name"], re.compile(p["regex"])) for p in match.get("patterns", [])]
                rule["_exclude"] = re.compile(match["exclude"]) if match.get("exclude") else None
                self.string_patterns.append(rule)
        prefilters = [p["regex"] for rule in self.string_patterns for p in rule["match"].get("patterns", [])]
        if any("entropy" in rule["match"] for rule in self.string_patterns):
            prefilters.append(r"[A-Za-z0-9+/=_\-]{32,}")
        self.string_prefilter = (
            re.compile("|".join(f"(?:{p})" for p in prefilters)) if prefilters else re.compile(r"(?!)")
        )
        # Same alternatives on raw bytes (patterns are ASCII), applied before decoding each literal.
        self.string_prefilter_bytes = re.compile(self.string_prefilter.pattern.encode())
        self.identifier_pattern = code_facts.compile_identifier_pattern(self.identifiers) if self.identifiers else None
        self.secret_rules = [r for r in self.string_patterns if r["match"].get("mask")]
        self.call_method_bytes = {name.encode() for name in self.call_methods}
        self.override_method_bytes = {name.encode() for name in self.override_methods}


@lru_cache(maxsize=1)
def default_index():
    return RuleIndex()


def shannon(value):
    counts = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values()) if value else 0.0


def mask(secret):
    """First four characters and the length; the full value never enters the payload."""
    return f"{secret[:4]}…[{len(secret)} chars]"


def mask_text(value, index):
    for rule in index.secret_rules:
        for _, pattern in rule["_patterns"]:
            value = pattern.sub(lambda m: mask(m.group()), value)
    return value


class Evaluator:
    """Per-project evaluation: needs the resolver, the node map and the hierarchy for implicit receivers."""

    def __init__(self, nodes, resolve, hierarchy, index=None):
        self.nodes, self.resolve, self.hierarchy = nodes, resolve, hierarchy
        self.index = index or default_index()
        self.symbols = {key for key, node in nodes.items() if not node.get("external")}
        self.by_kind, self.by_name = {}, {}
        for rule in self.index.rules:
            match = rule["match"]
            self.by_kind.setdefault(match["kind"], []).append(rule)
            # Calls and overrides are looked up by method name, creations by type short name.
            names = (
                match.get("methods")
                if match["kind"] in {"call", "override"}
                else [t.rsplit(".", 1)[-1] for t in match.get("types", [])]
                if match["kind"] == "new"
                else []
            )
            for name in names:
                self.by_name.setdefault((match["kind"], name), []).append(rule)
        self.type_cache, self.hint_cache = {}, {}

    # -- type helpers -------------------------------------------------------------------------
    def type_of(self, name, owner):
        """FQN for a type name written in `owner`'s file, or None when unknown."""
        if not name or name == "?":
            return None
        key = (owner["id"], name)
        if key not in self.type_cache:
            target, status, _ = self.resolve(name.removesuffix("[]"), owner, self.symbols)
            self.type_cache[key] = target if status in {"resolved", "external"} else None
        return self.type_cache[key]

    def enclosing(self, owner):
        """The class and its lexically enclosing classes (inner classes may call outer methods)."""
        prefix = f"{owner['package']}." if owner.get("package") else ""
        names = [*owner.get("_owners", ()), owner["name"]]
        return [prefix + ".".join(names[:size]) for size in range(len(names), 0, -1)]

    def wildcard_match(self, name, owner, types):
        """`import android.webkit.*;` + `WebView` with no project type of that name."""
        short = name.rsplit(".", 1)[-1]
        packages = set(owner.get("_wildcards", []))
        return any(t.rsplit(".", 1)[-1] == short and t.rsplit(".", 1)[0] in packages for t in types)

    def is_api(self, fqn, types):
        if fqn in types:
            return True
        return bool(self.hierarchy.ancestors(fqn) & set(types))

    def imports_hint(self, owner, match):
        key = (owner["id"], id(match))
        if key not in self.hint_cache:
            self.hint_cache[key] = self._imports_hint(owner, match)
        return self.hint_cache[key]

    def _imports_hint(self, owner, match):
        hints = set(match.get("importHints", [])) | set(match.get("types", []))
        imported = {fqn for fqns in owner.get("_imports", {}).values() for fqn in fqns}
        packages = set(owner.get("_wildcards", []))
        return bool(hints & imported) or any(h.rsplit(".", 1)[0] in packages for h in hints)

    def receiver_confidence(self, receiver, owner, match):
        """Confidence rank, or 0 when the receiver is known to be something else."""
        types = match["types"]
        hint = CONFIDENCE["medium"] if self.imports_hint(owner, match) else CONFIDENCE["low"]
        kind = receiver["kind"]
        if kind in {"variable", "new"} and receiver.get("type"):
            fqn = self.type_of(receiver["type"], owner)
            if fqn:
                return CONFIDENCE["high"] if self.is_api(fqn, types) else 0
            if self.wildcard_match(receiver["type"], owner, types):
                return CONFIDENCE["high"]
            # Declared type we cannot resolve: matching short name is plausible, anything else is not the API.
            return hint if any(receiver["type"].rsplit(".", 1)[-1] == t.rsplit(".", 1)[-1] for t in types) else 0
        if kind == "variable":  # variable of unknown type (lambda parameter)
            return hint
        if kind == "name":
            name = receiver["name"]
            if name[:1].isupper() or "." in name:
                fqn = self.type_of(name, owner)
                if fqn:
                    return CONFIDENCE["high"] if self.is_api(fqn, types) else 0
                if self.wildcard_match(name, owner, types):
                    return CONFIDENCE["high"]
            return hint
        if kind == "implicit":
            # The method belongs to the class itself, one of its ancestors or an enclosing class.
            unknown = False
            for candidate in self.enclosing(owner):
                ancestors = self.hierarchy.ancestors(candidate)
                if set(types) & (ancestors | {candidate}):
                    return CONFIDENCE["high"]
                # No `extends` means java.lang.Object; a known framework class chain is also complete.
                if self.hierarchy.has_superclass(candidate) and not self.hierarchy.class_chain_known(candidate):
                    unknown = True
            return CONFIDENCE["low"] if unknown else 0
        if kind == "call":
            returned = match.get("returnTypes", {}).get(receiver.get("method"))
            if returned and self.is_api(returned, types):
                inner = receiver.get("inner", {})
                if inner.get("kind") == "name" and self.type_of(inner.get("name"), owner) == returned:
                    return CONFIDENCE["high"]  # static factory, e.g. Runtime.getRuntime()
                return CONFIDENCE["medium"]
            return hint
        return hint

    # -- argument predicates: True, False or None (cannot tell) ---------------------------------
    @staticmethod
    def check_argument(predicate, args):
        index = predicate["index"]
        if index >= len(args):
            return False
        arg = args[index]
        kind, value = arg["kind"], arg.get("value")
        if "equals" in predicate:
            if kind in {"bool", "string", "int"}:
                return value == predicate["equals"]
            return None if kind in UNKNOWN_ARGUMENTS else False
        if "in" in predicate:
            if kind in {"string", "int"}:
                options = predicate["in"]
                if predicate.get("ignoreCase") and isinstance(value, str):
                    return value.lower() in {str(o).lower() for o in options}
                return value in options
            return None if kind in UNKNOWN_ARGUMENTS else False
        if "regex" in predicate:
            if kind == "string":
                return re.search(predicate["regex"], value) is not None
            return None if kind in UNKNOWN_ARGUMENTS else False
        if predicate.get("notLiteral"):
            return kind not in {"string", "string-concat", "null"}
        if predicate.get("concat"):
            return kind == "concat"
        if predicate.get("literalish"):
            return kind in {"string", "bytes-literal", "array-literal", "int"}
        if "flagsWithout" in predicate:
            if kind == "int":
                return not value & predicate["flagsWithout"]
            if kind in {"field", "flags", "identifier"}:
                return predicate["flagName"] not in arg.get("text", "") if kind != "identifier" else None
            return None
        return False

    # -- evaluation --------------------------------------------------------------------------
    def evaluate(self, owner, exposed_component):
        findings, per_rule = [], {}
        for event in owner.get("_facts", ()):
            for rule, confidence, extra in self.match_event(event, owner, exposed_component):
                key = rule["id"]
                per_rule[key] = per_rule.get(key, 0) + 1
                if per_rule[key] > MAX_PER_RULE_AND_CLASS:
                    continue
                snippet = mask_text(event.get("snippet", ""), self.index)[:SNIPPET]
                finding = {
                    "ruleId": key,
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "confidence": NAMES[confidence],
                    "classId": owner["id"],
                    "file": owner["path"],
                    "line": event["line"],
                    "endLine": event["endLine"],
                    "snippet": snippet,
                    "inAnonymous": event["inAnonymous"],
                }
                if owner.get("_partial"):
                    finding["partial"] = True
                finding.update(extra)
                findings.append(finding)
        return findings

    def match_event(self, event, owner, exposed_component):
        kind = event["kind"]
        if kind == "call" or kind == "override":
            candidates = self.by_name.get((kind, event["method"]), ())
        elif kind == "new":
            candidates = self.by_name.get((kind, event["type"].rsplit(".", 1)[-1]), ())
        else:
            candidates = self.by_kind.get(kind, ())
        for rule in candidates:
            match = rule["match"]
            if match.get("context") == "exportedComponent" and not exposed_component:
                continue
            if kind == "call":
                if event["method"] not in match["methods"]:
                    continue
                if "argCount" in match and len(event["args"]) != match["argCount"]:
                    continue
                if not self.apply_arguments(match, event, CONFIDENCE["high"]):
                    continue  # cheap argument check first
                confidence = self.receiver_confidence(event["receiver"], owner, match)
            elif kind == "new":
                short = event["type"].rsplit(".", 1)[-1]
                if short not in {t.rsplit(".", 1)[-1] for t in match["types"]}:
                    continue
                if "argCount" in match and len(event["args"]) != match["argCount"]:
                    continue
                confidence = self.receiver_confidence({"kind": "new", "type": event["type"]}, owner, match)
            elif kind == "override":
                if event["method"] not in match["methods"]:
                    continue
                confidence = self.override_confidence(event, owner, match)
                if confidence and not self.body_matches(event, match):
                    confidence = 0
            elif kind == "identifier":
                confidence = CONFIDENCE["medium"] if event["name"] in match["names"] else 0
            elif kind == "string":
                yield from self.match_string(event, rule)
                continue
            else:
                continue
            if not confidence:
                continue
            confidence = self.apply_arguments(match, event, confidence)
            if confidence:
                yield rule, confidence, {}

    def apply_arguments(self, match, event, confidence):
        for predicate in match.get("args", ()):
            result = self.check_argument(predicate, event.get("args", []))
            if result is False:
                return 0
            if result is None:
                confidence = min(confidence, CONFIDENCE["low"])
        return confidence

    def override_confidence(self, event, owner, match):
        wanted = set(match["roles"])
        if event.get("anonymousType"):
            fqn = self.type_of(event["anonymousType"], owner)
            if fqn:
                return CONFIDENCE["high"] if wanted & self.hierarchy.roles_of(fqn) else 0
            return CONFIDENCE["low"] if wanted & self.hierarchy.roles_by_short_name(event["anonymousType"]) else 0
        if event["inAnonymous"]:
            return 0  # local class or lambda body: owner's roles do not apply
        best = 0
        for role in owner.get("roles", ()):
            if role["role"] in wanted:
                best = max(best, CONFIDENCE[role["confidence"]])
        return best

    @staticmethod
    def body_matches(event, match):
        if "body" in match:
            return event["body"] == match["body"]
        if "calls" in match:
            return bool(set(match["calls"]) & set(event["calls"]))
        return True

    def match_string(self, event, rule):
        match, value = rule["match"], event["value"]
        exclude = rule["_exclude"]
        if "entropy" in match:
            options = match["entropy"]
            if not options["minLength"] <= len(value) <= options["maxLength"] or re.search(r"\s", value):
                return
            if exclude and exclude.search(value):
                return
            if any(p.search(value) for r in self.index.secret_rules if r is not rule for _, p in r["_patterns"]):
                return  # already reported with a known format
            classes = sum(bool(re.search(p, value)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
            if classes < 3 or shannon(value) < options["min"]:
                return
            yield (
                rule,
                CONFIDENCE["low"],
                {
                    "detail": f"entropy {shannon(value):.1f} bits/char",
                    "secret": mask(value),
                    "_secretValue": value,
                },
            )
            return
        for name, pattern in rule["_patterns"]:
            found = pattern.search(value)
            if not found or (exclude and exclude.search(value)):
                continue
            extra = {"detail": name}
            if match.get("mask"):
                extra["secret"] = mask(found.group())
                extra["_secretValue"] = found.group()  # moved out of the finding by Project; never in the payload
            else:
                extra["value"] = value[:SNIPPET]
            yield rule, CONFIDENCE["high"] if match.get("mask") else CONFIDENCE["medium"], extra
            return


def summary(rule):
    """Public rule metadata for the payload and the UI."""
    keys = ("id", "title", "severity", "category", "description", "remediation", "falsePositives", "references")
    return {key: rule[key] for key in keys}
