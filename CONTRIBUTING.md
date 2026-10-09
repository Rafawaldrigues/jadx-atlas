# Contributing

Contributions are welcome. Please follow these project rules ([docs/DESIGN.md](docs/DESIGN.md) explains why):

1. Honest resolution: never join classes by short name; every inferred fact carries a confidence and evidence.
2. Findings are candidates: every new rule comes with a positive case, a negative case, known false
   positives and verified official references.
3. Local only: no network calls at run time, no telemetry, no CDN. The CSP stays strict.
4. Hostile input: code, the manifest and APK strings are untrusted. In the browser, use `textContent` only.
5. Minimal dependencies: a new dependency needs a written justification in `docs/DESIGN.md`.
6. Licences: do not copy code from GPL projects. Do not add APKs or decompiled third-party code.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check . && ruff format --check .
npm run check
```

The end-to-end APK test runs when `javac`, `jadx` and the Android SDK are available. Use
`python scripts/build_test_apk.py [--obfuscate]` to build the test APK. If you change the Markdown report,
regenerate the golden file with `ATLAS_UPDATE_GOLDEN=1 python -m unittest tests.test_report` and review the diff.

## Commits

Small commits, one logical change each, formatted as `type(scope): description`
(for example `feat(manifest): parse exported components`). Add a line to the *Unreleased* section of
`CHANGELOG.md`.
