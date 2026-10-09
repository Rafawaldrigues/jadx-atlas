# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-09

First public release: the class-hierarchy viewer becomes an attack-surface map for Android apps.

### Added
- Installable package (`pyproject.toml`) with the `jadx-atlas` command (`serve`, `diff`, `export`).
- AndroidManifest attack surface: effective export with its reason (AOSP rules), unknown values with an
  explained guess, permissions, providers and deep links; Surface panel and app alerts.
- Roles from inheritance (Activity, Service, TrustManager, WebViewClient...) with path and confidence,
  through a framework table generated with `javap` from official artifacts.
- 30 finding-candidate rules (WebView, TLS, crypto, execution, storage/IPC, secrets) with `high`,
  `medium` or `low` confidence; secrets are always masked.
- Intent edges (`launches`, `sends_action`, `registers_receiver`) and edge layers on the map.
- Possible paths from exposed entries to classes with findings (`/api/paths`, Paths tab).
- Version diff (`jadx-atlas diff`, `--fail-on` for CI) and the Compare versions dialog.
- Markdown and JSON reports with a documented schema (`jadx-atlas export`, `--anonymize`,
  `--include-secrets`, `--apk`).
- Obfuscated code support: JADX aliases, obfuscation estimate, global search, hideable libraries,
  package grouping, analyst notes stored outside the analysed folder.
- Parallel indexing, JSON disk cache and on-demand routes (`/api/summary`, `/api/list`, `/api/neighbors`).
- Per-run session token on `/api/`, `--verbose` structured logs and a CSP without `unsafe-inline`.
- CI on Linux, Windows and macOS, ruff, front-end tests with `node --test`, MIT licence and security policy.

### Changed
- Plain desktop-style UI (grey panels, square corners, titled borders); the map draws white boxes with
  black outlines and hollow inheritance arrows.
- Cytoscape.js 3.34.3 and cytoscape-dagre 4.0.1, which bundles dagre.
- `tree-sitter` is kept below 0.26: 0.26.0 crashes in the query engine on real JADX output.

### Fixed
- The map no longer breaks on unknown edge kinds.
- Incomplete `java.lang` list; code inside anonymous classes is now analysed.
- "Export map" no longer includes the folder's absolute path.

## [0.1.0]

- Local explorer of `extends`/`implements` relations in Java code exported by JADX.

[Unreleased]: https://github.com/Rafawaldrigues/jadx-atlas/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Rafawaldrigues/jadx-atlas/releases/tag/v0.2.0
