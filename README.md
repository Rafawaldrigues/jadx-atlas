# JADX Atlas

Explorador **local** de herança e interfaces para código Java exportado pelo JADX. Busca classes e pacotes, desenha relações `extends` / `implements` e abre o arquivo na linha da declaração. A interface está em português.

> **Uso responsável:** analise apenas aplicativos que você tem autorização para analisar. Veja [SECURITY.md](SECURITY.md).

## Instalar e iniciar

Requer Python 3.10 ou superior.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
jadx-atlas
```

O navegador abre em **http://127.0.0.1:8765** com uma demonstração fictícia de pedidos. As bibliotecas do mapa já estão incluídas em `atlas/web/vendor`; depois da instalação, o aplicativo funciona sem internet. O atalho `./iniciar.sh` faz os três passos acima.

Também é possível iniciar com uma pasta específica:

```bash
jadx-atlas "/caminho/do/aplicativo/sources"
```

Use `--no-browser` para não abrir o navegador e `--port 8766` para trocar a porta (`--port 0` escolhe uma livre). `jadx-atlas serve ...` é a forma explícita do mesmo comando. Encerre com `Ctrl+C` no terminal.

## Importar um aplicativo

1. No JADX, exporte os arquivos com **Salvar tudo**, ou execute `jadx -d saida aplicativo.apk`.
2. No Atlas, clique em **Abrir projeto**.
3. Informe o caminho da pasta `sources` exportada, ou de uma pasta que a contenha, e clique em **Analisar pasta**.

O Atlas percorre as subpastas e mostra o progresso. Uma importação com erro ou cancelada mantém o projeto anterior. O APK não é aberto diretamente por esta versão.

## Superfície de ataque (AndroidManifest)

Quando a exportação do JADX inclui recursos (sem `--no-res`), o Atlas lê `resources/AndroidManifest.xml` (ou `--manifest <arquivo>`) e liga cada componente à sua classe no grafo:

- **Exportação efetiva**, sempre com o motivo: explícita, implícita por intent-filter (targetSdk < 31), padrão antigo de provider (targetSdk < 17) e assim por diante, seguindo as regras do AOSP.
- **Valores que não dá para saber com certeza** ficam como *desconhecido* (`@bool/...`) ou *inconsistente*, mas recebem um **palpite** com a origem (por exemplo `@bool/x = true em res/values/bools.xml`). Na interface aparecem como "potencialmente exportado" e `EXP?`.
- Permissões (inclusive a herdada de `<application>`), `protectionLevel` das permissões do próprio app, providers avaliados pelo lado mais fraco (leitura ou escrita) e deep links (`VIEW` + `BROWSABLE` + `scheme`).
- Painel **Superfície** com os componentes ordenados por exposição e **Alertas do app** (`debuggable`, `allowBackup`, `usesCleartextTraffic`, `testOnly`). Filtros "Exportado", "Deep link", "Sem permissão" e "Tipo". Nós exportados têm borda dupla e o marcador `⇥`.

Tudo isso é **candidato a revisão**, não veredicto. O Manifest é lido como entrada hostil: DTD e entidades são recusados, e há limites de tamanho e profundidade. O Manifest binário (AXML) não é decodificado. Validação em [docs/VALIDATION.md](docs/VALIDATION.md).

## Papéis por herança

Cada classe recebe papéis (Activity, Service, Receiver, Provider, Application, Fragment, TrustManager, HostnameVerifier, WebViewClient, WebChromeClient, SSLSocketFactory, AsyncTask, Parcelable, Serializable) pela **cadeia de ancestrais**, inclusive quando os nomes são ofuscados (`a.b.c (Activity)`). Depois do código do app, a cadeia continua por uma tabela de tipos do Android, do AndroidX e da support library, gerada com `javap` a partir dos artefatos oficiais (`atlas/data/framework_hierarchy.json`, com a versão de cada tipo). Cada papel mostra o caminho percorrido e a confiança: uma referência ambígua no meio do caminho a rebaixa. O papel é conferido com o Manifest: se um componente declarado não tem o ancestral esperado, aparece um aviso de "possível erro de resolução".

## Candidatos a achado (APIs sensíveis)

30 regras em `atlas/data/rules/*.json` procuram usos que **costumam** indicar problema: WebView (JavaScript, ponte nativa, acesso a arquivos, depuração, `loadUrl` dinâmico), TLS (TrustManager vazio, HostnameVerifier que aceita tudo, `onReceivedSslError` com `proceed()`, protocolos antigos), criptografia (ECB, DES/RC4, MD5/SHA-1, chave/IV/semente literais), execução (`Runtime.exec`, `ProcessBuilder`, `DexClassLoader`, reflexão), armazenamento e IPC (`MODE_WORLD_*`, SQL concatenado, `PendingIntent` mutável, broadcast sem permissão, leitura de Intent em componente exportado) e segredos (formatos conhecidos, alta entropia, URLs `http://`). Cada regra documenta falsos positivos conhecidos e referências oficiais.

A **confiança** não depende de um sistema de tipos:

- **high**: o tipo declarado do receptor resolve, pelos imports do arquivo, para a classe da API, e o método bate;
- **medium**: o método bate e o arquivo importa a API, mas o tipo do receptor não foi determinado (getter encadeado, campo herdado);
- **low**: só o nome do método bate, ou um argumento não pôde ser avaliado (variável, concatenação).

Receptores de outro tipo são descartados. O código de classes anônimas é atribuído à classe que o contém. Segredos aparecem sempre mascarados (`AKIA…[20 caracteres]`). Clicar num achado abre o código na linha, com a explicação da regra. **Não há análise de fluxo de dados**: são pistas para revisão manual.

## Navegação por Intents

O Atlas liga telas e componentes pelas chamadas de Intent: `startActivity`, `startService`, `bindService`, `sendBroadcast`, `PendingIntent.get*` e `registerReceiver`, com alvos por `X.class`, `setClassName`, `setComponent` ou ação implícita casada com os `intent-filter` do Manifest. O fluxo é **só dentro do método**, sem análise entre métodos. Intents que não dá para rastrear (ação dinâmica, Intent vindo de outro método) aparecem como "não resolvidos", com o motivo, e nunca viram uma aresta inventada. No mapa, escolha a camada **Herança**, **Intents** ou **Todas**.

## Caminhos possíveis

A aba **Caminhos** procura, a partir de uma entrada exposta (componente exportado, deep link, Application), rotas até classes com candidato a achado, seguindo arestas de Intent, referências de tipo (`uses`: criação de objeto, chamada estática, tipo de variável) e, opcionalmente, herança. São os caminhos mais curtos primeiro, com limites de profundidade, quantidade e tempo, ordem determinística e evidência (arquivo:linha) em cada passo. **É um caminho possível, não prova de alcançabilidade nem de exploração.** Também existe em `GET /api/paths?entry=<classe>&target=<classe>&maxDepth=6`.

## Comparar versões

```bash
jadx-atlas diff app-v1/sources app-v2/sources --format md --out diff.md --fail-on exported-added,permission-added
```

Mostra o que mudou na superfície de ataque: componentes novos ou removidos, `exported` que passou de `false` para `true`, permissões, deep links, flags de `<application>`, candidatos a achado novos e removidos (a identidade não depende da linha) e mudanças na herança dos componentes. Classes ofuscadas renomeadas são pareadas só como **possível correspondência**. Com `--fail-on`, o código de saída é 2 quando a mudança aparece, o que serve para CI. Na interface, use **⇄ Comparar versões**.

## Relatório

```bash
jadx-atlas export app/sources --format md --out relatorio.md --apk app.apk
```

Gera um esqueleto de writeup: rastreabilidade (versão da ferramenta, data, SHA-256 do Manifest e, com `--apk`, do APK), dados do app, superfície de ataque, candidatos por severidade com evidência, caminhos possíveis, metodologia e limitações, e seções para o analista preencher. `--format json` produz o índice completo no formato de [docs/schema/atlas-index.schema.json](docs/schema/atlas-index.schema.json). Segredos ficam mascarados (use `--include-secrets` com cuidado), e `--anonymize` troca pacote, classes e hosts por identificadores estáveis para compartilhar sem expor o alvo. O caminho absoluto da pasta nunca é exportado.

## Código ofuscado

O rodapé estima a ofuscação (fração de classes com nomes de até 2 caracteres). Quando a exportação foi feita com `jadx --deobf`, os comentários `renamed from` e `compiled from` viram aliases pesquisáveis. A busca global (3+ caracteres) procura também em papéis, regras e strings literais (inclusive URLs). "Ocultar bibliotecas" esconde prefixos conhecidos (`atlas/data/libraries.json`) e os que você informar, mas nunca o pacote do app. "Agrupar pacotes" reduz o mapa a um nó por pacote. Apelidos, etiquetas e notas ficam no seu diretório de dados (por exemplo `~/.local/share/jadx-atlas`), nunca dentro da pasta analisada.

## Explorar

- **Busca**: localiza pelo nome da classe, nome completo, pacote ou caminho do arquivo. Atalho `/`.
- **Explorador**: agrupa declarações em pacotes recolhíveis e permite filtrar classes/interfaces. As relações também estão disponíveis como botões para navegação pelo teclado.
- **Visão geral**: mapa do projeto, com filtro de pacote; os tipos pais fora do pacote permanecem visíveis como contexto.
- **Foco na classe**: mostra de 1 a 5 níveis de ancestrais, descendentes ou conexões em ambos os sentidos. Um duplo clique no nó ativa o foco.
- **Herança completa**: remove o limite de profundidade e reúne toda a cadeia conhecida de classes e interfaces da seleção. O inspetor separa relações diretas das herdadas e permite ir à classe raiz conhecida.
- **Navegação no mapa**: arraste o fundo para mover, use a roda do mouse ou a régua para zoom, as setas do controle para deslocar e o alvo para centralizar a classe selecionada. `+`/`-` alteram o zoom, `0` ajusta tudo, as setas movem, `C` centraliza, `H` abre a herança completa e `R` vai à raiz.
- **Filtros**: ligue/desligue `extends`, `implements` e referências externas.
- **Inspecionar classe**: mostra declaração, pais, interfaces e quem estende/implementa diretamente. Clique nas relações para navegar; a seta de voltar recupera a seleção anterior.
- **Código**: clique no nome do arquivo no painel para abrir o conteúdo completo, com números de linha e a declaração destacada. O código é somente para leitura.
- **Exportar mapa / relatório**: salva o índice em JSON (formato documentado) ou o relatório em Markdown, opcionalmente anonimizados; não inclui o conteúdo completo dos arquivos nem o caminho absoluto da pasta.

As setas saem da classe/interface filha e apontam para o tipo pai. Relações `implements` são tracejadas e roxas; relações `extends` são contínuas. Tipos externos têm borda tracejada; referências não resolvidas ficam em amarelo. Clicar em um tipo externo explica por que o código está indisponível.

## Projetos grandes

O índice inclui todas as declarações recuperáveis. A lista carrega 200 resultados por vez. O mapa desenha **até 600 nós por visualização** para manter a navegação utilizável; quando esse limite é atingido, o rodapé informa o total omitido. Use busca, filtro de pacote e foco para acessar as demais classes. A exportação JSON contém o índice completo, independentemente do recorte visível. Recortes grandes (mais de 120 nós ou 350 relações) usam uma grade compacta; recortes menores usam disposição hierárquica. O foco começa com um nível e permite expandir.

Arquivos maiores que 8 MiB são ignorados com aviso. Links simbólicos, `.git`, `node_modules` e `.venv` são ignorados na busca. Os arquivos Java são lidos como UTF-8, substituindo bytes inválidos. A pasta original não é modificada. Se um arquivo mudar depois da análise, é necessário reimportar antes de abrir seu código.

## Precisão e limites deste escopo

- Usa a árvore sintática do **Tree-sitter Java**, em vez de procurar herança por expressões regulares.
- Indexa classes, interfaces, enums, records, anotações e tipos membros nomeados, incluindo nomes ofuscados.
- Resolve referências por escopo lexical, imports explícitos, pacote, nomes qualificados e imports com `*` quando os tipos estão disponíveis. Reconhece um conjunto de tipos comuns de `java.lang`.
- Imports para tipos ausentes geram nós externos. Nomes ambíguos ou desconhecidos geram referências próprias identificadas como não resolvidas; a ferramenta não junta classes apenas porque têm o mesmo nome curto.
- Java inválido é analisado parcialmente, com aviso. Declarações duplicadas mantêm a primeira ocorrência e geram aviso.
- Exibe **relações explicitamente declaradas**. Não adiciona automaticamente `Object`, `Enum`, `Record` ou interfaces implícitas.
- Não substitui a resolução completa de um compilador: tipos membros herdados, classpaths externos e alguns casos complexos de imports/escopo podem permanecer não resolvidos. Não verifica se o código compila.
- Classes locais dentro de métodos e classes anônimas não são indexadas. Se o JADX as exportar como classes nomeadas separadas, elas podem ser indexadas normalmente.
- Não analisa Kotlin original, Smali, código nativo, chamadas entre métodos, fluxo de dados ou comportamento em execução.
- O código é aberto no próprio Atlas. Esta versão não controla uma instância do JADX nem modifica o código exportado.

## Desenvolvimento e verificação

```bash
pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check . && ruff format --check .
npm run check
```

O teste de integração com o JADX roda quando `javac`, `jar` e `jadx` estão no `PATH`; caso contrário, é pulado. Para testes de escala, `python scripts/gen_large_project.py --classes 20000 --depth 8 --obfuscated 0.6` gera um projeto sintético em `artifacts/` (pasta ignorada pelo Git).

O servidor usa a biblioteca padrão do Python e só escuta em `127.0.0.1`. Os arquivos do projeto são lidos pelo processo local; não há analytics, CDN ou serviço remoto. Origem e Host são validados nas rotas. Todas as abas abertas compartilham o mesmo projeto em memória. O índice não persiste entre reinicializações; inicie novamente passando a pasta para reabrir o projeto.

As dependências do navegador são distribuídas junto com suas licenças. Para atualizá-las/regerá-las, instale Node.js e execute:

```bash
npm ci
npm run vendor
```

Estrutura: `atlas/indexer.py` (análise), `atlas/server.py` (servidor local), `atlas/__main__.py` (linha de comando), `atlas/web/` (interface), `atlas/examples/` (demonstração), `scripts/` (vendor e gerador sintético), `tests/` (testes), `docs/` (decisões, validação e desempenho).

Licença: [MIT](LICENSE). As bibliotecas em `atlas/web/vendor/` mantêm suas próprias licenças (MIT). Este projeto não é afiliado ao JADX.

Referências: [JADX](https://github.com/skylot/jadx), [Tree-sitter Python](https://github.com/tree-sitter/py-tree-sitter), [Cytoscape.js](https://js.cytoscape.org/) e [Cytoscape Dagre](https://github.com/cytoscape/cytoscape.js-dagre).
