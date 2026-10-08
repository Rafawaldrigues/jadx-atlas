#!/usr/bin/env python3
"""Generate or check atlas/data/framework_hierarchy.json from real class files.

Direct parents come from `javap` run on the Android SDK `android.jar` and on
official AAR/JAR artifacts from Google Maven (downloaded once into
artifacts/maven/, which Git ignores). Nothing is executed from those files.
Development tool only: JADX Atlas itself never touches the network.

    python scripts/framework_hierarchy.py            # regenerate the JSON
    python scripts/framework_hierarchy.py --check    # exit 1 if the JSON differs
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "atlas" / "data" / "framework_hierarchy.json"
CACHE = ROOT / "artifacts" / "maven"
PLATFORM = "android-37.0"
MAVEN = "https://dl.google.com/android/maven2"

# source -> types read from it. "platform" is <sdk>/platforms/<PLATFORM>/android.jar.
TYPES = {
    "platform": [
        "android.content.Context",
        "android.content.ContextWrapper",
        "android.view.ContextThemeWrapper",
        "android.app.Activity",
        "android.app.ListActivity",
        "android.app.ExpandableListActivity",
        "android.app.ActivityGroup",
        "android.app.TabActivity",
        "android.app.AliasActivity",
        "android.app.LauncherActivity",
        "android.app.NativeActivity",
        "android.preference.PreferenceActivity",
        "android.app.Service",
        "android.app.IntentService",
        "android.app.job.JobService",
        "android.service.notification.NotificationListenerService",
        "android.accessibilityservice.AccessibilityService",
        "android.inputmethodservice.AbstractInputMethodService",
        "android.inputmethodservice.InputMethodService",
        "android.service.wallpaper.WallpaperService",
        "android.app.admin.DeviceAdminService",
        "android.service.quicksettings.TileService",
        "android.widget.RemoteViewsService",
        "android.service.dreams.DreamService",
        "android.net.VpnService",
        "android.nfc.cardemulation.HostApduService",
        "android.service.autofill.AutofillService",
        "android.content.BroadcastReceiver",
        "android.app.admin.DeviceAdminReceiver",
        "android.appwidget.AppWidgetProvider",
        "android.content.ContentProvider",
        "android.content.SearchRecentSuggestionsProvider",
        "android.provider.DocumentsProvider",
        "android.app.Application",
        "android.app.Fragment",
        "android.app.DialogFragment",
        "android.app.ListFragment",
        "android.preference.PreferenceFragment",
        "javax.net.ssl.TrustManager",
        "javax.net.ssl.X509TrustManager",
        "javax.net.ssl.X509ExtendedTrustManager",
        "javax.net.ssl.HostnameVerifier",
        "javax.net.SocketFactory",
        "javax.net.ssl.SSLSocketFactory",
        "android.net.SSLCertificateSocketFactory",
        "android.webkit.WebViewClient",
        "android.webkit.WebChromeClient",
        "android.os.AsyncTask",
        "android.os.Parcelable",
        "java.io.Serializable",
        "java.io.Externalizable",
    ],
    "androidx.appcompat:appcompat:1.7.0": [
        "androidx.appcompat.app.AppCompatActivity",
        "androidx.appcompat.app.AppCompatDialogFragment",
    ],
    "androidx.fragment:fragment:1.8.5": [
        "androidx.fragment.app.FragmentActivity",
        "androidx.fragment.app.Fragment",
        "androidx.fragment.app.DialogFragment",
        "androidx.fragment.app.ListFragment",
    ],
    "androidx.activity:activity:1.9.3": ["androidx.activity.ComponentActivity"],
    "androidx.core:core:1.13.1": [
        "androidx.core.app.ComponentActivity",
        "androidx.core.app.JobIntentService",
        "androidx.core.content.FileProvider",
    ],
    "androidx.legacy:legacy-support-core-utils:1.0.0": ["androidx.legacy.content.WakefulBroadcastReceiver"],
    "androidx.lifecycle:lifecycle-service:2.8.7": ["androidx.lifecycle.LifecycleService"],
    "androidx.multidex:multidex:2.0.1": ["androidx.multidex.MultiDexApplication"],
    "com.google.firebase:firebase-messaging:24.1.0": [
        "com.google.firebase.messaging.FirebaseMessagingService",
        "com.google.firebase.messaging.EnhancedIntentService",
    ],
    "com.android.support:appcompat-v7:28.0.0": [
        "android.support.v7.app.AppCompatActivity",
        "android.support.v7.app.AppCompatDialogFragment",
    ],
    "com.android.support:support-fragment:28.0.0": [
        "android.support.v4.app.FragmentActivity",
        "android.support.v4.app.Fragment",
        "android.support.v4.app.DialogFragment",
        "android.support.v4.app.ListFragment",
    ],
    "com.android.support:support-compat:28.0.0": [
        "android.support.v4.app.SupportActivity",
        "android.support.v4.app.JobIntentService",
        "android.support.v4.content.FileProvider",
    ],
    "com.android.support:support-core-utils:28.0.0": ["android.support.v4.content.WakefulBroadcastReceiver"],
}

HEADER = re.compile(
    r"^(?:public |protected |private )?(?:abstract |final |static )*(class|interface) ([\w.$]+)(?:<.*?>)?"
    r"(?: extends ([^{]+?))?(?: implements ([^{]+?))?\s*\{?$"
)


def erase(types):
    """Split a javap type list and drop generic arguments."""
    if not types:
        return []
    depth, current, result = 0, "", []
    for char in types:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(current.strip())
            current = ""
        elif depth == 0:
            current += char
    if current.strip():
        result.append(current.strip())
    return [name.replace("$", ".") for name in result]


def jar_for(source, sdk: Path):
    if source == "platform":
        return sdk / "platforms" / PLATFORM / "android.jar"
    group, artifact, version = source.split(":")
    target = CACHE / f"{artifact}-{version}.jar"
    if target.exists():
        return target
    CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{MAVEN}/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.aar"
    archive = CACHE / f"{artifact}-{version}.aar"
    print(f"baixando {url}", file=sys.stderr)
    urllib.request.urlretrieve(url, archive)  # noqa: S310 (fixed https URL, development only)
    with zipfile.ZipFile(archive) as aar, aar.open("classes.jar") as inner, target.open("wb") as out:
        shutil.copyfileobj(inner, out)
    return target


def javap(jar: Path, names, platform=False):
    # --system none: java.* and javax.* must come from android.jar, not from the local JDK.
    system = ["--system", "none"] if platform else []
    output = subprocess.run(
        ["javap", *system, "-cp", str(jar), *names], capture_output=True, text=True, check=True
    ).stdout
    found = {}
    for line in output.splitlines():
        match = HEADER.match(line.strip())
        if match:
            kind, name, extends, implements = match.groups()
            parents = erase(extends)
            if kind == "interface":  # interfaces list their super-interfaces after `extends`
                found[name.replace("$", ".")] = {"kind": "interface", "extends": [], "implements": parents}
            else:
                found[name.replace("$", ".")] = {
                    "kind": "abstract class" if " abstract " in f" {line} " else "class",
                    "extends": [p for p in parents if p != "java.lang.Object"],
                    "implements": erase(implements),
                }
    missing = set(names) - set(found)
    if missing:
        raise RuntimeError(f"javap não encontrou {sorted(missing)} em {jar.name}")
    return found


def generate(sdk: Path):
    types = {}
    for source, names in TYPES.items():
        label = f"{PLATFORM} android.jar" if source == "platform" else source
        for name, info in javap(jar_for(source, sdk), names, platform=source == "platform").items():
            info["source"] = label
            types[name] = info
    return {
        "_comment": "Generated by scripts/framework_hierarchy.py from javap output; do not edit by hand.",
        "version": 1,
        "sources": sorted({info["source"] for info in types.values()}),
        "types": dict(sorted(types.items())),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sdk", type=Path, default=Path.home() / "Android" / "Sdk")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    data = generate(args.sdk)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        same = OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text
        print("framework_hierarchy.json confere com javap" if same else "framework_hierarchy.json DIFERE de javap")
        return 0 if same else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"{len(data['types'])} tipos gravados em {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
