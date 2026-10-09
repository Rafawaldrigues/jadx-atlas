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

**Resultado:** 12 de 12 componentes conferem. (Na Fase 5 o app ganhou um 13º componente, `PaymentActivity`, não exportado, para a cadeia plantada; o teste automático cobre os 13.) `exported=true` confirmados: 8; potencialmente exportados: 1 (`ResourceActivity`, valor real `true`, então o palpite estava certo). O teste `tests/test_jadx_integration.py::ApkManifestIntegrationTests` refaz esta conferência automaticamente quando javac, JADX e Android SDK estão disponíveis.

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

## Fase 3 · Uso de APIs sensíveis (2026-10-08)

**Testes de regra:** `tests/test_rules.py` tem um caso positivo e um negativo para **cada** uma das 30 regras (o negativo inclui receptores de outro tipo, argumentos seguros e placeholders), além de casos de confiança, contexto, sombreamento, classes anônimas, arquivo com erro de sintaxe e custo.

**Código real (sem vulnerabilidade plantada):** AndroidX appcompat 1.7.0, core 1.13.1, fragment 1.8.5, activity 1.9.3, firebase-messaging 24.1.0 e support 28.0.0 (appcompat-v7, support-compat), decompilados com JADX 1.5.6: 887 arquivos, 2.032 tipos.

| Regra | Achados | Revisão manual |
|---|---|---|
| exec-reflection (info) | 175 | Esperado em bibliotecas (compatibilidade por reflexão). |
| pendingintent-mutable | 11 | 2 com `high` em `SearchView` (`FLAG_ONE_SHOT` sem `FLAG_IMMUTABLE`, candidatos reais em targetSdk < 31); 9 com `low` (flags vindas de variável ou helper, como `addMutabilityFlags`). |
| broadcast-without-permission (info) | 2 | `ShortcutManagerCompat`: broadcast com Intent explícito construído antes (falso positivo documentado). |
| crypto-weak-hash | 1 | `GmsRpc`: SHA-1 para derivar identificador (uso não criptográfico, falso positivo documentado). |

Antes dos ajustes da D-016, a mesma base gerava 3 falsos positivos de criptografia (`getInstance(...)` implícito em `FirebaseMessaging`) e 9 de entropia (strings `@Metadata` do Kotlin). Os dois casos ganharam testes de regressão.

**Desempenho:** 1,0 s sem regras contra 1,44 a 1,56 s com regras (+45 a 55%) nesses 2.032 tipos; detalhes em docs/PERFORMANCE.md.

## Fase 5 · Caminhos da entrada até o ponto sensível (2026-10-09)

**Alvo:** o mesmo app sintético, agora com a cadeia plantada da D-022. SHA-256 do build: `9ffbde85b678e6fa9f1f54d9691b1624fe20a35dc67793bd8141d0ed17f0541a`. Exportado com JADX 1.5.6.

**Resultado do Atlas** (aba Caminhos, entrada `DeepLinkActivity`, alvo padrão):

```
Caminho 1 → br.atlas.testapp.InsecureClient (confiança high, 2 passos)
  DeepLinkActivity --launches (startActivity)--> PaymentActivity   [br/atlas/testapp/DeepLinkActivity.java:15]
  PaymentActivity --uses (referência de tipo)--> InsecureClient    [br/atlas/testapp/PaymentActivity.java:10]
```

O achado `tls-trustmanager-accepts-all` (high, `inAnonymous: true`) fica em `InsecureClient`, e as 9 entradas padrão vêm do Manifest.

**Comparação com a busca manual no JADX** (contagem de ações de navegação, não tempo medido com usuários):

| Abordagem | Ações até ligar entrada exposta → ponto vulnerável |
|---|---|
| JADX GUI, partindo do problema | 1) buscar `checkServerTrusted`; 2) abrir `InsecureClient`; 3) "find usage" de `InsecureClient`; 4) abrir `PaymentActivity`; 5) "find usage" de `PaymentActivity`; 6) abrir `DeepLinkActivity`; 7) abrir o `AndroidManifest.xml` e confirmar que ela é exportada e tem deep link (e que `PaymentActivity` não é). **7 ações**, e isso só depois de saber o que procurar. |
| JADX GUI, partindo das entradas | Abrir o Manifest e percorrer cada uma das 9 entradas expostas até achar o TrustManager: dezenas de ações. |
| JADX Atlas | 1) aba Caminhos; 2) buscar com a entrada sugerida (ou percorrer as entradas no seletor); 3) clicar nos passos para ver a evidência. **3 ações**, e o achado já aparece no painel Achados sem que se saiba o que procurar. |

**Limitações honestas:** a cadeia foi plantada por mim, num app pequeno. Em apps reais, as arestas `uses` dão muitos caminhos plausíveis mas irrelevantes (o limite de expansões e a ordenação reduzem, mas não eliminam). Falta a validação com um app de CTF.
