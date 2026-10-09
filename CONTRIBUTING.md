# Contribuindo

Obrigado pelo interesse! Algumas regras do projeto (veja também `docs/DECISIONS.md`):

1. **Honestidade de resolução:** nunca una classes pelo nome curto; todo fato inferido carrega confiança e evidência.
2. **Achados são candidatos:** toda regra nova vem com caso positivo, caso negativo, falsos positivos conhecidos e referências conferidas.
3. **100% local:** nada de chamadas de rede em tempo de execução, telemetria ou CDN. A CSP continua estrita.
4. **Entrada hostil:** código, Manifest e strings do APK não são confiáveis. No navegador, só `textContent`.
5. **Dependências mínimas:** qualquer dependência nova precisa de justificativa em `docs/DECISIONS.md`.
6. **Licenças:** não copie código de projetos GPL. Não inclua APKs nem código decompilado de terceiros.

## Ambiente

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check . && ruff format --check .
npm run check
```

O teste ponta a ponta com APK roda quando `javac`, `jadx` e o Android SDK estão disponíveis. Use `python scripts/build_test_apk.py [--obfuscate]` para gerar o APK de teste.

## Commits

Pequenos, um por tarefa lógica, no formato `tipo(escopo): descrição` (por exemplo `feat(manifest): parse exported components`). Atualize o `CHANGELOG.md` na seção "Não lançado".
