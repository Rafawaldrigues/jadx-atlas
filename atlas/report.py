"""Exportable reports (phase 7): unified JSON and a Markdown write-up skeleton.

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
- **Índice:** declarações Java extraídas com Tree-sitter da exportação do JADX; herança resolvida por escopo léxico, imports, pacote e curingas. Nomes ambíguos ou desconhecidos ficam marcados como tal, nunca unidos por nome curto.
- **Superfície de ataque:** AndroidManifest decodificado (lido como entrada hostil). A exportação efetiva segue as regras do AOSP, e valores que dependem de recursos aparecem como desconhecidos, com um palpite explicado.
- **Papéis:** cadeia de ancestrais (projeto + tabela de framework gerada por `javap`), com o caminho e a menor confiança encontrada nele.
- **Candidatos a achado:** regras de dados sobre chamadas, criações, overrides e strings. Confiança `high` quando o tipo declarado do receptor resolve para a API, `medium` quando só método e import batem, `low` quando só o nome do método bate.
- **Intents e caminhos:** fluxo apenas dentro do método; caminhos são rotas *possíveis* no grafo de Intents, referências de tipo e herança.

**Limitações:** não há análise de fluxo de dados nem interprocedural; não há execução do app; código nativo, Kotlin original, Smali e recursos além do Manifest não são analisados; reflexão e carregamento dinâmico escondem fluxos reais. Todo item deste relatório é um **candidato** que precisa de confirmação manual."""


def sha256_file(path, limit=2 * 1024 * 1024 * 1024):
    """Hash only (the APK content is never parsed). Refuses symlinks and files over 2 GiB."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"--apk: {path} não é um arquivo regular")
    if path.stat().st_size > limit:
        raise ValueError("--apk: arquivo maior que 2 GiB")
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
            "secrets": "incluídos (--include-secrets)" if include_secrets else "mascarados",
            "anonymized": bool(anonymize),
            "note": "Candidatos para revisão manual; não são veredictos.",
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
        report["name"] = "projeto anonimizado"
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
        f"> {meta['note']} Gerado por {meta['tool']} em {meta['generated']} (schemaVersion {report['schemaVersion']}).",
        "",
    ]
    lines += ["## Rastreabilidade", "", "| Item | Valor |", "|---|---|"]
    lines.append(f"| SHA-256 do AndroidManifest analisado | `{meta['manifestSha256'] or '—'}` |")
    lines.append(f"| SHA-256 do APK | `{meta['apkSha256'] or 'não informado (--apk)'}` |")
    lines.append(f"| Segredos | {meta['secrets']} |")
    lines.append(f"| Anonimizado | {'sim' if meta['anonymized'] else 'não'} |")
    lines += ["", "## Aplicativo", ""]
    if manifest:
        lines += ["| Campo | Valor |", "|---|---|"]
        for key, label in (
            ("package", "Pacote"),
            ("versionName", "Versão"),
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
        lines.append(f"| Atributos de `<application>` | {', '.join(flags) or '—'} |")
        lines.append(f"| Permissões usadas | {', '.join(f'`{p}`' for p in manifest['usesPermissions']) or '—'} |")
    else:
        lines.append(
            "AndroidManifest não analisado (exportação sem recursos). A superfície de ataque não está neste relatório."
        )
    lines += [
        "",
        f"Índice: {stats['types']} tipos, {stats['relations']} relações de herança, {stats['files']} arquivos.",
        "",
    ]
    if manifest:
        lines += [
            "## Superfície de ataque",
            "",
            "| Componente | Tipo | Exportado | Motivo | Permissão | Deep links |",
            "|---|---|---|---|---|---|",
        ]
        for c in manifest["components"]:
            exported = str(c["exported"]) + (f" (palpite: {c.get('exportedGuess')})" if "exportedGuess" in c else "")
            permission = f"`{c['permission']}` ({c['protectionLevel']})" if c["permission"] else "nenhuma"
            links = ", ".join(f"`{d['uri']}`" for d in c["deepLinks"]) or "—"
            lines.append(
                f"| `{c['name']}` | {c['type']} | {exported} | {_cell(c['exportedReason'])} | {permission} | {links} |"
            )
        lines.append("")
    rules = {r["id"]: r for r in report.get("rules", [])}
    lines += ["## Candidatos a achado", ""]
    if not report["findings"]:
        lines += ["Nenhum candidato encontrado pelas regras atuais.", ""]
    for severity in SEVERITY_ORDER:
        items = [f for f in report["findings"] if f["severity"] == severity]
        if not items:
            continue
        lines += [f"### Severidade {severity} ({len(items)})", ""]
        for f in items[:200]:
            rule = rules.get(f["ruleId"], {})
            lines.append(
                f"- **{rule.get('title', f['ruleId'])}** (`{f['ruleId']}`, confiança {f['confidence']}): `{f['classId']}` em `{f['file']}:{f['line']}`"
            )
            lines.append(f"  - Trecho: `{_cell(f['snippet'])}`")
            if f.get("secretValue"):
                lines.append(f"  - Valor: `{f['secretValue']}`")
            elif f.get("secret"):
                lines.append(f"  - Valor mascarado: `{f['secret']}`")
        if len(items) > 200:
            lines.append(f"- … mais {len(items) - 200} no JSON.")
        lines.append("")
    if report["paths"]:
        lines += [
            "## Caminhos possíveis",
            "",
            "Rotas no grafo de Intents, referências de tipo e herança; não provam alcançabilidade nem exploração.",
            "",
        ]
        for path in report["paths"]:
            lines.append(
                f"- `{path['entry']}` ({', '.join(path['reasons'])}) → `{path['target']}` (confiança {path['confidence']}):"
            )
            for step in path["steps"]:
                lines.append(
                    f"  - `{step['from']}` --{step['kind']} ({step['via']})--> `{step['to']}` [`{step['file']}:{step['line']}`]"
                )
        lines.append("")
    lines += ["## Metodologia e limitações", "", METHODOLOGY, ""]
    lines += [
        "## Para o analista",
        "",
        "### Reprodução",
        "",
        "<!-- PREENCHER: passos para reproduzir, ambiente, versão testada -->",
        "",
        "### Impacto",
        "",
        "<!-- PREENCHER: o que um atacante consegue, pré-condições, severidade avaliada -->",
        "",
        "### Correção sugerida",
        "",
        "<!-- PREENCHER -->",
        "",
    ]
    return "\n".join(lines)


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("`", "'")[:160]


def to_json(report):
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def add_arguments(parser):
    parser.add_argument("path", help="Exportação do JADX (pasta sources ou a pasta de saída)")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--out", type=Path, help="Arquivo de saída (padrão: saída padrão)")
    parser.add_argument("--manifest", help="AndroidManifest.xml decodificado (padrão: procurar perto da pasta)")
    parser.add_argument("--apk", help="Registrar o SHA-256 deste APK (só o hash; o conteúdo não é analisado)")
    parser.add_argument(
        "--anonymize", action="store_true", help="Trocar pacote, classes e hosts por identificadores estáveis"
    )
    parser.add_argument(
        "--include-secrets", action="store_true", help="Incluir o valor completo dos segredos (cuidado ao compartilhar)"
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
