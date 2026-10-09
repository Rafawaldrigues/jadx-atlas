"""Read a decoded AndroidManifest.xml (JADX or apktool output) as hostile input.

Every derived fact carries its reason. Platform rules follow AOSP
`com.android.internal.pm.pkg.component` (ParsedActivityUtils, ParsedServiceUtils,
ParsedProviderUtils, ParsedMainComponentUtils); see docs/DECISIONS.md D-009.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from itertools import product
from pathlib import Path
import xml.sax
from xml.sax.handler import ContentHandler, feature_namespaces

from defusedxml import DefusedXmlException
import defusedxml.sax

ANDROID = "http://schemas.android.com/apk/res/android"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 64
MAX_ELEMENTS = 50_000
MAX_FILTERS = 200
MAX_DEEP_LINKS = 50
COMPONENT_TYPES = ("activity", "activity-alias", "service", "receiver", "provider")
VIEW = "android.intent.action.VIEW"
BROWSABLE = "android.intent.category.BROWSABLE"
AXML_MAGIC = b"\x03\x00\x08\x00"
S, JELLY_BEAN_MR1 = 31, 17


class ManifestError(ValueError):
    pass


class _Element:
    __slots__ = ("tag", "attrs", "children", "line", "text")

    def __init__(self, tag, attrs, line):
        self.tag, self.attrs, self.children, self.line, self.text = tag, attrs, [], line, ""

    def get(self, name):
        """android:<name>, falling back to the unprefixed attribute (e.g. `package`)."""
        return self.attrs.get((ANDROID, name), self.attrs.get((None, name)))

    def find(self, tag):
        return [child for child in self.children if child.tag == tag]


class _TreeBuilder(ContentHandler):
    def __init__(self):
        super().__init__()
        self.stack, self.root, self.count, self.locator = [], None, 0, None

    def setDocumentLocator(self, locator):
        self.locator = locator

    def startElementNS(self, name, qname, attrs):
        self.count += 1
        if self.count > MAX_ELEMENTS:
            raise ManifestError(f"XML com mais de {MAX_ELEMENTS} elementos; ignorado")
        if len(self.stack) >= MAX_DEPTH:
            raise ManifestError(f"XML com mais de {MAX_DEPTH} níveis; ignorado")
        line = self.locator.getLineNumber() if self.locator else None
        element = _Element(name[1], {key: value for key, value in attrs.items()}, line)
        if self.stack:
            self.stack[-1].children.append(element)
        else:
            self.root = element
        self.stack.append(element)

    def endElementNS(self, name, qname):
        self.stack.pop()

    def characters(self, content):
        # Only short scalar values (e.g. <bool>) are needed; cap memory for hostile text nodes.
        if self.stack and len(self.stack[-1].text) < 64:
            self.stack[-1].text += content[:64]


def _parse_xml(data: bytes, limit: int, label: str):
    if len(data) > limit:
        raise ManifestError(f"{label} maior que {limit // (1024 * 1024)} MiB; ignorado")
    if data.startswith(AXML_MAGIC):
        raise ManifestError(f"{label} está em formato binário (AXML); exporte com o JADX sem --no-res")
    handler = _TreeBuilder()
    parser = defusedxml.sax.make_parser()
    parser.forbid_dtd, parser.forbid_entities, parser.forbid_external = True, True, True
    parser.setFeature(feature_namespaces, True)
    parser.setContentHandler(handler)
    try:
        source = xml.sax.xmlreader.InputSource()
        source.setByteStream(BytesIO(data))
        parser.parse(source)
    except DefusedXmlException as error:
        raise ManifestError(f"{label} recusado: DTD ou entidades não são permitidos ({type(error).__name__})") from None
    except xml.sax.SAXException as error:
        cause = error.getException() if hasattr(error, "getException") else None
        if isinstance(cause, ManifestError):
            raise cause from None
        raise ManifestError(f"{label} inválido: {error}") from None
    if handler.root is None:
        raise ManifestError(f"{label} vazio")
    return handler.root


def _read_regular_file(path: Path, limit: int, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"{label} não é um arquivo regular (links simbólicos são ignorados)")
    if path.stat().st_size > limit:
        raise ManifestError(f"{label} maior que {limit // (1024 * 1024)} MiB; ignorado")
    return path.read_bytes()


def locate(root, explicit=None):
    """Return the manifest path to use, or None. An explicit path must exist."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"Manifest informado não encontrado ou não é um arquivo regular: {path}")
        return path.resolve()
    root = Path(root)
    for candidate in (
        root / "resources" / "AndroidManifest.xml",
        root.parent / "resources" / "AndroidManifest.xml",
        root / "AndroidManifest.xml",
    ):
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    return None


def bool_resources(res_dir: Path, warnings: list):
    """{name: {qualifier: "true"/"false"}} from res/values*/bools.xml (JADX and apktool layout)."""
    values = {}
    if not res_dir.is_dir() or res_dir.is_symlink():
        return values
    for folder in sorted(res_dir.glob("values*")):
        file = folder / "bools.xml"
        if folder.is_symlink() or not file.exists():
            continue
        qualifier = folder.name[len("values") :].lstrip("-") or "default"
        try:
            root = _parse_xml(
                _read_regular_file(file, MAX_RESOURCE_BYTES, file.name), MAX_RESOURCE_BYTES, f"{folder.name}/bools.xml"
            )
        except (ManifestError, OSError) as error:
            warnings.append(str(error))
            continue
        for item in root.find("bool"):
            name = item.attrs.get((None, "name"))
            if name:
                values.setdefault(name, {})[qualifier] = item.text.strip()
    return values


def _sdk(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def qualify(name, package):
    """Manifest class name → qualified name (`.Foo` and `Foo` are relative to the package)."""
    if not name:
        return None
    if name.startswith("."):
        return f"{package}{name}" if package else name[1:]
    if "." not in name:
        return f"{package}.{name}" if package else name
    return name


def class_id(qualified):
    """JADX node ids use dots for nested classes; the manifest may use `$`."""
    return qualified.replace("$", ".") if qualified else None


def _tristate(raw, default, resources, attribute):
    """(value, explicit, reason, guess, guessReason) for a boolean manifest attribute."""
    if raw is None:
        return default, False, None, None, None
    if raw in {"true", "false"}:
        return raw == "true", True, "explícito", None, None
    if raw.startswith("@") and "/" in raw:
        kind, name = raw[1:].split("/", 1)
        kind = kind.split(":")[-1]
        reason = f"depende do recurso {raw}"
        options = resources.get(name, {}) if kind == "bool" else {}
        if not options:
            return "unknown", True, reason, None, f"recurso {raw} não encontrado em res/values*/bools.xml"
        default_value = options.get("default")
        variants = sorted({v for v in options.values() if v in {"true", "false"}})
        if default_value in {"true", "false"} and len(variants) == 1:
            return "unknown", True, reason, default_value == "true", f"{raw} = {default_value} em res/values/bools.xml"
        detail = ", ".join(f"{q}={v}" for q, v in sorted(options.items()))
        # Several configurations disagree: the conservative (more exposed) reading is the potential one.
        guess = "true" in variants if attribute == "exported" else ("false" not in variants)
        return "unknown", True, reason, guess, f"{raw} varia por configuração ({detail})"
    return "unknown", True, f"valor inesperado {raw!r}", None, None


def effective_exported(component, raw, filters_counted, target_sdk, min_sdk, resources):
    """Return the exported decision with its reason, guess and confidence."""
    kind = component["type"]
    value, explicit, reason, guess, guess_reason = _tristate(raw, None, resources, "exported")
    confidence = "high"
    if explicit and value != "unknown":
        return {"exported": value, "exportedExplicit": True, "exportedReason": "explícito", "confidence": confidence}
    sdk = target_sdk if target_sdk is not None else (min_sdk if min_sdk is not None else 1)
    if target_sdk is None:
        confidence = "medium"
    if kind == "provider":
        implicit = sdk < JELLY_BEAN_MR1
        implicit_reason = f"padrão de provider com targetSdk {sdk} {'<' if implicit else '≥'} 17"
    elif filters_counted:
        implicit = True
        implicit_reason = f"implícito por intent-filter (targetSdk {sdk} < 31)"
    else:
        implicit = False
        implicit_reason = "sem intent-filter e sem exported"
    if explicit:  # resource reference or unexpected literal
        if guess is None:
            guess, guess_reason = (
                implicit,
                f"{guess_reason or 'valor não resolvido'}; palpite pela regra implícita ({implicit_reason})",
            )
        return {
            "exported": "unknown",
            "exportedExplicit": True,
            "exportedReason": reason,
            "exportedGuess": guess,
            "exportedGuessReason": guess_reason,
            "confidence": "low",
        }
    if kind != "provider" and filters_counted and sdk >= S:
        result = {
            "exported": "inconsistent",
            "exportedExplicit": False,
            "confidence": "low",
            "exportedReason": f"intent-filter sem android:exported com targetSdk {sdk} ≥ 31: o Android 12+ recusa a instalação",
        }
        if min_sdk is not None and min_sdk < S:
            result.update(
                exportedGuess=True,
                exportedGuessReason=f"em dispositivos com API < 31 (minSdk {min_sdk}) seria exportado implicitamente",
            )
        else:
            result.update(exportedGuess=None, exportedGuessReason="o app não instala em nenhuma versão suportada")
        return result
    return {
        "exported": implicit,
        "exportedExplicit": False,
        "exportedReason": implicit_reason,
        "confidence": confidence,
    }


def _intent_filter(element):
    data = []
    for item in element.find("data"):
        entry = {
            key: item.get(key)
            for key in (
                "scheme",
                "host",
                "port",
                "path",
                "pathPrefix",
                "pathPattern",
                "pathSuffix",
                "pathAdvancedPattern",
                "mimeType",
            )
        }
        data.append({k: v for k, v in entry.items() if v is not None})
    return {
        "actions": [a.get("name") for a in element.find("action") if a.get("name")],
        "categories": [c.get("name") for c in element.find("category") if c.get("name")],
        "data": data,
        "autoVerify": element.get("autoVerify") == "true",
        "line": element.line,
    }


def deep_links(filters):
    """VIEW + BROWSABLE + scheme. <data> elements of one filter combine (scheme × host × path)."""
    links = []
    for item in filters:
        if VIEW not in item["actions"] or BROWSABLE not in item["categories"]:
            continue
        schemes = sorted({d["scheme"] for d in item["data"] if "scheme" in d})
        if not schemes:
            continue
        hosts = sorted({d["host"] + (f":{d['port']}" if "port" in d else "") for d in item["data"] if "host" in d}) or [
            None
        ]
        paths = [
            (k, d[k])
            for d in item["data"]
            for k in ("path", "pathPrefix", "pathPattern", "pathSuffix", "pathAdvancedPattern")
            if k in d
        ] or [(None, None)]
        for scheme, host, (path_kind, path) in product(schemes, hosts, paths):
            if len(links) >= MAX_DEEP_LINKS:
                return links, True
            links.append(
                {
                    "scheme": scheme,
                    "host": host,
                    "pathKind": path_kind,
                    "path": path,
                    "autoVerify": item["autoVerify"],
                    "uri": f"{scheme}://{host or '*'}{path or ''}" if host or path else f"{scheme}:",
                }
            )
    return links, False


def parse_manifest(data: bytes, resources=None, label="AndroidManifest.xml"):
    """Parse manifest bytes. `resources` is the result of bool_resources()."""
    resources = resources or {}
    root = _parse_xml(data, MAX_MANIFEST_BYTES, label)
    if root.tag != "manifest":
        raise ManifestError(f"{label}: elemento raiz <{root.tag}> não é <manifest>")
    warnings = []
    package = root.get("package") or ""
    uses_sdk = (root.find("uses-sdk") or [None])[0]
    min_sdk = _sdk(uses_sdk.get("minSdkVersion")) if uses_sdk else None
    target_sdk = _sdk(uses_sdk.get("targetSdkVersion")) if uses_sdk else None
    if target_sdk is None and min_sdk is not None:
        warnings.append(f"targetSdkVersion ausente; a plataforma usa minSdkVersion ({min_sdk})")
    elif target_sdk is None:
        warnings.append("uses-sdk ausente; regras de exportação aplicadas com confiança média")
    defined = [
        {"name": p.get("name"), "protectionLevel": p.get("protectionLevel") or "normal", "line": p.line}
        for p in root.find("permission")
        if p.get("name")
    ]
    levels = {p["name"]: p["protectionLevel"] for p in defined}
    applications = root.find("application")
    app = applications[0] if applications else _Element("application", {}, None)
    if len(applications) > 1:
        warnings.append("Mais de um <application>; apenas o primeiro foi analisado")
    app_permission = app.get("permission")
    app_enabled, *_ = _tristate(app.get("enabled"), True, resources, "enabled")

    def flag(name):
        raw = app.get(name)
        value, _, reason, guess, guess_reason = _tristate(raw, None, resources, name)
        result = {"value": value, "raw": raw}
        if value == "unknown":
            result.update(reason=reason, guess=guess, guessReason=guess_reason)
        return result

    application = {
        "name": class_id(qualify(app.get("name"), package)),
        "debuggable": flag("debuggable"),
        "allowBackup": flag("allowBackup"),
        "usesCleartextTraffic": flag("usesCleartextTraffic"),
        "testOnly": flag("testOnly"),
        "networkSecurityConfig": app.get("networkSecurityConfig"),
        "fullBackupContent": app.get("fullBackupContent"),
        "dataExtractionRules": app.get("dataExtractionRules"),
        "permission": app_permission,
        "enabled": app_enabled,
    }

    def protection(permission):
        if not permission:
            return None
        return levels.get(permission, "unknown")

    components = []
    for element in app.children:
        if element.tag not in COMPONENT_TYPES:
            continue
        name = qualify(element.get("name"), package)
        if not name:
            warnings.append(f"<{element.tag}> sem android:name na linha {element.line}; ignorado")
            continue
        filters_xml = element.find("intent-filter")
        if len(filters_xml) > MAX_FILTERS:
            warnings.append(f"{name}: {len(filters_xml)} intent-filters; apenas {MAX_FILTERS} analisados")
            filters_xml = filters_xml[:MAX_FILTERS]
        filters = [_intent_filter(f) for f in filters_xml]
        # Activities and receivers drop filters without <action>; services keep them (failOnNoActions=false).
        counted = (
            [f for f in filters if f["actions"]]
            if element.tag in {"activity", "activity-alias", "receiver"}
            else filters
        )
        component = {"type": element.tag, "name": name, "line": element.line}
        target = qualify(element.get("targetActivity"), package) if element.tag == "activity-alias" else None
        component["targetActivity"] = class_id(target)
        component["classId"] = class_id(target or name)
        component["rawName"] = target or name  # with `$` kept, for R8 top-level `Outer$Inner` classes
        enabled, _, enabled_reason, enabled_guess, enabled_guess_reason = _tristate(
            element.get("enabled"), True, resources, "enabled"
        )
        if app_enabled is False:
            enabled, enabled_reason = False, '<application android:enabled="false">'
        component["enabled"] = enabled
        if enabled == "unknown":
            component.update(
                enabledReason=enabled_reason, enabledGuess=enabled_guess, enabledGuessReason=enabled_guess_reason
            )
        component.update(
            effective_exported(component, element.get("exported"), bool(counted), target_sdk, min_sdk, resources)
        )
        own = element.get("permission")
        if element.tag == "provider":
            read = element.get("readPermission") or own or app_permission
            write = element.get("writePermission") or own or app_permission
            component["provider"] = {
                "authorities": [a for a in (element.get("authorities") or "").split(";") if a],
                "grantUriPermissions": element.get("grantUriPermissions") == "true",
                "readPermission": read,
                "readProtectionLevel": protection(read),
                "writePermission": write,
                "writeProtectionLevel": protection(write),
                "pathPermissions": [
                    {
                        k: p.get(k)
                        for k in (
                            "path",
                            "pathPrefix",
                            "pathPattern",
                            "permission",
                            "readPermission",
                            "writePermission",
                        )
                        if p.get(k)
                    }
                    for p in element.find("path-permission")
                ],
            }
            # Weakest side decides exposure: a provider readable without permission is exposed.
            permission = None if not read or not write else (read if read == write else f"{read} / {write}")
            declared = own or element.get("readPermission") or element.get("writePermission")
            source = None if not permission else ("component" if declared else "application")
            level = None if not read or not write else min((protection(read), protection(write)), key=_level_rank)
        else:
            # An activity-alias does not inherit <application android:permission> (AOSP ParsedActivityUtils).
            inherited = None if element.tag == "activity-alias" else app_permission
            permission = own or inherited
            source = "component" if own else ("application" if inherited else None)
            level = protection(permission)
        component.update(
            permission=permission, permissionSource=source, protectionLevel=level, process=element.get("process")
        )
        component["intentFilters"] = filters
        component["deepLinks"], truncated = (
            deep_links(filters) if element.tag in {"activity", "activity-alias"} else ([], False)
        )
        if truncated:
            warnings.append(f"{name}: mais de {MAX_DEEP_LINKS} combinações de deep link; lista truncada")
        component["exposure"] = exposure_rank(component)
        components.append(component)
    components.sort(key=lambda c: (c["exposure"], c["type"], c["name"]))
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "package": package,
        "versionName": root.get("versionName"),
        "versionCode": root.get("versionCode"),
        "minSdk": min_sdk,
        "targetSdk": target_sdk,
        "targetSdkSource": "uses-sdk" if target_sdk is not None else "missing",
        "usesPermissions": sorted({p.get("name") for p in root.find("uses-permission") if p.get("name")}),
        "definedPermissions": defined,
        "application": application,
        "components": components,
        "warnings": warnings,
    }


_LEVEL_ORDER = {None: 0, "unknown": 1, "normal": 2, "dangerous": 3}


def _level_rank(level):
    """Lower is weaker. Anything containing signature/privileged/knownSigner ranks strongest."""
    if level and any(word in level for word in ("signature", "privileged", "knownSigner")):
        return 9
    return _LEVEL_ORDER.get(level, 1)


def exposure_rank(component):
    """0 = most exposed. Disabled components and non-exported ones come last."""
    exported = component["exported"]
    if exported in {"unknown", "inconsistent"}:
        exported_now = component.get("exportedGuess")
    else:
        exported_now = exported
    disabled = component["enabled"] is False
    if not exported_now:
        rank = 6 if exported in {"unknown", "inconsistent"} else 7
    elif not component.get("permission"):
        rank = 0
    elif _level_rank(component.get("protectionLevel")) < 9:
        rank = 1
    else:
        rank = 2
    if exported in {"unknown", "inconsistent"} and exported_now:
        rank += 3  # potentially exposed: after the confirmed ones of the same tier
    return rank + (10 if disabled else 0)


def load(path: Path, base: Path | None = None):
    """Load a manifest file plus res/values*/bools.xml next to it."""
    data = _read_regular_file(path, MAX_MANIFEST_BYTES, "AndroidManifest.xml")
    resource_warnings = []
    resources = bool_resources(path.parent / "res", resource_warnings)
    result = parse_manifest(data, resources)
    result["warnings"] = resource_warnings + result["warnings"]
    try:
        result["path"] = path.relative_to(base).as_posix() if base else str(path)
    except ValueError:
        result["path"] = str(path)
    return result
