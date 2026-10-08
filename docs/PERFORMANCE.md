# Desempenho

Máquina do autor, Python 3.14.7, um processo. Medições pontuais (não há benchmark automatizado ainda; ele entra na Fase 9).

| Conjunto | Tipos | Sem regras | Com regras (Fase 3) |
|---|---|---|---|
| Sintético `gen_large_project.py --classes 20000 --depth 8 --obfuscated 0.6` (corpos vazios) | 20.000 | 2,1 s | 2,4 s (inclui papéis) |
| AndroidX + support + Firebase decompilados (JADX 1.5.6) | 2.032 | 1,0 s | 1,44 a 1,56 s |
| Caso extremo do teste `test_collection_cost_is_bounded` (900 chamadas relevantes por arquivo × 40) | 40 | ~0,26 s | < 3× |

Cálculo de papéis (Fase 2) em 20 mil classes: 0,05 s.
