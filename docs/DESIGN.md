# Design notes

Why JADX Atlas works the way it does. Code comments point to the sections below.

## Principles

- Honest resolution. Classes are never joined by short name. Every inferred fact (relation, role,
  finding, edge, path) carries a resolution status or a confidence (`high`, `medium`, `low`) and evidence
  (file, line, snippet). Unknown stays unknown.
- Candidates, not verdicts. Findings and paths are leads for manual review. Each rule documents its
  known false positives.
- Local only. No network calls at run time, no telemetry, no CDN. The server binds to `127.0.0.1`.
- Hostile input. Decompiled code, the manifest and APK strings are untrusted (see *Security of the tool*).
- Minimal dependencies. Runtime: `tree-sitter`, `tree-sitter-java`, `defusedxml`. Front-end libraries
  (Cytoscape.js, dagre) are vendored with their MIT licences.

## Indexing and name resolution

- Declarations are parsed with Tree-sitter (no regular expressions). References resolve through lexical
  scope, explicit imports, the current package, wildcard imports and `java.lang`. Ambiguous names keep
  their candidate list; unresolved names get a scope-specific placeholder id, so two unknown `Base`
  classes are never merged.
- `java.lang` is generated from `android.jar` (`atlas/data/java_lang.json`, 102 types). A single wildcard
  import of a framework package that contains the name (per the framework table) resolves to it; with
  several wildcards the name stays unresolved, because another package could contain it.
- Anonymous and local classes do not get nodes (their names are not stable), but their code is analysed
  and attributed to the enclosing named class with `inAnonymous: true`.

## Manifest and attack surface

Rules follow the AOSP component parsers (`frameworks/base/core/java/com/android/internal/pm/pkg/component/`):

- Provider without `exported`: exported by default when `targetSdkVersion < 17`; intent filters do not matter.
- Activity and receiver without `exported`: exported when they have an intent filter. A filter **without
  an `<action>` is dropped** by the platform (`failOnNoActions=true`). With targetSdk ≥ 31 the app fails to
  install (`MISSING_EXPORTED_FLAG`), so Atlas reports `inconsistent`.
- Service: same, but an action-less filter **counts** (`failOnNoActions=false`).
- Permissions: activity, service and receiver inherit `<application android:permission>`; `activity-alias`
  does **not**; a provider uses `readPermission`/`writePermission`, then `permission`, then the
  application's. Exposure is judged by the weakest side (a provider readable or writable without a
  permission is exposed).
- Unknown values carry a guess. `exported="@bool/x"` is reported as `unknown`; the guess comes from
  `res/values*/bools.xml` (several configurations → the more exposed value) or from the implicit rule,
  always with its reason and `low` confidence. These count as "potentially exported", never as confirmed.
- Parsing uses `defusedxml` SAX (DTDs, entities and external references forbidden; size, depth and element
  limits; binary AXML detected) because the SAX locator gives line numbers for evidence.
- JADX writes `resources/AndroidManifest.xml` next to `sources/` (nothing with `--no-res`); apktool puts it
  next to the sources. A missing manifest is not a warning: plain Java folders are a supported use.

## Roles from inheritance

- `atlas/data/framework_hierarchy.json` is **generated** by `scripts/framework_hierarchy.py` with `javap`
  from `android.jar` (`--system none`, so `java.*` comes from Android, not the local JDK) and from official
  Google Maven artifacts (AndroidX, support library, Firebase). Each type records its source and version;
  `--check` re-verifies. This revealed details easy to get wrong by hand: AppCompat goes through **two**
  `ComponentActivity` classes, and `FirebaseMessagingService` through `EnhancedIntentService`.
- Roles are computed per role from its defining types downwards (Dijkstra keyed by weakest confidence, then
  depth), so cycles terminate and the cost is O(roles × edges) (0.05 s for 20,000 classes).
- Path confidence is the weakest edge: resolved/external/framework = `high`; an ambiguous edge = `medium`
  (2 candidates) or `low` (more); unresolved edges stop the chain.
- Declared components are cross-checked (`roleCheck`); a class with a component ancestor but no
  declaration is flagged `undeclaredComponent` (information only).

## Finding rules

- Facts come from **one** Tree-sitter query on the same parse tree, with `#any-of?` predicates so
  irrelevant method names never reach Python, and a byte-level prefilter for strings. Only events some rule
  can match are kept.
- The receiver's *declared* type is looked up lexically (locals declared earlier, parameters,
  catch/for/try variables, fields of enclosing classes). This is not type inference.
- **Confidence:** `high` when the declared type (or created type) resolves through the file's imports to the
  API or a known subtype, for implicit calls in a class whose ancestry contains the API, and for static
  factories (`Runtime.getRuntime().exec`); `medium` when the method and an import match but the receiver
  type is unknown (chained getter, inherited field); `low` when only the method name matches or an argument
  cannot be evaluated. A receiver that resolves to another type, or an implicit call in a class whose
  superclass chain is known and does not contain the API, never matches.
- Secrets are always masked in the payload (`AKIA…[20 chars]`); full values stay in memory and are
  exported only with `--include-secrets`. Strings inside annotations (Kotlin `@Metadata`) are ignored.
- At most 20 findings per rule and class.

## Intents and paths

- Intent edges (`launches`, `sends_action`, `registers_receiver`) live in `payload.intentEdges`, separate
  from inheritance `edges`, so roles and hierarchy views are unaffected. Flow is intra-method and in
  document order; JADX's `(Class<?>) X.class` casts are unwrapped. Untraceable intents are recorded with a
  reason and never become invented edges. Actions match manifest intent filters with `medium` confidence.
- `uses` edges (object creation, static calls, local/field types) are kept server-side as adjacency lists.
- Path search is a breadth-first search over simple paths with limits (depth 6 by default, 3 paths per
  target, 30 in total, 2 s, 3 expansions per node), deterministic ordering and weakest-link confidence.
  Every result says `approximate: true`.

## Version diff

- Order of trust: manifest facts (names survive obfuscation), then findings, then the `extends` chain of
  components present in both versions.
- Finding identity is `ruleId` + class + hash of the whitespace-normalised snippet; line numbers are not
  part of it.
- Renamed obfuscated classes (short names outside the manifest) are paired only when their structural
  fingerprint (kind, roles, external parents, string constants, rules) is unique on both sides, and always
  labelled "possible match".

## Reports

- One JSON format for the CLI and the UI, described by `docs/schema/atlas-index.schema.json` and checked in
  the tests by a small validator (`tests/schema_check.py`) instead of an extra dependency. The absolute
  import path is never exported.
- `--anonymize` replaces the app's packages, classes, file names and URL hosts with deterministic
  identifiers. There is no SARIF output: validating it against the official schema would need a new
  dependency.

## Obfuscated code

- JADX 1.5.6 writes `/* JADX INFO: renamed from: a.a */` (with `--deobf`) and
  `/* JADX INFO: compiled from: Helper.java */`; older versions omit `JADX INFO:`. Samples generated from
  this project's code are in `tests/fixtures/jadx-1.5.6/`.
- R8 (tested with 8.10.9) replaces the SourceFile attribute with `"SourceFile"`, can keep `Outer$Inner` as a
  top-level class with `$` in its name, and produces lowercase class names. All three are handled.
- Obfuscation estimate: share of classes with a simple name of at most 2 characters or renamed by JADX
  (< 10 % low, < 40 % medium, otherwise high).
- Analyst notes are stored in the user data directory (`~/.local/share/jadx-atlas`, Application Support,
  APPDATA, or `ATLAS_DATA_DIR`), never inside the analysed folder.

## Performance

- Parsing runs in a process pool from 300 files (one parser per process; results in submission order, so
  the index is identical to a serial run).
- The per-file cache is keyed by path, mtime and size, plus a global key over the tool version and every
  data file; it is gzipped JSON (never pickle, so a cache file cannot execute code) in the user cache
  directory, never inside the analysed folder.
- `/api/project` refuses payloads above `ATLAS_MAX_PAYLOAD_MB` (150 by default) and points to the on-demand
  routes (`/api/summary`, `/api/list`, `/api/neighbors`, `/api/search`); the UI does not load those
  projects.

## Security of the tool

- **Session token:** each run generates a random token, passed in the URL fragment (never sent in requests,
  Referer headers or logs), kept in `sessionStorage` and required in `X-Atlas-Token` on `/api/` routes
  (constant-time comparison). `Host` and `Origin` are validated as well.
- **CSP:** everything is `'self'` (scripts, styles, connections), `frame-ancestors 'none'`, and no
  `unsafe-inline`. Cytoscape's injected container style is shipped as `cytoscape.css` instead.
- The browser code never uses HTML injection sinks; a static test enforces it. Symbolic links are ignored,
  file sizes are capped, and `--verbose` logs never include query strings or code.

## Packaging and tests

- The version lives in `atlas/__init__.py`. The UI, demo and data files ship as package data, so wheel and
  `pipx` installs work.
- CI: Python 3.10–3.14 on Linux, 3.14 on Windows and macOS, ruff, front-end tests (`node --test`),
  dependency audit, a package build check, an optional JADX integration job and an informative benchmark.
