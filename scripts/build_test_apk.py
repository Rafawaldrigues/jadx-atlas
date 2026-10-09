#!/usr/bin/env python3
"""Build the synthetic validation APK in tests/apk/testapp with the Android SDK.

The APK is unsigned (JADX does not need a signature) and is written under
artifacts/, which Git ignores. All sources belong to this project.

    python scripts/build_test_apk.py [--sdk ~/Android/Sdk] [--out artifacts/testapp]
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "tests" / "apk" / "testapp"


def version_key(path: Path):
    return [
        int(part) if part.isdigit() else 0 for part in path.name.replace("android-", "").replace("-", ".").split(".")
    ]


def find_sdk(explicit=None):
    candidates = [
        explicit,
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        Path.home() / "Android" / "Sdk",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_dir():
            sdk = Path(candidate).expanduser()
            tools = sorted((p for p in (sdk / "build-tools").glob("*") if (p / "aapt2").exists()), key=version_key)
            platforms = sorted(
                (p for p in (sdk / "platforms").glob("android-*") if (p / "android.jar").exists()), key=version_key
            )
            if tools and platforms:
                return tools[-1], platforms[-1] / "android.jar"
    return None


def run(command):
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{command[0]} falhou:\n{result.stdout}{result.stderr}")
    return result.stdout


def build(out: Path, sdk=None, obfuscate=False):
    found = find_sdk(sdk)
    if not found:
        raise RuntimeError("Android SDK não encontrado (defina ANDROID_HOME ou use --sdk)")
    tools, android_jar = found
    if not shutil.which("javac"):
        raise RuntimeError("javac não encontrado no PATH")
    out.mkdir(parents=True, exist_ok=True)
    apk = out / ("atlas-testapp-r8.apk" if obfuscate else "atlas-testapp.apk")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        run([tools / "aapt2", "compile", "--dir", APP / "res", "-o", work / "res.zip"])
        run(
            [
                tools / "aapt2",
                "link",
                "-I",
                android_jar,
                "--manifest",
                APP / "AndroidManifest.xml",
                "-o",
                work / "base.apk",
                work / "res.zip",
            ]
        )
        sources = sorted((APP / "java").rglob("*.java"))
        run(["javac", "--release", "11", "-cp", android_jar, "-d", work / "classes", *sources])
        classes = sorted((work / "classes").rglob("*.class"))
        if obfuscate:
            # R8 ships inside d8.jar: keep manifest components, rename the rest (tests/apk/testapp/proguard-rules.pro).
            run(
                [
                    "java",
                    "-cp",
                    tools / "lib" / "d8.jar",
                    "com.android.tools.r8.R8",
                    "--release",
                    "--lib",
                    android_jar,
                    "--min-api",
                    "21",
                    "--pg-conf",
                    APP / "proguard-rules.pro",
                    "--pg-map-output",
                    out / "mapping.txt",
                    "--output",
                    work,
                    *classes,
                ]
            )
        else:
            run([tools / "d8", "--lib", android_jar, "--min-api", "21", "--output", work, *classes])
        shutil.copyfile(work / "base.apk", apk)
        with zipfile.ZipFile(apk, "a") as archive:
            archive.write(work / "classes.dex", "classes.dex")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    return {"apk": str(apk), "sha256": digest, "buildTools": tools.name, "platform": android_jar.parent.name}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sdk", help="Pasta do Android SDK")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "testapp")
    parser.add_argument(
        "--obfuscate", action="store_true", help="Passar pelo R8 (renomeia classes fora dos componentes)"
    )
    args = parser.parse_args(argv)
    try:
        summary = build(args.out, args.sdk, args.obfuscate)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
