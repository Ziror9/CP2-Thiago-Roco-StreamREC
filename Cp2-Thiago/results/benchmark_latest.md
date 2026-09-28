# Resultado do benchmark (2026-09-28 16:49:35)

- Requisições por cenário: **2000** | Concorrência: **50** | Usuários distintos: **200** | limit=10

| Cenário | Throughput (req/s) | Média (ms) | p50 (ms) | p95 (ms) | p99 (ms) | HIT | MISS | Hit ratio | Consultas Neo4j |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sem_cache | 22.3 | 2187.18 | 1612.79 | 5823.3 | 8369.14 | 0 | 0 | 0% | 2000 |
| cache_frio | 49.0 | 988.23 | 598.61 | 3247.37 | 5579.53 | 1794 | 206 | 90% | 206 |
| cache_quente | 77.5 | 623.3 | 416.72 | 1834.67 | 2985.17 | 2000 | 0 | 100% | 0 |

- **cache_frio** vs sem_cache: throughput **2.2x**, latência média **-55%**, consultas ao Neo4j **-90%**
- **cache_quente** vs sem_cache: throughput **3.5x**, latência média **-72%**, consultas ao Neo4j **-100%**
