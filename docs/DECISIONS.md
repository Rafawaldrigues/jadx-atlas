# Registro de decisões

Formato: contexto, decisão, alternativas consideradas. Decisões novas entram no fim.

## D-001 · Licença MIT (2026-10-08, Fase 0)

- **Contexto:** o projeto vai ser público, e as bibliotecas incluídas em `atlas/web/vendor/` (Cytoscape.js, dagre, cytoscape-dagre) são MIT. `tree-sitter` e `tree-sitter-java` também são MIT.
- **Decisão:** MIT, com o titular `Rafawaldrigues` (o nome de usuário do GitHub).
- **Alternativas:** Apache-2.0 (dá concessão explícita de patentes, mas o texto é mais longo); GPL foi descartada para permitir uso em ferramentas internas de terceiros.

## D-002 · `web/` e `examples/` dentro do pacote `atlas` (Fase 0)

- **Contexto:** `app.py` achava `web/` e `examples/` por caminho relativo ao próprio arquivo, na raiz do repositório. Isso funciona em `pip install -e .`, mas falha em instalação por wheel ou `pipx` (planejada na Fase 10).
- **Decisão:** mover para `atlas/web/` e `atlas/examples/` (declarados como `package-data`), e o servidor para `atlas/server.py`. `app.py` fica na raiz como um *shim* de compatibilidade (reexporta `BASE`, `Handler` e `State`, que os testes existentes importam). Conferido com um venv novo e instalação não editável.
- **Alternativas:** mapear diretórios com `package-dir` do setuptools (frágil em modo editável), ou ler arquivos via `importlib.resources` mantendo a raiz (o servidor HTTP precisa de um diretório real).

## D-003 · Python ≥ 3.10 (Fase 0)

- **Contexto:** `tree-sitter==0.25.2` exige `>=3.10` e publica wheels de cp310 a cp314. `tree-sitter-java==0.23.5` é abi3 (`>=3.9`).
- **Decisão:** `requires-python = ">=3.10"`. O CI testa 3.10, 3.11, 3.12, 3.13 e 3.14. Localmente foram rodados 3.14.7 (com JADX 1.5.6) e 3.12.3. As dependências ficam limitadas à série menor (`>=0.25.2,<0.26`), porque `tree-sitter` 0.26 já existe e a API do binding muda entre versões menores.
- **Alternativas:** pinos exatos (seguro, mas impede correções de segurança de patch).

## D-004 · `asmx.css` mantido e renomeado para `theme.css` (Fase 0)

- **Contexto:** suspeitava-se que `web/asmx.css` era um resto de outro projeto. Não é: `index.html` o carrega, e ele define o tema visual inteiro.
- **Decisão:** manter o arquivo e renomeá-lo para `theme.css`. O comentário de origem ("adapted from ASMX") foi preservado. **Pendente:** confirmar que o ASMX é seu ou que a licença dele permite essa adaptação.

## D-005 · Ruff com `line-length = 120`, sem E501 (Fase 0)

- **Contexto:** o código usava linhas longas e compactas. Fixtures Java nos testes ficam mais legíveis em uma linha só.
- **Decisão:** `ruff format` aplicado uma vez a todo o código, em um commit separado (`style:`). Regras `E, F, W, I, UP, B`, ignorando `E501`. O formatador cuida do comprimento sempre que consegue. A versão do ruff está fixada no CI (`0.16.10`).

## D-006 · Comando `jadx-atlas` com subcomandos (Fase 0)

- **Decisão:** `jadx-atlas [serve] [pasta] [--port N] [--no-browser]`. Sem subcomando conhecido, o primeiro argumento é tratado como pasta e o comando é `serve`, o que mantém o comportamento do antigo `app.py`. `export` e `diff` entram nas fases 6 e 7. `--port 0` escolhe uma porta livre.

## D-007 · Gerador sintético com nomes seguros (Fase 0)

- **Decisão:** `scripts/gen_large_project.py` usa só letras minúsculas nos nomes ofuscados, para não colidir em sistemas de arquivos que ignoram maiúsculas. O primeiro segmento dos pacotes ofuscados leva um dígito (`a3.k`), para nunca coincidir com um nome de classe e não criar FQNs obscurecidos (JLS §6.4.2). O gerador só apaga uma pasta que contenha o marcador `.atlas-generated`.

## D-008 · Onde o JADX grava o Manifest (Fase 1, passo 0)

- **Verificado** com JADX 1.5.6 e um APK sintético (`tests/apk/testapp`): `jadx -d out app.apk` grava `out/resources/AndroidManifest.xml` (XML de texto, namespace `android:`, com `uses-sdk`) e `out/resources/res/values/*.xml`. Com `--no-res`, `out/resources/` fica vazia.
- O JADX expande nomes relativos de `activity`, `service`, `receiver` e `provider`, mas **não** os de `activity-alias` (`android:name` e `targetActivity` continuam com `.Nome`). Ele também escreve classes aninhadas como `Outer.Inner` em vez de `Outer$Inner`.
- Atributos que apontam para recurso continuam como referência (`@bool/x`), e o valor fica em `res/values*/bools.xml`.
- **Decisão:** o localizador procura `P/resources/`, `P/../resources/` e `P/` (apktool), nessa ordem. A normalização aceita `.Nome`, `Nome`, `a.b.Nome` e `$`.

## D-009 · Regras da plataforma conferidas no AOSP (Fase 1)

Fonte: `frameworks/base/core/java/com/android/internal/pm/pkg/component/` (branch `main`, lida em 2026-10-08).
- Provider sem `exported`: o padrão é `targetSdkVersion < 17` (`ParsedProviderUtils`). Os filtros não influenciam.
- Activity e receiver sem `exported`: exportados se houver intent-filter. Um filtro **sem `<action>` é descartado** (`failOnNoActions=true`) e não conta. Com targetSdk ≥ 31 a instalação falha (`MISSING_EXPORTED_FLAG`).
- Service: o mesmo, mas um filtro sem `<action>` **conta** (`failOnNoActions=false`).
- Permissão: activity, service e receiver herdam `<application android:permission>`; **activity-alias não herda**; provider usa `readPermission`/`writePermission`, depois `permission`, depois a da aplicação.

## D-010 · Valor desconhecido ainda leva um palpite (Fase 1)

- **Contexto:** `exported` ou `enabled` podem apontar para um recurso (`@bool/x`), e o Manifest pode ser inconsistente (sem `exported`, com filtro, targetSdk ≥ 31).
- **Decisão:** o valor fica `"unknown"` ou `"inconsistent"`, nunca é inventado, mas o componente carrega `exportedGuess` e `exportedGuessReason`:
  - recurso encontrado com um único valor → palpite = esse valor;
  - recurso que varia por configuração → palpite = a leitura mais exposta (`true` para `exported`), com a lista de variantes;
  - recurso não encontrado → palpite pela regra implícita, explicitando isso;
  - inconsistente com `minSdk < 31` → palpite `true` (em aparelhos antigos ele instala e fica exportado implicitamente).
- A confiança desses casos é `low`. A interface diz "potencialmente exportado" e "EXP?", e eles entram no filtro "Exportado" e em `stats.potentiallyExported`, mas **nunca** em `stats.exportedComponents`.

## D-011 · Ausência de Manifest não é aviso (Fase 1)

- **Contexto:** analisar uma pasta só com Java continua sendo um uso válido, e um teste antigo conta os avisos exatamente.
- **Decisão:** a ausência fica em `stats.manifest = "missing"`, e a interface mostra uma faixa explicando. Um Manifest encontrado mas recusado (DTD, tamanho, AXML) gera aviso e `stats.manifest = "invalid"`. Um caminho informado explicitamente (`--manifest` ou o campo do diálogo) que falha interrompe a importação com erro.

## D-012 · SAX do defusedxml em vez de ElementTree (Fase 1)

- O ElementTree acelerado em C não informa números de linha, e a linha do componente é a evidência exigida pela regra 1. O `defusedxml.sax` com `forbid_dtd`, `forbid_entities` e `forbid_external` dá o locator de linha, e os limites de profundidade e de elementos são aplicados durante a leitura.

## D-013 · Tabela de framework gerada por `javap` (Fase 2)

- **Contexto:** cada cadeia precisava ser verificada nas fontes do SDK ou do AndroidX, com a versão registrada.
- **Decisão:** `atlas/data/framework_hierarchy.json` é **gerado** por `scripts/framework_hierarchy.py` a partir do `javap`, aplicado ao `android.jar` da plataforma `android-37.0` (com `--system none`, para que `java.*` e `javax.*` venham do android.jar e não do JDK local) e aos `classes.jar` de AARs oficiais do Google Maven: appcompat 1.7.0, fragment 1.8.5, activity 1.9.3, core 1.13.1, legacy-support-core-utils 1.0.0, lifecycle-service 2.8.7, multidex 2.0.1, firebase-messaging 24.1.0 e support library 28.0.0. Cada tipo guarda `source`, e `--check` refaz a conferência. O `android-36` instalado não tem `android.jar` (instalação parcial), por isso foi usado o 37.0.
- **Correções em relação ao esperado:** a cadeia do AppCompat tem **dois** `ComponentActivity` (`androidx.activity` → `androidx.core.app`). `FirebaseMessagingService` passa por `EnhancedIntentService`. `WakefulBroadcastReceiver` (AndroidX) fica em `androidx.legacy.content`.
- **Limitação:** outras versões de bibliotecas podem ter cadeias diferentes. Uma classe incluída nas fontes do APK (por exemplo, androidx empacotado) é descrita pelas próprias arestas, não pela tabela.
- **Alternativas:** escrever a tabela à mão (sem verificação) ou ler fontes do AndroidX (não estavam disponíveis localmente).

## D-014 · Cálculo de papéis (Fase 2)

- Busca a partir dos tipos que definem cada papel, descendo pelas arestas invertidas (projeto + tabela), com Dijkstra pela chave (maior confiança, menor profundidade). O conjunto de visitados garante término com ciclos. O custo é O(papéis × arestas); em 20 mil classes sintéticas, 0,05 s.
- A confiança do caminho é a da aresta mais fraca: `resolved`/`external`/framework = `high`; `ambiguous` com 2 candidatos = `medium`, com mais = `low` (cada candidato é seguido). `unresolved` interrompe o caminho. Nunca há junção por nome curto.
- Papéis só vão para classes do projeto, nunca para nós externos. `node.roles = [{role, label, confidence, path, via}]`.
- Cruzamento com o Manifest: `component.roleCheck` = confiança do papel esperado ou `"missing"`, este último com aviso "possível erro de resolução". Classe com ancestral de componente mas não declarada recebe `undeclaredComponent` (só informação).
- Nós externos presentes na tabela recebem o `kind` real (`interface`, `abstract`) e a `framework` de origem.

## D-015 · Extração de fatos de código sem reparse e sem tabela de variáveis guardada (Fase 3)

- **Contexto:** a coleta não pode dobrar o tempo de indexação, e o `payload` não pode guardar corpos de métodos.
- **Decisão:** `atlas/code_facts.py` roda *queries* do tree-sitter (em C) na **mesma árvore** de `parse_file` e guarda só os eventos que alguma regra pode casar: chamadas cujo nome de método está nas regras, criações de tipos das regras, `override` de métodos das regras, identificadores (`MODE_WORLD_*`) e strings que passam no pré-filtro. O tipo declarado do receptor é resolvido **na hora**, subindo pelos escopos (variável local declarada antes, parâmetros, catch/for/try-with-resources, campos das classes envolventes). Por isso a tabela de variáveis não é armazenada. Mapas de campos e constantes ficam em cache por corpo de classe, para evitar custo quadrático.
- Código de classes anônimas e locais é atribuído à classe nomeada que o contém, com `inAnonymous: true`. Para regras de `override`, o tipo da classe anônima (`new X509TrustManager() {...}`) é resolvido pelos imports e passa pela tabela de framework.
- Strings dentro de anotações (por exemplo `@Metadata` do Kotlin) são ignoradas.

## D-016 · Confiança dos achados (Fase 3)

- `high`: o tipo declarado do receptor (ou o tipo criado) resolve pelos imports/pacote para o tipo da API, ou para um subtipo conhecido pela tabela de framework; chamada implícita numa classe (ou numa classe envolvente) cujo ancestral é o tipo da API; fábrica estática (`Runtime.getRuntime().exec`).
- `medium`: o método bate, o arquivo importa a API (ou um tipo indicado em `importHints`) e o receptor não tem tipo determinável (getter encadeado como `getSettings()`, campo herdado). Identificadores `MODE_WORLD_*` também ficam em `medium`.
- `low`: só o nome do método bate; ou um argumento não pôde ser avaliado (variável, concatenação, chamada).
- **Descartado:** receptor cujo tipo resolve para outra classe; chamada implícita numa classe sem `extends` (é `Object`) ou com cadeia de framework conhecida que não contém a API. Esse é o principal filtro contra falsos positivos.
- Limite de 20 achados por regra e classe, para controlar o volume.

## D-017 · Segredos mascarados sempre (Fase 3)

- O payload nunca contém o valor completo de um segredo: o campo `secret` traz os 4 primeiros caracteres e o tamanho, e o `snippet` passa pelo mesmo mascaramento. A opção `--include-secrets` fica para a exportação (Fase 7).

## D-018 · `java.lang` gerado do android.jar e curinga único de framework (Fase 3, corrige A1)

- `atlas/data/java_lang.json` (102 tipos) é gerado pelo mesmo script da tabela de framework. A lista antiga, escrita à mão, é mantida em união, para código Java que não é Android.
- `import android.webkit.*;` com **um único** import curinga resolve `WebViewClient` para `android.webkit.WebViewClient` quando esse tipo está na tabela gerada por `javap`. Com mais de um curinga, continua `unresolved`: um pacote desconhecido poderia ter um tipo com o mesmo nome.

## D-019 · Arestas de Intent em campo separado (Fase 4)

- **Contexto:** papéis (Fase 2), "herança completa" e estatísticas de relações percorrem `edges` assumindo herança.
- **Decisão:** as arestas de Intent ficam em `payload.intentEdges` (`launches`, `sends_action`, `registers_receiver`), com `via`, `line`, `confidence` e, quando houver, `action`/`actions`/`component`. `edges` continua só com herança. A interface junta os dois conforme a camada escolhida (Herança, Intents ou Todas).
- **Fluxo:** apenas dentro do método e em ordem de documento. Vale a última atribuição da variável antes da chamada, seguida das chamadas `setClass`/`setClassName`/`setComponent`/`setAction`/`setPackage` sobre ela. Builders encadeados (`new Intent(...).setAction(...)`) são seguidos. Não há sensibilidade a desvios (um `if/else` que atribui Intents diferentes fica com a última atribuição no texto) nem análise entre métodos.
- **Confiança:** alvo `X.class` resolvido = `high`; nome de classe em string (`setClassName`) = `medium`; ação ligada a `intent-filter` do Manifest = `medium` (o Android pode escolher outro app, e filtros com categorias e dados não são comparados); `registerReceiver(new X(), ...)` = `high`, e por variável = `medium`.
- **Nunca inventar alvo:** ação dinâmica, Intent vindo de outro método, classe fora das fontes ou ação sem filtro correspondente vão para `node.intents.unresolved`, com o motivo.
- **Constantes:** `static final String` literais da própria classe, das classes que a envolvem e de outras classes do projeto (`Actions.GO`, resolvido pelos imports). O `javac` já embute constantes do framework como literais, e é isso que o JADX mostra.
- **Marcadores:** `readsIntent` em classes com papel de componente que chamam `getIntent()`; `deepLinkHandler` em componentes com deep link no Manifest. São só marcadores, não análise de fluxo.
- `PendingIntent.getActivity/getService/getBroadcast/getForegroundService` só contam quando o receptor é `PendingIntent`, o que evita confusão com `Fragment.getActivity()`.

## D-020 · Arestas `uses` compactas e fora do payload (Fase 5)

- **Definição:** criação de objeto, chamada estática (`Tipo.metodo()`) e tipo declarado de variável local ou campo que resolva (pelo mesmo `resolve()`) para uma classe do projeto. Parâmetros, tipos de retorno e casts ficam de fora: dobravam o volume e o custo sem acrescentar muito aos caminhos.
- **Armazenamento:** listas de adjacência (`FlowGraph`) no servidor, com a primeira linha de cada referência como evidência. O payload só leva `stats.usesEdges`; os vizinhos chegam por `GET /api/uses?id=` e os caminhos por `GET /api/paths`.
- **Custo:** a extração usa **uma** query do tree-sitter com predicados `#any-of?` (os nomes irrelevantes de método são descartados antes do Python) e um pré-filtro de strings sobre bytes. No AndroidX decompilado (2.032 tipos), a indexação com regras, Intents e `uses` fica em ~1,7 s, contra ~0,95 s sem (docs/PERFORMANCE.md).

## D-021 · Busca de caminhos (Fase 5)

- BFS de caminhos simples (sem repetir nó, então ciclos não travam), com os mais curtos primeiro e limites: `maxDepth` (padrão 6, máximo 10), 3 caminhos por alvo, 30 no total, 2 s de tempo e 3 expansões por nó (o que evita explosão combinatória em grafos densos). Os vizinhos são ordenados por tipo de aresta (`launches` < `sends_action` < `registers_receiver` < `uses` < `extends`) e por id, e o resultado final pelo comprimento, confiança e sequência: **ordem determinística**.
- Um alvo encerra o caminho. Um caminho com a mesma sequência de classes que outro já encontrado (por exemplo, por `uses` depois de `launches`) é descartado.
- A confiança do caminho é a da aresta mais fraca. Todo resultado traz `approximate: true` e a nota "caminho possível; não é prova de alcançabilidade nem de exploração".
- **Entradas padrão:** componentes exportados (inclusive potencialmente), quem trata deep link e a classe Application. **Alvos padrão:** classes com achado `medium` ou `high`. Subir por `extends` é opcional (padrão ligado), porque código herdado roda como a subclasse.

## D-022 · Critério de aceite com cadeia plantada em vez de APK de CTF (Fase 5)

- **Contexto:** o critério pede um APK de CTF com vulnerabilidade conhecida. Baixar um APK de terceiros exige conferir a licença e a autorização de uso.
- **Decisão:** acrescentei ao app sintético próprio (`tests/apk/testapp`) uma cadeia conhecida: `DeepLinkActivity` (exportada por deep link) → `PaymentActivity` (não exportada) → `InsecureClient` (TrustManager anônimo que aceita tudo). A conferência automática está em `tests/test_jadx_integration.py`. **Pendente:** repetir com um app de CTF e registrar em docs/VALIDATION.md.
- Essa validação revelou um bug real: o JADX escreve `new Intent(this, (Class<?>) X.class)`, e o cast não era desembrulhado. Corrigido, com teste de regressão.

## D-023 · Diferença entre versões (Fase 6)

- **Ordem de confiança:** (1) Manifest, cujos nomes são estáveis mesmo com ofuscação: componentes novos e removidos, `exported` alterado (com `false → true` destacado), permissões, deep links, ações dos filtros, flags de `<application>` e versões/SDKs; (2) candidatos a achado novos e removidos; (3) cadeia de herança (só `extends`, incluindo a parte do framework) dos componentes presentes nas duas versões.
- **Identidade de achado:** `ruleId` + classe + SHA-256 (12 caracteres) do trecho com espaços normalizados. A linha **não** entra: ela muda entre versões. Uma chamada que só mudou de linha não aparece como nova.
- **Classes ofuscadas:** só nomes curtos com cara de ofuscação (até 3 caracteres, como `a`, `bc`, `a$b`) e fora do Manifest (que é a âncora) são pareados, pela impressão estrutural (tipo, `abstract`, papéis, pais externos, constantes de string e regras com achado). O pareamento só acontece quando a impressão é **única** dos dois lados: com constantes de string, a confiança é `medium`; sem elas, `low`; classe vazia nunca é pareada. O rótulo é sempre "possível correspondência", não prova de equivalência.
- **CLI:** `jadx-atlas diff <antiga> <nova> [--format md|json] [--out arquivo] [--fail-on categorias]`. Categorias: `exported-added`, `permission-added`, `component-added`, `deeplink-added`, `finding-added` (medium ou high), `app-flag`. O código de saída é 2 quando alguma aparece, e 0 quando não há diferença (o relatório diz "Nenhuma diferença").
- **Interface:** "⇄ Comparar versões" indexa a outra exportação num segundo espaço do servidor (no máximo dois projetos em memória; importar um projeto novo descarta a comparação). O usuário escolhe se a outra versão é a mais antiga ou a mais nova. O relatório baixa em `.md`.
- **Limitação:** a comparação de filtros olha só as ações (não categorias nem `data`), e a herança só o `extends`.

## D-024 · Relatório exportável e JSON unificado (Fase 7)

- **Um formato JSON só:** `jadx-atlas export --format json` e o botão "Exportar mapa" passam pela mesma função (`atlas/report.py`) e seguem `docs/schema/atlas-index.schema.json`. O JSON traz o payload mais `report` (ferramenta, data UTC, SHA-256 do Manifest, SHA-256 do APK quando `--apk` é usado, estado dos segredos e da anonimização) e os `paths` relevantes. O caminho absoluto da pasta (`root`) **nunca** é exportado.
- **Validação do esquema sem dependência nova:** `tests/schema_check.py` implementa o subconjunto de JSON Schema usado (type, required, properties, items, enum, minimum, maxLength, oneOf, `$ref` local). Alternativa descartada: `jsonschema`, que é uma dependência extra só para testes.
- **Markdown:** rastreabilidade, dados do app, tabela da superfície de ataque, candidatos por severidade com evidência (arquivo:linha e trecho), caminhos possíveis, "Metodologia e limitações" e seções marcadas para o analista (`<!-- PREENCHER -->`: reprodução, impacto, correção). Um *golden file* (`tests/golden/demo-report.md`) congela o formato; `ATLAS_UPDATE_GOLDEN=1` o regenera quando a mudança é intencional.
- **Redação por padrão:** segredos mascarados (o valor completo fica só na memória do processo e entra no relatório apenas com `--include-secrets`); trechos com até 160 caracteres.
- **`--anonymize`:** troca os pacotes e as classes do app por identificadores determinísticos (`pkg01`, `Class0001`, atribuídos pela ordem alfabética, então rodar de novo dá o mesmo resultado), os caminhos de arquivo por `ClassNNNN.java` e os hosts de URLs por `hostN.invalid`. Nomes de framework e bibliotecas externas ficam como estão, e os textos das regras não são alterados. Nomes simples com menos de 4 caracteres só são trocados como parte do nome qualificado, para não corromper outras palavras.
- **SARIF 2.1.0: não implementado.** Seria preciso validar contra o esquema oficial, o que pediria baixar esse esquema e adicionar `jsonschema`. Fica registrado como trabalho futuro.
- **`--apk`:** só calcula o hash (até 2 GiB, recusa links simbólicos). O conteúdo do APK nunca é interpretado.

## D-025 · Código ofuscado e navegação (Fase 8)

- **Formato real dos comentários do JADX** (conferido no 1.5.6 com código deste projeto; amostras em `tests/fixtures/jadx-1.5.6/`): `/* JADX INFO: renamed from: a.a */` (com `--deobf`), `/* JADX INFO: renamed from: p.b, reason: use source file name */` e `/* JADX INFO: compiled from: Helper.java */`. Versões antigas escrevem sem `JADX INFO:`, e as duas formas são aceitas. Os valores viram `originalName` e `sourceFile`, entram na busca e aparecem como alias.
- **R8 de verdade** (`scripts/build_test_apk.py --obfuscate`, R8 8.10.9 do `d8.jar`) revelou três coisas: (1) o R8 troca o atributo SourceFile por `"SourceFile"`, então `compiled from` quase não aparece em apps reais; (2) com a classe externa removida, `Outer$InnerReceiver` vira uma classe de topo **com `$` no nome**, e o Manifest não ligava (corrigido: tenta o nome cru e `originalName`); (3) classes ofuscadas são minúsculas (`new a()`), e o filtro de `uses` as descartava (corrigido: o teste de maiúscula vale só para receptores de chamada estática).
- **Métrica de ofuscação:** fração das classes do projeto com nome simples de até 2 caracteres (depois do último `$`) ou renomeadas pelo JADX. Menos de 10% é `baixa`, menos de 40% é `média`, e acima disso é `alta`. É uma estimativa: nomes curtos legítimos (`R`, `UI`) contam.
- **Bibliotecas:** prefixos em `atlas/data/libraries.json` marcam `node.library`, exceto sob o pacote do app vindo do Manifest (um app `com.google.app` não é escondido pelo prefixo `com.google.`). Na interface, "Ocultar bibliotecas" e os prefixos extras ficam no `localStorage` do navegador (só preferência local).
- **Busca global** (`GET /api/search?q=`): nome, alias, pacote, papel, regra com achado e até 60 strings literais por classe (até 120 bytes cada, sem decodificar escapes). Uma linha por classe, com o campo mais forte; o índice fica no servidor, fora do payload.
- **Agrupamento por pacote:** visão agregada (um nó por pacote, com contagem de classes e de achados ≥ medium); clicar expande o pacote.
- **Anotações:** apelido, etiquetas e nota (até 80 caracteres, 10 etiquetas de 30 e 2.000 caracteres) em `<diretório de dados>/jadx-atlas/annotations/<sha256 do caminho>.json`. O diretório é o XDG no Linux, Application Support no macOS e APPDATA no Windows, ou `ATLAS_DATA_DIR`. A gravação é atômica, e há recusa explícita se o diretório de dados estiver dentro da pasta analisada.
- **Também corrigido:** `data/rules/*.json` não estava no `package-data`, então uma instalação por wheel ficaria sem regras.
