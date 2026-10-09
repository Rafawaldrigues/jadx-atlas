"""Possible paths from an entry point to a sensitive class (phase 5).

The graph is an over-approximation built from resolved facts (Intent edges,
type references and, optionally, inheritance). A path is "possible", never a
proof of reachability or exploitability; there is no data-flow analysis.
"""

from __future__ import annotations

from collections import deque
import time

KIND_ORDER = {"launches": 0, "sends_action": 1, "registers_receiver": 2, "uses": 3, "extends": 4}
CONFIDENCE = {"high": 3, "medium": 2, "low": 1}
NOTE = "possible path; not proof of reachability or exploitability"
LIMITS = {"maxDepth": 6, "maxPathsPerTarget": 3, "maxPaths": 30, "maxSeconds": 2.0, "maxExpansionsPerNode": 3}


class FlowGraph:
    """Adjacency lists: source id -> sorted [(target, kind, via, line, confidence)]."""

    def __init__(self):
        self.adjacency = {}

    def add(self, source, target, kind, via, line, confidence):
        if source != target:
            self.adjacency.setdefault(source, []).append((target, kind, via, line, confidence))

    def freeze(self):
        for edges in self.adjacency.values():
            # Deterministic: strongest kinds first, then target id, then line.
            edges.sort(key=lambda e: (KIND_ORDER.get(e[1], 9), e[0], e[3] or 0, e[2]))
        return self

    def neighbours(self, node, kinds):
        return [edge for edge in self.adjacency.get(node, ()) if edge[1] in kinds]


def find_paths(graph, entry, targets, kinds, limits=None, clock=time.perf_counter):
    """Breadth-first search for simple paths, shortest first, with hard limits.

    Every node may be expanded at most `maxExpansionsPerNode` times, which keeps the
    search linear-ish on dense graphs while still returning alternative routes.
    """
    limits = {**LIMITS, **(limits or {})}
    targets = set(targets)
    started = clock()
    found, per_target, expansions, sequences = [], {}, {}, set()
    truncated = None
    queue = deque([(entry, ())])
    while queue:
        if clock() - started > limits["maxSeconds"]:
            truncated = "time"
            break
        node, steps = queue.popleft()
        if node in targets and steps:
            sequence = (entry, *(s["to"] for s in steps))
            # Same classes through a weaker edge kind (e.g. uses after launches) adds nothing: BFS saw the best first.
            if sequence not in sequences and per_target.get(node, 0) < limits["maxPathsPerTarget"]:
                sequences.add(sequence)
                per_target[node] = per_target.get(node, 0) + 1
                confidence = min((s["confidence"] for s in steps), key=lambda c: CONFIDENCE[c])
                found.append({"target": node, "length": len(steps), "confidence": confidence, "steps": list(steps)})
                if len(found) >= limits["maxPaths"]:
                    truncated = "total paths"
                    break
            continue  # a target ends the path (the next sensitive class gets its own path)
        if len(steps) >= limits["maxDepth"]:
            continue
        expansions[node] = expansions.get(node, 0) + 1
        if expansions[node] > limits["maxExpansionsPerNode"]:
            continue
        visited = {entry, *(s["to"] for s in steps)}
        for target, kind, via, line, confidence in graph.neighbours(node, kinds):
            if target in visited:
                continue  # simple paths only: cycles never loop
            step = {"from": node, "to": target, "kind": kind, "via": via, "line": line, "confidence": confidence}
            queue.append((target, (*steps, step)))
    found.sort(key=lambda p: (p["length"], -CONFIDENCE[p["confidence"]], p["target"], [s["to"] for s in p["steps"]]))
    return {"entry": entry, "approximate": True, "note": NOTE, "paths": found, "truncated": truncated, "limits": limits}


def as_text(result, files):
    """Plain-text rendering for the "copy path" button and reports."""
    lines = [f"Entry: {result['entry']} ({NOTE})"]
    for index, path in enumerate(result["paths"], 1):
        lines.append(f"\nPath {index} → {path['target']} (confidence {path['confidence']}, {path['length']} steps)")
        for step in path["steps"]:
            where = f"{files.get(step['from'], '?')}:{step['line']}" if step["line"] else files.get(step["from"], "?")
            lines.append(f"  {step['from']} --{step['kind']} ({step['via']})--> {step['to']}   [{where}]")
    return "\n".join(lines)
