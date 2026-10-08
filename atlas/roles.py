"""Assign roles (Activity, TrustManager, ...) from the transitive ancestor chain.

Roles follow resolved ids only, never short names. Project edges come first;
known framework types continue the chain through atlas/data/framework_hierarchy.json
(generated from javap; see scripts/framework_hierarchy.py). A path's confidence is
the weakest edge on it: resolved/external and framework edges are `high`, an
ambiguous edge is `medium` (2 candidates) or `low` (more), unresolved edges stop.
"""

from __future__ import annotations

from functools import lru_cache
import heapq
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
CONFIDENCE = {"high": 3, "medium": 2, "low": 1}
NAMES = {value: key for key, value in CONFIDENCE.items()}


@lru_cache(maxsize=1)
def framework():
    return json.loads((DATA / "framework_hierarchy.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def roles():
    return json.loads((DATA / "roles.json").read_text(encoding="utf-8"))["roles"]


def parent_links(nodes, edges):
    """child id -> [(parent id, confidence rank, via)]."""
    links = {}
    for edge in edges:
        status = edge["resolution"]
        if status in {"resolved", "external"}:
            links.setdefault(edge["source"], []).append((edge["target"], CONFIDENCE["high"], edge["kind"]))
        elif status == "ambiguous":
            candidates = nodes.get(edge["target"], {}).get("candidates", [])
            rank = CONFIDENCE["medium"] if len(candidates) <= 2 else CONFIDENCE["low"]
            for candidate in candidates:
                links.setdefault(edge["source"], []).append(
                    (candidate, rank, f"{edge['kind']} (ambíguo: {len(candidates)} candidatos)")
                )
    for type_id, info in framework()["types"].items():
        # A class shipped in the sources (e.g. bundled androidx) is described by its own edges.
        if type_id in nodes and not nodes[type_id].get("external"):
            continue
        for parent in info["extends"] + info["implements"]:
            links.setdefault(type_id, []).append((parent, CONFIDENCE["high"], f"framework ({info['source']})"))
    return links


def assign(nodes, edges):
    """Set node["roles"] on project (non-external) nodes. Returns {role id: count}."""
    children = {}
    for child, parents in parent_links(nodes, edges).items():
        for parent, rank, via in parents:
            children.setdefault(parent, []).append((child, rank, via))
    counts = {}
    for role in roles():
        # Dijkstra on (highest confidence, then shortest path) from the defining types downwards.
        best, previous, heap, settled = {}, {}, [], set()
        for type_id in role["types"]:
            best[type_id] = (CONFIDENCE["high"], 0)
            heapq.heappush(heap, (-CONFIDENCE["high"], 0, type_id))
        while heap:
            negative_rank, depth, current = heapq.heappop(heap)
            if current in settled:
                continue  # cycles and duplicate pushes end here
            settled.add(current)
            for child, rank, via in children.get(current, ()):
                candidate = (min(-negative_rank, rank), depth + 1)
                known = best.get(child)
                if child in settled or (known and (known[0], -known[1]) >= (candidate[0], -candidate[1])):
                    continue
                best[child], previous[child] = candidate, (current, via)
                heapq.heappush(heap, (-candidate[0], candidate[1], child))
        for node_id in settled:
            node = nodes.get(node_id)
            if not node or node.get("external") or node_id in role["types"]:
                continue
            path, vias, current = [node_id], [], node_id
            while current in previous:
                current, via = previous[current]
                path.append(current)
                vias.append(via)
            node.setdefault("roles", []).append(
                {
                    "role": role["id"],
                    "label": role["label"],
                    "confidence": NAMES[best[node_id][0]],
                    "path": path,
                    "via": vias,
                }
            )
            counts[role["id"]] = counts.get(role["id"], 0) + 1
    return counts


def framework_kind(type_id):
    info = framework()["types"].get(type_id)
    return (info["kind"], info["source"]) if info else (None, None)


class Hierarchy:
    """Memoised ancestor sets over project edges plus the framework table (any confidence)."""

    def __init__(self, nodes, edges):
        self.links = parent_links(nodes, edges)
        self.cache = {}
        self.role_types = {}
        for role in roles():
            for type_id in role["types"]:
                self.role_types.setdefault(type_id, set()).add(role["id"])

    def ancestors(self, type_id):
        if type_id not in self.cache:
            seen, stack = set(), [type_id]
            while stack:
                for parent, _, _ in self.links.get(stack.pop(), ()):
                    if parent not in seen:
                        seen.add(parent)
                        stack.append(parent)
            seen.discard(type_id)
            self.cache[type_id] = seen
        return self.cache[type_id]

    def has_superclass(self, type_id):
        return any(via == "extends" or via.startswith("extends ") for _, _, via in self.links.get(type_id, ()))

    def class_chain_known(self, type_id):
        """True when a framework *class* is an ancestor, i.e. the superclass chain ends in known territory."""
        types = framework()["types"]
        return any(types.get(a, {}).get("kind", "interface") != "interface" for a in self.ancestors(type_id))

    def roles_of(self, type_id):
        found = set()
        for candidate in self.ancestors(type_id) | {type_id}:
            found |= self.role_types.get(candidate, set())
        return found

    def roles_by_short_name(self, name):
        short = name.rsplit(".", 1)[-1]
        found = set()
        for type_id in framework()["types"]:
            if type_id.rsplit(".", 1)[-1] == short:
                found |= self.roles_of(type_id)
        return found
