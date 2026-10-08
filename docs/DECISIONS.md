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
