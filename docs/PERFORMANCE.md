# Desempenho

Máquina do autor: 16 núcleos lógicos, Python 3.14.7. Os números vêm de `scripts/bench.py`, que roda cada tamanho num processo novo, para que o pico de memória (`ru_maxrss`) seja daquela medição. Os sintéticos vêm de `gen_large_project.py --depth 8 --obfuscated 0.5 --seed 11`.

**Atenção:** os projetos sintéticos têm classes de corpo vazio. Em código real (AndroidX decompilado), o custo por classe é cerca de 40 vezes maior. Por isso há as duas medições.

## Linha de base (antes da Fase 9: um processo, sem cache)

| Conjunto | Tipos | Indexação | Pico de memória | Payload | `/api/project` | `/api/search` |
|---|---|---|---|---|---|---|
| Sintético | 5.000 | 0,77 s | 62 MB | 2,4 MB | 29 ms | 7 ms |
| Sintético | 20.000 | 3,15 s | 162 MB | 9,5 MB | 112 ms | 26 ms |
| Sintético | 50.000 | 8,45 s | 371 MB | 23,9 MB | 261 ms | 65 ms |
| AndroidX + support + Firebase (JADX 1.5.6) | 2.032 | 1,77 s | 50 MB | 1,3 MB | 16 ms | 5 ms |

## Depois da Fase 9

| Conjunto | Serial | Paralelo (8 processos) | Cache: 1ª execução (grava) | Cache: 2ª execução (acerto) |
|---|---|---|---|---|
| Sintético 50.000 | 8,7 s | **5,1 s** | 7,4 s | 7,3 s |
| AndroidX 2.032 | 1,79 s | **1,03 s** | — | **0,41 s** |

- **Paralelismo:** ganho de 1,7x em código real e no sintético grande. Abaixo de 300 arquivos o padrão continua serial, porque iniciar processos custa mais do que economiza.
- **Cache:** compensa em código real (parse caro), com 0,41 s contra 1,03 s. No sintético, ler o cache (JSON comprimido) custa tanto quanto reparsear arquivos triviais. O formato JSON foi escolhido em vez de `pickle` de propósito: um arquivo de cache nunca pode executar código (D-026).
- **Bug encontrado pela medição:** a primeira versão do cache verificava `relative not in fresh` numa *lista*, o que é quadrático: 50 mil arquivos levavam 40 s. Corrigido com um conjunto.
- **Estimativa para um APK real com 20 mil classes:** ~17 s serial e ~9 s paralelo pela proporção do AndroidX, dentro da meta sugerida (menos de 30 s). Ainda não foi medido num APK real desse tamanho.

## Interface

- A lista carrega 200 itens por vez ("mostrar mais"), o mapa desenha no máximo 600 nós (com aviso), a busca tem debounce de 250 ms e grafos grandes usam grade sem animação.
- Payloads acima de `ATLAS_MAX_PAYLOAD_MB` (padrão 150) não são enviados inteiros: `/api/project` responde `tooLarge` com um resumo, e a interface mostra o aviso. **Limitação:** a interface ainda não tem um modo completo baseado em `/api/list` e `/api/neighbors`. Essas rotas existem e estão testadas, mas o front-end atual carrega o payload inteiro abaixo do limite. 50 mil classes sintéticas dão 24 MB, abaixo do limite.

## Histórico

| Fase | Conjunto | Sem regras | Com regras/Intents/uses |
|---|---|---|---|
| 3 | AndroidX 2.032 | 1,0 s | 1,44 a 1,56 s |
| 5 | AndroidX 2.032 | 0,95 s | 1,7 s |
| 2 | Cálculo de papéis em 20 mil classes | — | 0,05 s |
