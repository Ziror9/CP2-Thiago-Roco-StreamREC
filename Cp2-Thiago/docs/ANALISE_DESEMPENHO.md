# Testes de desempenho e análise dos resultados

> **Nota:** este arquivo registra a primeira rodada de testes. A análise completa e atualizada (EDA, avaliação offline do modelo, simulação de TTL e estatística da última rodada de carga) está em **Documentacao_StreamRec.docx** e em `results/analise_dados.md`, gerado por `scripts/analise_dados.py`. Os números absolutos variam entre execuções conforme a carga da máquina; as conclusões relativas se mantêm.

## 1. Objetivo

Medir o impacto do cache Redis na API de recomendação quando **muitos usuários acessam ao mesmo tempo**, avaliando:
- **latência** (média, p50, p95 e p99);
- **vazão** (requisições por segundo);
- **carga no banco** (número de consultas enviadas ao Neo4j);
- **eficácia do cache** (hit ratio).

## 2. Ambiente

| Item | Valor |
|---|---|
| Máquina | Windows 11, 8 vCPU, 8 GB de RAM, Docker Desktop (WSL2) |
| Neo4j | 5.26 Community, heap de 1 GB, page cache de 512 MB |
| Redis | 7 (alpine), maxmemory de 256 MB, política `allkeys-lru` |
| API | FastAPI + Uvicorn, 4 workers, Python 3.12 |
| Dados (seed) | 200 usuários, 300 filmes, 12 gêneros, 5.736 `WATCHED`, 4.003 `RATED` |
| Endpoint testado | `GET /users/{id}/recommendations?limit=10` |

> Todos os componentes, inclusive o gerador de carga, rodaram **na mesma máquina** e disputaram CPU. Os números absolutos são menores do que em servidores dedicados; o que importa é a **comparação relativa** entre os cenários.

## 3. Metodologia

Três ferramentas complementares:

1. **`scripts/benchmark.py`** (Python, httpx assíncrono): 2.000 requisições por cenário, 50 simultâneas, distribuídas aleatoriamente entre os 200 usuários. A sequência de usuários é a mesma em todos os cenários (semente fixa). Antes de cada cenário, as métricas do Redis são zeradas.
   - **sem_cache:** `use_cache=false`, ou seja, toda requisição consulta o Neo4j (situação sem Redis).
   - **cache_frio:** cache esvaziado antes; a 1ª requisição de cada usuário é MISS e as seguintes são HIT.
   - **cache_quente:** cache já populado; 100% HIT.
2. **`scripts/ab_compare.ps1`** (ApacheBench, escrito em C): 2.000 requisições, 50 simultâneas, com e sem cache. Por ser mais leve que um cliente Python, mede melhor o **teto real da API**.
3. **Requisições individuais** (sem concorrência), para ver o custo isolado de cada caminho.

Os resultados brutos ficam em [`results/`](../results).

## 4. Resultados

### 4.1 Requisições individuais (sem concorrência)

| Caminho | Tempo no servidor |
|---|---:|
| MISS, 1ª consulta após subir o Neo4j (compilação do plano e cache frio) | ~2.800 ms |
| MISS, Neo4j aquecido | ~65 ms |
| **HIT** | **~3,5 ms** |

Um HIT é **~18x mais rápido** que o MISS aquecido e **~800x** mais rápido que o MISS frio.

### 4.2 ApacheBench: 2.000 requisições, concorrência 50 ([results/ab_latest.txt](../results/ab_latest.txt))

| Métrica | Sem cache | Com cache (HIT) | Ganho |
|---|---:|---:|---:|
| Vazão (req/s) | 59,0 | **416,3** | **7,1x** |
| Latência média (ms) | 848 | **120** | **-86%** |
| p50 (ms) | 812 | 108 | -87% |
| p95 (ms) | 1.464 | 247 | -83% |
| p99 (ms) | 1.814 | 296 | -84% |
| Máximo (ms) | 2.581 | 548 | -79% |
| Falhas | 0 | 0 | |

### 4.3 Benchmark Python: 3 cenários, concorrência 50 ([results/benchmark_latest.md](../results/benchmark_latest.md))

| Cenário | Vazão (req/s) | Média (ms) | p50 (ms) | p95 (ms) | p99 (ms) | HIT | MISS | Hit ratio | Consultas Neo4j |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sem_cache | 32,0 | 1.523 | 1.105 | 4.292 | 6.241 | 0 | 0 | 0% | **2.000** |
| cache_frio | 62,7 | 774 | 486 | 2.440 | 4.196 | 1.794 | 206 | 90% | **206** |
| cache_quente | 90,3 | 537 | 367 | 1.593 | 2.460 | 2.000 | 0 | 100% | **0** |

- cache_frio vs sem_cache: vazão **2,0x**, latência média **-49%**, consultas ao Neo4j **-90%**
- cache_quente vs sem_cache: vazão **2,8x**, latência média **-65%**, consultas ao Neo4j **-100%**

## 5. Análise

### 5.1 O cache reduz drasticamente as consultas ao banco
Esse era o objetivo principal da empresa. Com 2.000 requisições:
- sem cache: **2.000** consultas ao Neo4j (1 por requisição);
- cache frio: **206**, ou seja, cerca de 1 por usuário distinto. As outras 1.794 vieram do Redis;
- cache quente: **0**.

O número de consultas ao banco deixa de acompanhar o **número de acessos** e passa a acompanhar o **número de usuários distintos por janela de TTL**. Com o TTL de 5 min, um usuário que abre o app 20 vezes nesse intervalo gera 1 consulta ao Neo4j, e não 20.

### 5.2 Latência e vazão sob concorrência
- **Sem cache, o gargalo é o Neo4j.** Durante o teste, o container do Neo4j chegou a **~530% de CPU** (mais de 5 núcleos), enquanto a API e o Redis ficaram quase ociosos. Cada recomendação percorre o grafo (vizinhos, filmes dos vizinhos, gêneros, notas médias); com 50 requisições simultâneas, elas entram em fila e a latência vai de ~65 ms (isolada) para ~850 ms em média e ~1,8 s no p99.
- **Com cache, o custo vira uma leitura O(1) no Redis** (Redis a ~6% de CPU). A vazão sobe **7x** (ab) e a latência cai **86%**.
- **As caudas (p95/p99) melhoram tanto quanto a média.** Isso importa para a experiência: são os usuários mais lentos que abandonam o app.

### 5.3 Por que o benchmark Python mostra ganhos menores que o ab
No cenário cache_quente, o próprio **gerador de carga em Python virou o gargalo**: um único processo com ~110–130% de CPU e ~90 req/s. O ApacheBench, com a mesma API e a mesma carga, chegou a **416 req/s**. Portanto:
- o benchmark Python é ótimo para **contar HIT/MISS e consultas ao Neo4j** por cenário;
- o ab mede melhor a **capacidade máxima** da API.

Esse é um cuidado importante em testes de carga: sempre verificar se quem limita o teste é o sistema testado ou o gerador de carga.

### 5.4 Cache frio e *cache stampede*
No cenário cache_frio houve **206 MISS para 200 usuários**. As 6 consultas extras vieram de requisições **simultâneas** do mesmo usuário que chegaram antes de a primeira gravar no Redis. Todas calcularam a mesma recomendação (*cache stampede*). Com poucos usuários é irrelevante, mas em uma virada de TTL em horário de pico pode sobrecarregar o banco. A mitigação está descrita em [ARQUITETURA.md §6](ARQUITETURA.md#6-limitações-e-evolução): lock `SET NX` / single-flight e TTL com variação aleatória (*jitter*).

### 5.5 Trade-off: consistência x desempenho
- A **invalidação explícita** garante que quem avalia ou assiste um filme recebe na próxima chamada uma recomendação recalculada (demonstrado em `scripts/demo_flow.py`: MISS → HIT → avaliação → MISS).
- **Efeitos indiretos** (a nota de um vizinho mudar a recomendação de outro usuário) aparecem em até 1 TTL (5 min). Para recomendação de filmes, esse atraso é aceitável e evita invalidações em cascata.
- **TTL maior** aumenta o hit ratio e reduz a carga no banco, mas as recomendações ficam mais antigas. **TTL menor** faz o contrário. 5 min é um ponto de partida; o ideal é ajustar com o hit ratio real de produção (`GET /cache/stats`).

## 6. Conclusões

1. O Redis cumpriu o objetivo de **reduzir as consultas ao Neo4j** entre **90% e 100%**, conforme o aquecimento do cache.
2. Sob 50 acessos simultâneos, o cache deu **7x mais vazão** e **86% menos latência média** (ab), sem nenhuma falha.
3. O Neo4j fica livre para o que faz melhor: **análise de relacionamentos** nos MISS e **gravação das interações**.
4. A arquitetura **cache-aside + TTL + invalidação por evento** é simples, stateless e escala horizontalmente. É próxima do que se usa em aplicações reais.
5. Próximos passos: proteção contra *stampede*, pré-cálculo das recomendações em lote para os usuários mais ativos, réplicas de leitura do Neo4j e monitoramento do hit ratio (Prometheus/Grafana).

## 7. Como reproduzir

```bash
docker compose up -d --build
docker compose exec api python -m scripts.seed --reset
# Benchmark Python (PowerShell; no Linux/macOS use $(pwd))
docker compose run --rm --no-deps -v "${PWD}/results:/code/results" api python -m scripts.benchmark --base-url http://api:8000 --requests 2000 --concurrency 50
# ApacheBench
powershell -ExecutionPolicy Bypass -File .\scripts\ab_compare.ps1 -N 2000 -C 50
```
