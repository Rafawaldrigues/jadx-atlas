# Validação manual

## Fase 1 · Superfície de ataque do AndroidManifest (2026-10-08)

**Alvo:** app sintético `tests/apk/testapp` (código deste projeto, sem questão de licença), construído com `python scripts/build_test_apk.py` (build-tools 36.0.0, android-37.0, javac `--release 11`, d8 `--min-api 21`). O APK não é assinado nem versionado. SHA-256 do build usado: `3460e77e1cbd467d4899ea20a281230dc9828e6c070d5865582cbe90f9b37809` (o hash muda a cada build, porque o zip guarda datas).

**Exportação:** `jadx -d jadx-out atlas-testapp.apk` (JADX 1.5.6) → `jadx-out/resources/AndroidManifest.xml` e `jadx-out/resources/res/values/bools.xml`. Com `--no-res`, a pasta `resources/` fica vazia.

**Referência independente:** `aapt2 dump xmltree --file AndroidManifest.xml atlas-testapp.apk` lê o Manifest **binário** direto do APK, sem passar pelo JADX. A coluna "Esperado" aplica à saída do aapt2 as regras da plataforma (AOSP `ParsedActivityUtils`, `ParsedServiceUtils` e `ParsedProviderUtils`) com `targetSdkVersion=30`.

| Componente (aapt2) | `exported` no binário | Filtros | Esperado | Atlas | Classe ligada |
|---|---|---|---|---|---|
| activity `.MainActivity` | true | 1 | exportado | `true` (explícito) | ✓ |
| activity `.DeepLinkActivity` | ausente | 2 (VIEW+BROWSABLE) | exportado implícito, 2 deep links | `true` (implícito por intent-filter), `https://atlas.example/open` (autoVerify) e `atlas:` | ✓ |
| activity `.InternalActivity` | false | 0 | não exportado | `false` (explícito) | ✓ |
| activity `ResourceActivity` | `@0x7f010000` (= `@bool/export_resource` = true) | 1 | exportado (resolve para true) | `unknown`, palpite `true` (`res/values/bools.xml`) | ✓ |
| activity-alias `.AliasLauncher` → `.InternalActivity` | true | 0 | exportado, sem permissão herdada | `true`, classe `InternalActivity` | ✓ |
| service `.SyncService` | true, permission SYNC | 0 | exportado com permissão `signature` | `true`, `signature` | ✓ |
| service `.LocalService` | ausente | 0 | não exportado | `false` | ✓ |
| receiver `.BootReceiver` | ausente | 1 | exportado implícito | `true` (implícito) | ✓ |
| receiver `Outer$InnerReceiver` | true | 0 | exportado | `true`; o JADX escreve `Outer.InnerReceiver` | ✓ |
| receiver `.MissingReceiver` | true | 0 | exportado; classe não compilada | `true`, `class: null`, com aviso | — (correto) |
| provider `.DataProvider` | true, readPermission READ_DATA | 0 | exportado; **escrita sem permissão** | `true`, sem permissão efetiva (lado mais fraco) | ✓ |
| provider `.LegacyProvider` | ausente | 0 | não exportado (targetSdk 30 ≥ 17) | `false` | ✓ |

**Resultado:** 12 de 12 componentes conferem. `exported=true` confirmados: 8; potencialmente exportados: 1 (`ResourceActivity`, valor real `true`, então o palpite estava certo). O teste `tests/test_jadx_integration.py::ApkManifestIntegrationTests` refaz esta conferência automaticamente quando javac, JADX e Android SDK estão disponíveis.

**Divergências e observações:**
- O aapt2 mostra nomes relativos (`.MainActivity`), e o JADX os expande (`br.atlas.testapp.MainActivity`), exceto em `activity-alias`, onde o nome e o `targetActivity` continuam relativos. O Atlas normaliza os dois casos.
- No binário, `exported` de `ResourceActivity` é um id de recurso (`@0x7f010000`). O JADX o converte de volta para `@bool/export_resource`. Por isso o Atlas consegue dar um palpite, mas não afirma o valor: outra configuração (`values-v31` etc.) poderia mudá-lo.
- MobSF não foi usado. O aapt2 lê o mesmo binário que o instalador do Android lê.

**Limitação desta validação:** o app é sintético e pequeno. A conferência em um app real de terceiros (open source ou de CTF) fica pendente.

## Fase 2 · Papéis por herança (2026-10-08)

**Mesmo APK sintético e mesma exportação da Fase 1.**

| Critério | Resultado |
|---|---|
| Todas as activities do Manifest (4 activities + 1 alias → `InternalActivity`) têm papel `activity` | 5/5, confiança `high` (`roleCheck`) |
| Nenhuma classe sem ancestral de Activity recebe o papel | 0 classes extras |
| Services, receivers, providers e Application | 2, 2 (incluindo `Outer.InnerReceiver`), 2 e 1, todos com `high` |

**Demonstração (AndroidX):** `CheckoutActivity → BaseActivity → AppCompatActivity → FragmentActivity → androidx.activity.ComponentActivity → androidx.core.app.ComponentActivity → Activity` (alta). `BaseActivity` (abstrata, não declarada) é marcada como `undeclaredComponent`, só como informação.

**Conferência da tabela:** `python scripts/framework_hierarchy.py --check` → "framework_hierarchy.json confere com javap" (76 tipos, 13 fontes).

A conferência automática fica em `tests/test_jadx_integration.py::ApkManifestIntegrationTests`. Os casos sintéticos (ofuscação, ciclos, ambiguidade, coincidência de nome curto) ficam em `tests/test_roles.py`.
