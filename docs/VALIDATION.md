# Validation

How the analysis results were checked against independent references. Automated versions of these
checks live in `tests/test_jadx_integration.py` and run whenever `javac`, JADX and the Android SDK are
available.

## Test app

`tests/apk/testapp` is a small synthetic app written for this project (no third-party code, no licensing
question). `python scripts/build_test_apk.py` builds it with the Android SDK (build-tools 36.0.0,
android-37.0, `javac --release 11`, d8 `--min-api 21`); `--obfuscate` runs R8 instead of d8. The APK is
unsigned and never committed; its hash changes on every build because the zip stores timestamps.

It contains one component for every export rule branch, plus a deliberately vulnerable chain:
`DeepLinkActivity` (exported through a deep link) → `PaymentActivity` (not exported) →
`InsecureClient` (an anonymous `X509TrustManager` that accepts every certificate).

Exported with `jadx -d out atlas-testapp.apk` (JADX 1.5.6) → `out/resources/AndroidManifest.xml` and
`out/resources/res/values/bools.xml`. With `--no-res`, `out/resources/` is empty.

## Attack surface (AndroidManifest)

**Independent reference:** `aapt2 dump xmltree --file AndroidManifest.xml atlas-testapp.apk` reads the
**binary** manifest straight from the APK, without JADX. The "Expected" column applies the platform rules
(AOSP `ParsedActivityUtils`, `ParsedServiceUtils`, `ParsedProviderUtils`) to the aapt2 output with
`targetSdkVersion=30`.

| Component (aapt2) | `exported` in the binary | Filters | Expected | Atlas | Class linked |
|---|---|---|---|---|---|
| activity `.MainActivity` | true | 1 | exported | `true` (explicit) | ✓ |
| activity `.DeepLinkActivity` | missing | 2 (VIEW+BROWSABLE) | implicitly exported, 2 deep links | `true` (implicit through intent-filter), `https://atlas.example/open` (autoVerify) and `atlas:` | ✓ |
| activity `.InternalActivity` | false | 0 | not exported | `false` (explicit) | ✓ |
| activity `ResourceActivity` | `@0x7f010000` (= `@bool/export_resource` = true) | 1 | exported (resolves to true) | `unknown`, guess `true` (`res/values/bools.xml`) | ✓ |
| activity-alias `.AliasLauncher` → `.InternalActivity` | true | 0 | exported, no inherited permission | `true`, class `InternalActivity` | ✓ |
| activity `.PaymentActivity` | false | 0 | not exported | `false` (explicit) | ✓ |
| service `.SyncService` | true, permission SYNC | 0 | exported, `signature` permission | `true`, `signature` | ✓ |
| service `.LocalService` | missing | 0 | not exported | `false` | ✓ |
| receiver `.BootReceiver` | missing | 1 | implicitly exported | `true` (implicit) | ✓ |
| receiver `Outer$InnerReceiver` | true | 0 | exported | `true`; JADX writes `Outer.InnerReceiver` | ✓ |
| receiver `.MissingReceiver` | true | 0 | exported; class not compiled | `true`, `class: null`, with a warning | — (correct) |
| provider `.DataProvider` | true, readPermission READ_DATA | 0 | exported; **write side unprotected** | `true`, no effective permission (weakest side) | ✓ |
| provider `.LegacyProvider` | missing | 0 | not exported (targetSdk 30 ≥ 17) | `false` | ✓ |

**Result:** 13 of 13 components match. The one "potentially exported" component (`ResourceActivity`) has a
real value of `true`, so the guess was right.

**Notes:**
- aapt2 shows relative names (`.MainActivity`); JADX expands them (`br.atlas.testapp.MainActivity`) except
  for `activity-alias`, whose name and `targetActivity` stay relative. Atlas normalises both.
- In the binary, `ResourceActivity`'s `exported` is a resource id. JADX turns it back into
  `@bool/export_resource`, which is why Atlas can offer a guess without asserting the value: another
  configuration (`values-v31`, …) could change it.

## Roles from inheritance

| Criterion | Result |
|---|---|
| Every manifest activity (5 activities + 1 alias → `InternalActivity`) gets the `activity` role | 6/6, `high` confidence (`roleCheck`) |
| No class without an Activity ancestor gets the role | 0 extra classes |
| Services, receivers, providers and Application | 2, 2 (including `Outer.InnerReceiver`), 2 and 1, all `high` |

Demo (AndroidX): `CheckoutActivity → BaseActivity → AppCompatActivity → FragmentActivity →
androidx.activity.ComponentActivity → androidx.core.app.ComponentActivity → Activity` (high).
`BaseActivity` (abstract, not declared) is flagged as `undeclaredComponent`, as information only.

Framework table: `python scripts/framework_hierarchy.py --check` → both data files match
`android.jar`/`javap` (76 types from 13 sources; 102 `java.lang` types).

## Finding candidates on real code

`tests/test_rules.py` has a positive and a negative case for **each** of the 30 rules, plus confidence,
context, shadowing, anonymous-class, syntax-error and scaling cases.

Real code with no planted vulnerability: AndroidX appcompat 1.7.0, core 1.13.1, fragment 1.8.5,
activity 1.9.3, firebase-messaging 24.1.0 and support library 28.0.0, decompiled with JADX 1.5.6
(887 files, 2,032 types).

| Rule | Findings | Manual review |
|---|---|---|
| exec-reflection (info) | 175 | Expected in libraries (reflection for backwards compatibility). |
| pendingintent-mutable | 11 | 2 `high` in `SearchView` (`FLAG_ONE_SHOT` without `FLAG_IMMUTABLE`: real candidates for targetSdk < 31); 9 `low` (flags from a variable or a helper such as `addMutabilityFlags`). |
| broadcast-without-permission (info) | 2 | `ShortcutManagerCompat`: explicit Intent built earlier (documented false positive). |
| crypto-weak-hash | 1 | `GmsRpc`: SHA-1 used to derive an identifier (non-cryptographic use, documented false positive). |

Before tuning, the same code produced 3 crypto false positives (an implicit `getInstance(...)` call in
`FirebaseMessaging`) and 9 entropy false positives (Kotlin `@Metadata` strings). Both now have
regression tests.

## Possible paths

Atlas result (Paths tab, entry `DeepLinkActivity`, default targets):

```
Path 1 → br.atlas.testapp.InsecureClient (confidence high, 2 steps)
  DeepLinkActivity --launches (startActivity)--> PaymentActivity   [br/atlas/testapp/DeepLinkActivity.java:15]
  PaymentActivity --uses (type reference)--> InsecureClient        [br/atlas/testapp/PaymentActivity.java:10]
```

The `tls-trustmanager-accepts-all` finding (high, `inAnonymous: true`) is attributed to
`InsecureClient`. With R8 (`--obfuscate`), R8 inlines `InsecureClient` into `PaymentActivity` and the
TrustManager becomes class `a.a`; Atlas still finds `DeepLinkActivity → PaymentActivity → a.a`.

**Compared with manual navigation in the JADX GUI** (navigation actions counted, not timed with users):

| Approach | Actions to connect the exposed entry to the vulnerable code |
|---|---|
| JADX GUI, starting from the problem | 1) search `checkServerTrusted`; 2) open `InsecureClient`; 3) find usages of `InsecureClient`; 4) open `PaymentActivity`; 5) find usages of `PaymentActivity`; 6) open `DeepLinkActivity`; 7) open `AndroidManifest.xml` to confirm it is exported with a deep link (and that `PaymentActivity` is not). **7 actions**, and only once you know what to look for. |
| JADX GUI, starting from the entries | Open the manifest and walk each of the 9 exposed entries until the TrustManager shows up: dozens of actions. |
| JADX Atlas | 1) Paths tab; 2) search from the suggested entry (or go through the entries); 3) click the steps to see the evidence. **3 actions**, and the finding is already listed in the Findings panel without knowing what to look for. |

## Limitations of this validation

- The test app is small and the vulnerable chain was planted on purpose. On real apps, `uses` edges
  produce many plausible but irrelevant paths; the expansion limit and ordering reduce, but do not remove, them.
- The real-code check above uses libraries, not a complete third-party app with known vulnerabilities.
