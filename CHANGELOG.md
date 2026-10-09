# Changelog

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versionamento: [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado
- Pacote instalável (`pyproject.toml`) com o comando `jadx-atlas` (`serve`, `diff`, `export`), CI, ruff, licença MIT e política de segurança.
- AndroidManifest como superfície de ataque: exportação efetiva com motivo (regras do AOSP), valores desconhecidos com palpite, permissões, providers e deep links; painel Superfície e Alertas do app.
- Papéis por herança (Activity, Service, TrustManager, WebViewClient...) com caminho e confiança, a partir de uma tabela de framework gerada por `javap`.
- 30 regras de candidatos a achado (WebView, TLS, criptografia, execução, armazenamento/IPC, segredos), com confiança `high`, `medium` ou `low`; segredos sempre mascarados.
- Arestas de Intent (`launches`, `sends_action`, `registers_receiver`) e camadas no mapa.
- Caminhos possíveis da entrada exposta até a classe com achado (`/api/paths`).
- Comparação entre versões (`jadx-atlas diff`, `--fail-on` para CI) e interface "Comparar versões".
- Relatório Markdown e JSON com esquema documentado (`jadx-atlas export`, `--anonymize`, `--include-secrets`, `--apk`).
- Código ofuscado: aliases do JADX, métrica de ofuscação, busca global, bibliotecas ocultáveis, agrupamento por pacote, anotações fora da pasta analisada.
- Indexação paralela, cache em disco (JSON) e rotas por demanda (`/api/summary`, `/api/list`, `/api/neighbors`).
- Token de sessão em `/api/`, `--verbose` com logs estruturados, CSP sem `unsafe-inline`, lógica do front-end em módulo ES com testes `node --test`.

### Corrigido
- Mapa quebrava com tipos de aresta desconhecidos.
- Lista `java.lang` incompleta; código de classes anônimas agora é analisado.
- "Exportar mapa" incluía o caminho absoluto da pasta.
