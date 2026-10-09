# JADX Atlas: Pedidos · demonstração

> Candidatos para revisão manual; não são veredictos. Gerado por jadx-atlas 0.1.0 em 2026-01-01T00:00:00+00:00 (schemaVersion 6).

## Rastreabilidade

| Item | Valor |
|---|---|
| SHA-256 do AndroidManifest analisado | `7ff02e1f143331243072230a8fbaabadf7097ff0ad87c240e0e0c6e15e90354d` |
| SHA-256 do APK | `não informado (--apk)` |
| Segredos | mascarados |
| Anonimizado | não |

## Aplicativo

| Campo | Valor |
|---|---|
| Pacote | `br.exemplo.pedidos` |
| Versão | `2.1.0` |
| versionCode | `7` |
| minSdk | `24` |
| targetSdk | `30` |
| Atributos de `<application>` | `allowBackup`=True, `usesCleartextTraffic`=True |
| Permissões usadas | `android.permission.INTERNET` |

Índice: 14 tipos, 15 relações de herança, 13 arquivos.

## Superfície de ataque

| Componente | Tipo | Exportado | Motivo | Permissão | Deep links |
|---|---|---|---|---|---|
| `br.exemplo.pedidos.ui.CheckoutActivity` | activity | True | implícito por intent-filter (targetSdk 30 < 31) | nenhuma | `pedidos://checkout` |
| `br.exemplo.pedidos.ui.PedidoActivity` | activity | True | explícito | nenhuma | — |
| `br.exemplo.pedidos.ui.HistoricoActivity` | activity | False | explícito | nenhuma | — |

## Candidatos a achado

### Severidade medium (1)

- **JavaScript habilitado em WebView** (`webview-javascript-enabled`, confiança medium): `br.exemplo.pedidos.ui.CheckoutActivity` em `br/exemplo/pedidos/ui/CheckoutActivity.java:13`
  - Trecho: `webView.getSettings().setJavaScriptEnabled(true);`

### Severidade low (2)

- **Hash fraco (MD5, SHA-1)** (`crypto-weak-hash`, confiança high): `br.exemplo.pedidos.data.PedidoRepository` em `br/exemplo/pedidos/data/PedidoRepository.java:13`
  - Trecho: `MessageDigest digest = MessageDigest.getInstance("MD5");`
- **loadUrl com argumento não literal** (`webview-loadurl-dynamic`, confiança high): `br.exemplo.pedidos.ui.CheckoutActivity` em `br/exemplo/pedidos/ui/CheckoutActivity.java:14`
  - Trecho: `webView.loadUrl(url);`

### Severidade info (3)

- **URL http:// literal** (`cleartext-http-url`, confiança medium): `br.exemplo.pedidos.data.PedidoRepository` em `br/exemplo/pedidos/data/PedidoRepository.java:8`
  - Trecho: `private static final String API = "http://api.pedidos.example/v1/pedidos";`
- **Leitura de dados do Intent em componente exportado** (`untrusted-intent-input`, confiança medium): `br.exemplo.pedidos.ui.CheckoutActivity` em `br/exemplo/pedidos/ui/CheckoutActivity.java:12`
  - Trecho: `String url = getIntent().getStringExtra("url");`
- **sendBroadcast sem permissão** (`broadcast-without-permission`, confiança high): `br.exemplo.pedidos.ui.PedidoActivity` em `br/exemplo/pedidos/ui/PedidoActivity.java:25`
  - Trecho: `sendBroadcast(new Intent(ACAO_ATUALIZAR));`

## Caminhos possíveis

Rotas no grafo de Intents, referências de tipo e herança; não provam alcançabilidade nem exploração.

- `br.exemplo.pedidos.ui.PedidoActivity` (activity exportado) → `br.exemplo.pedidos.ui.CheckoutActivity` (confiança high):
  - `br.exemplo.pedidos.ui.PedidoActivity` --launches (startActivity)--> `br.exemplo.pedidos.ui.CheckoutActivity` [`br/exemplo/pedidos/ui/PedidoActivity.java:24`]

## Metodologia e limitações

- **Índice:** declarações Java extraídas com Tree-sitter da exportação do JADX; herança resolvida por escopo léxico, imports, pacote e curingas. Nomes ambíguos ou desconhecidos ficam marcados como tal, nunca unidos por nome curto.
- **Superfície de ataque:** AndroidManifest decodificado (lido como entrada hostil). A exportação efetiva segue as regras do AOSP, e valores que dependem de recursos aparecem como desconhecidos, com um palpite explicado.
- **Papéis:** cadeia de ancestrais (projeto + tabela de framework gerada por `javap`), com o caminho e a menor confiança encontrada nele.
- **Candidatos a achado:** regras de dados sobre chamadas, criações, overrides e strings. Confiança `high` quando o tipo declarado do receptor resolve para a API, `medium` quando só método e import batem, `low` quando só o nome do método bate.
- **Intents e caminhos:** fluxo apenas dentro do método; caminhos são rotas *possíveis* no grafo de Intents, referências de tipo e herança.

**Limitações:** não há análise de fluxo de dados nem interprocedural; não há execução do app; código nativo, Kotlin original, Smali e recursos além do Manifest não são analisados; reflexão e carregamento dinâmico escondem fluxos reais. Todo item deste relatório é um **candidato** que precisa de confirmação manual.

## Para o analista

### Reprodução

<!-- PREENCHER: passos para reproduzir, ambiente, versão testada -->

### Impacto

<!-- PREENCHER: o que um atacante consegue, pré-condições, severidade avaliada -->

### Correção sugerida

<!-- PREENCHER -->
