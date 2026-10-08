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
- **Exportar mapa**: salva todo o índice em JSON, incluindo relações, nomes, caminhos e avisos; não inclui o conteúdo completo dos arquivos.

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
