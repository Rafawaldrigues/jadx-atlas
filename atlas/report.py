"""Exportable reports: unified JSON and a Markdown write-up skeleton.

Redaction by default: secrets stay masked, snippets are capped at 160
characters and the absolute import path is never exported. `--anonymize`
replaces the app's package and class names (and URL hosts) with stable
identifiers so a report can be shared without exposing the target.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from . import __version__

SEVERITY_ORDER = ["high", "medium", "low", "info"]
METHODOLOGY = """\
- **Index:** Java declarations extracted with Tree-sitter from the JADX export; inheritance resolved through lexical scope, imports, package and wildcards. Ambiguous or unknown names stay marked as such and are never joined by short name.
- **Attack surface:** decoded AndroidManifest (read as hostile input). Effective export follows the AOSP rules; values that depend on resources are reported as unknown, with an explained guess.
- **Roles:** ancestor chain (project + framework table generated with `javap`), with the path and the weakest confidence along it.
- **Finding candidates:** data-driven rules over calls, object creations, overrides and strings. Confidence is `high` when the receiver's declared type resolves to the API, `medium` when only the method and the import match, `low` when only the method name matches.
- **Intents and paths:** flow within a single method only; paths are *possible* routes in the graph of Intents, type references and inheritance.

**Limitations:** no data-flow or interprocedural analysis; the app is never executed; native code, original Kotlin, Smali and resources other than the manifest are not analysed; reflection and dynamic code loading hide real flows. Every item in this report is a **candidate** that needs manual confirmation."""


def sha256_file(path, limit=2 * 1024 * 1024 * 1024):
    """Hash only (the APK content is never parsed). Refuses symlinks and files over 2 GiB."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"--apk: {path} is not a regular file")
    if path.stat().st_size > limit:
        raise ValueError("--apk: file larger than 2 GiB")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(project, generated=None, apk=None, include_secrets=False, anonymize=False, max_entries=10):
    """Report dictionary: payload subset + traceability + paths. Never includes the absolute root."""
    payload = copy.deepcopy(project.payload)
    payload.pop("root", None)
    manifest = payload.get("manifest")
    report = {
        "schemaVersion": payload["schemaVersion"],
        "report": {
            "tool": f"jadx-atlas {__version__}",
            "generated": generated or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "manifestSha256": manifest["sha256"] if manifest else None,
            "apkSha256": sha256_file(apk) if apk else None,
            "secrets": "included (--include-secrets)" if include_secrets else "masked",
            "anonymized": bool(anonymize),
            "note": "Candidates for manual review, not verdicts.",
        },
        **payload,
    }
    if include_secrets:
        for index, value in project.secret_values.items():
            report["findings"][index]["secretValue"] = value
    report["paths"] = []
    for entry in payload.get("pathEntries", [])[:max_entries]:
        result = project.paths(entry["id"])
        for path in result["paths"][:3]:
            report["paths"].append({"entry": entry["id"], "reasons": entry["reasons"], **path})
    if anonymize:
        rules = report.pop("rules", [])
        report = Anonymizer(project).apply(report)
        report["rules"] = rules  # rule texts are ours, not the target's
        report["name"] = "anonymized project"
    return report


class Anonymizer:
    """Stable, deterministic replacements for the app's own names (sorted order → numbered ids)."""

    def __init__(self, project):
        own = sorted(
            node_id for node_id, node in project.nodes.items() if not node["external"] and not node_id.startswith("?")
        )
        packages = sorted({project.nodes[i]["package"] for i in own if project.nodes[i]["package"]})
        manifest = project.payload.get("manifest") or {}
        if manifest.get("package"):
            packages = sorted(set(packages) | {manifest["package"]})
        self.replacements = {}
        for index, package in enumerate(packages, 1):
            self.replacements[package] = f"pkg{index:02d}"
        for index, class_id in enumerate(own, 1):
            node = project.nodes[class_id]
            alias = f"Class{index:04d}"
            package = self.replacements.get(node["package"], "")
            self.replacements[class_id] = f"{package}.{alias}" if package else alias
            if node.get("path"):
                self.replacements.setdefault(node["path"], f"{alias}.java")  # outer class sorts first
            if len(node["name"]) >= 4:
                self.replacements.setdefault(node["name"], alias)
        # Longest first so `app.ui.Main` is replaced before `app.ui` and `Main`.
        keys = sorted(self.replacements, key=len, reverse=True)
        self.pattern = re.compile("|".join(rf"(?<![\w$]){re.escape(k)}(?![\w$])" for k in keys)) if keys else None
        self.hosts = {}

    def text(self, value):
        if self.pattern is not None:
            value = self.pattern.sub(lambda m: self.replacements[m.group()], value)

        def host(match):
            name = match.group(2)
            self.hosts.setdefault(name, f"host{len(self.hosts) + 1}.invalid")
            return match.group(1) + self.hosts[name]

        return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:'\"`?#]+)", host, value)

    def apply(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.apply(v) for v in value]
        if isinstance(value, dict):
            return {self.apply(k) if isinstance(k, str) else k: self.apply(v) for k, v in value.items()}
        return value


def render_markdown(report):
    """Write-up skeleton: facts first, analyst sections marked for completion."""
    meta, stats, manifest = report["report"], report["stats"], report.get("manifest")
    lines = [f"# JADX Atlas: {report['name']}", ""]
    lines += [
        f"> {meta['note']} Generated by {meta['tool']} on {meta['generated']} (schemaVersion {report['schemaVersion']}).",
        "",
    ]
    lines += ["## Traceability", "", "| Item | Value |", "|---|---|"]
    lines.append(f"| SHA-256 of the analysed AndroidManifest | `{meta['manifestSha256'] or '—'}` |")
    lines.append(f"| SHA-256 of the APK | `{meta['apkSha256'] or 'not provided (--apk)'}` |")
    lines.append(f"| Secrets | {meta['secrets']} |")
    lines.append(f"| Anonymized | {'yes' if meta['anonymized'] else 'no'} |")
    lines += ["", "## App", ""]
    if manifest:
        lines += ["| Field | Value |", "|---|---|"]
        for key, label in (
            ("package", "Package"),
            ("versionName", "Version"),
            ("versionCode", "versionCode"),
            ("minSdk", "minSdk"),
            ("targetSdk", "targetSdk"),
        ):
            lines.append(f"| {label} | `{manifest.get(key)}` |")
        flags = [
            f"`{k}`={v['value']}"
            for k, v in manifest["application"].items()
            if isinstance(v, dict) and v.get("value") not in (None, False)
        ]
        lines.append(f"| `<application>` attributes | {', '.join(flags) or '—'} |")
        lines.append(f"| Used permissions | {', '.join(f'`{p}`' for p in manifest['usesPermissions']) or '—'} |")
    else:
        lines.append(
            "AndroidManifest not analysed (export without resources). The attack surface is not part of this report."
        )
    lines += [
        "",
        f"Index: {stats['types']} types, {stats['relations']} inheritance relations, {stats['files']} files.",
        "",
    ]
    if manifest:
        lines += [
            "## Attack surface",
            "",
            "| Component | Type | Exported | Reason | Permission | Deep links |",
            "|---|---|---|---|---|---|",
        ]
        for c in manifest["components"]:
            exported = str(c["exported"]) + (f" (guess: {c.get('exportedGuess')})" if "exportedGuess" in c else "")
            permission = f"`{c['permission']}` ({c['protectionLevel']})" if c["permission"] else "none"
            links = ", ".join(f"`{d['uri']}`" for d in c["deepLinks"]) or "—"
            lines.append(
                f"| `{c['name']}` | {c['type']} | {exported} | {_cell(c['exportedReason'])} | {permission} | {links} |"
            )
        lines.append("")
    rules = {r["id"]: r for r in report.get("rules", [])}
    lines += ["## Finding candidates", ""]
    if not report["findings"]:
        lines += ["No candidate found by the current rules.", ""]
    for severity in SEVERITY_ORDER:
        items = [f for f in report["findings"] if f["severity"] == severity]
        if not items:
            continue
        lines += [f"### Severity {severity} ({len(items)})", ""]
        for f in items[:200]:
            rule = rules.get(f["ruleId"], {})
            lines.append(
                f"- **{rule.get('title', f['ruleId'])}** (`{f['ruleId']}`, confidence {f['confidence']}): `{f['classId']}` in `{f['file']}:{f['line']}`"
            )
            lines.append(f"  - Snippet: `{_cell(f['snippet'])}`")
            if f.get("secretValue"):
                lines.append(f"  - Value: `{f['secretValue']}`")
            elif f.get("secret"):
                lines.append(f"  - Masked value: `{f['secret']}`")
        if len(items) > 200:
            lines.append(f"- … {len(items) - 200} more in the JSON.")
        lines.append("")
    if report["paths"]:
        lines += [
            "## Possible paths",
            "",
            "Routes in the graph of Intents, type references and inheritance; they do not prove reachability or exploitability.",
            "",
        ]
        for path in report["paths"]:
            lines.append(
                f"- `{path['entry']}` ({', '.join(path['reasons'])}) → `{path['target']}` (confidence {path['confidence']}):"
            )
            for step in path["steps"]:
                lines.append(
                    f"  - `{step['from']}` --{step['kind']} ({step['via']})--> `{step['to']}` [`{step['file']}:{step['line']}`]"
                )
        lines.append("")
    lines += ["## Methodology and limitations", "", METHODOLOGY, ""]
    lines += [
        "## For the analyst",
        "",
        "### Reproduction",
        "",
        "<!-- TODO: steps to reproduce, environment, tested version -->",
        "",
        "### Impact",
        "",
        "<!-- TODO: what an attacker can do, preconditions, assessed severity -->",
        "",
        "### Suggested fix",
        "",
        "<!-- TODO -->",
        "",
    ]
    return "\n".join(lines)


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("`", "'")[:160]


def to_json(report):
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def add_arguments(parser):
    parser.add_argument("path", help="JADX export (the sources folder or the output folder)")
    parser.add_argument(
        "--format", choices=("md", "json"), default="md", help="md: report, json: full index (default: md)"
    )
    parser.add_argument("--out", type=Path, help="Output file (default: standard output)")
    parser.add_argument("--manifest", help="Decoded AndroidManifest.xml (default: look next to the folder)")
    parser.add_argument("--apk", help="Record the SHA-256 of this APK (hash only; its content is not analysed)")
    parser.add_argument(
        "--anonymize", action="store_true", help="Replace package, class names and hosts with stable identifiers"
    )
    parser.add_argument(
        "--include-secrets", action="store_true", help="Include full secret values (careful when sharing)"
    )


def run(args, parser):
    from .indexer import Project

    try:
        project = Project(args.path, manifest=args.manifest)
        report = build(project, apk=args.apk, include_secrets=args.include_secrets, anonymize=args.anonymize)
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    text = to_json(report) if args.format == "json" else render_markdown(report)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0
