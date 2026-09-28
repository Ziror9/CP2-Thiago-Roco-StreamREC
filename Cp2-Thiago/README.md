# StreamRec: API de Recomendação Personalizada

Primeira versão de uma plataforma de recomendação de filmes para uma empresa de streaming.

| Tecnologia | Papel |
|---|---|
| **Neo4j 5.26** | Armazena e analisa o grafo de relacionamentos Usuário ↔ Filme ↔ Gênero |
| **Redis 7** | Cache das recomendações (TTL, HIT/MISS, invalidação, métricas) |
| **Python 3.12** | Linguagem da aplicação (driver assíncrono do Neo4j e `redis.asyncio`) |
| **FastAPI + Uvicorn** | API REST (4 workers, assíncrona, com documentação Swagger automática) |
| **Front-end (HTML/CSS/JS + nginx)** | Interface web estilo streaming em http://localhost:3000, consumindo a API via proxy `/api` |
| **Docker Compose** | Sobe todo o ambiente com um único comando |

## Requisitos atendidos

| # | Requisito | Onde |
|---|---|---|
| 1 | Cadastrar usuários | `POST /users` |
| 2 | Cadastrar filmes | `POST /movies` |
| 3 | Registrar gêneros | `POST /genres`, `POST /movies/{id}/genres` |
| 4 | Registrar filmes assistidos | `POST /users/{id}/watched` |
| 5 | Registrar avaliações | `POST /users/{id}/ratings` (nota de 1 a 5) |
| 6 | Gerar recomendações personalizadas | `GET /users/{id}/recommendations` (algoritmo híbrido em Cypher) |
| 7 | Armazenar recomendações em cache | Redis, chave `rec:user:{id}:limit:{n}` |
| 8 | Identificar CACHE HIT e CACHE MISS | campo `cache`, header `X-Cache`, log da API e `GET /cache/stats` |
| 9 | Utilizar TTL | `CACHE_TTL_SECONDS` (padrão 300 s), campo `ttl_seconds` e header `X-Cache-TTL` |
| 10 | Invalidar cache | automático ao avaliar/assistir/cadastrar filme; manual via `DELETE /cache...` |
| 11 | API REST | FastAPI, Swagger em http://localhost:8000/docs |
| 12 | Testes de desempenho | `scripts/benchmark.py`, `scripts/ab_compare.ps1/.sh` e `scripts/locustfile.py` |
| 13 | Análise dos resultados | `scripts/analise_dados.py` → [results/analise_dados.md](results/analise_dados.md), [docs/ANALISE_DESEMPENHO.md](docs/ANALISE_DESEMPENHO.md) e **Documentacao_StreamRec.docx** |

Documentação complementar:
- **[Documentacao_StreamRec.docx](Documentacao_StreamRec.docx)**: documento completo da entrega (arquitetura, EDA, modelagem, avaliação offline, cache, testes de desempenho e evidências)
- [docs/ARQUITETURA.md](docs/ARQUITETURA.md): arquitetura, modelo de grafo, algoritmo e estratégia de cache
- [docs/ANALISE_DESEMPENHO.md](docs/ANALISE_DESEMPENHO.md): metodologia, resultados e conclusões dos testes de carga

---

## Passo a passo para rodar localmente

### Pré-requisitos
- **Docker Desktop** instalado e **aberto** (no Windows, espere o ícone da baleia ficar "running")
- Opcional: Python 3.11+ (somente se quiser rodar a API ou os scripts fora do Docker)

### 1. Subir o ambiente (Neo4j + Redis + API)

Na pasta do projeto:

```bash
docker compose up -d --build
```

Na primeira vez, o download das imagens leva alguns minutos. A API só sobe depois que o Neo4j e o Redis estiverem saudáveis. Para acompanhar:

```bash
docker compose ps
```

Os três containers (`streamrec-neo4j`, `streamrec-redis`, `streamrec-api`) devem aparecer como `Up`.

### 2. Popular a base com dados de exemplo

Gera 12 gêneros, 300 filmes, 200 usuários, cerca de 5.700 filmes assistidos e 4.000 avaliações:

```bash
docker compose exec api python -m scripts.seed --reset
```

> `--reset` apaga os dados antes. Parâmetros opcionais: `--users 500 --movies 1000`.

### 3. Acessar

| O quê | URL |
|---|---|
| **Front-end StreamRec (site)** | **http://localhost:3000** |
| Swagger (testar a API pelo navegador) | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| Neo4j Browser (usuário `neo4j`, senha `streaming123`) | http://localhost:7474 |

### 3.1 Usando o front-end (http://localhost:3000)

Interface no estilo das plataformas de streaming, 100% conectada à API:

| Tela | O que faz | Endpoints usados |
|---|---|---|
| **Quem está assistindo?** | Escolhe um perfil, cria um novo (`+ Adicionar perfil`) ou entra por ID (`u1`…`u200`) | `GET /users`, `POST /users`, `GET /users/{id}` |
| **Início** | Destaque principal, fileira **Recomendados para você** (com o motivo: "fãs parecidos" ou "seu gênero"), Top 10, fileiras por gênero favorito e assistidos | `GET /users/{id}/recommendations`, `GET /movies/top`, `GET /movies?genre=` |
| **Detalhes do título** (clique no card) | Assistir e avaliar com estrelas (1–5); títulos semelhantes | `POST /users/{id}/watched`, `POST /users/{id}/ratings`, `GET /movies/{id}` |
| **Em alta / Gêneros / Assistidos** | Navegação pelo catálogo e pelo histórico do perfil | `GET /movies/top`, `GET /genres`, `GET /movies` |
| **Adicionar título** | Cadastra filmes e registra gêneros novos | `POST /movies`, `POST /genres` |
| **Painel do cache** | HIT, MISS, hit ratio, consultas ao Neo4j, botões para buscar com ou sem cache, invalidar e zerar métricas, além do log das chamadas | `GET /cache/stats`, `DELETE /cache...` |

O indicador no canto inferior direito mostra, a cada carregamento das recomendações, se foi **CACHE HIT** (verde) ou **CACHE MISS** (laranja), o tempo no servidor e o TTL restante. Avaliar ou assistir um título invalida o cache do perfil: a próxima recomendação volta como MISS e é recalculada no Neo4j.

> Um perfil novo, sem histórico, recebe "Populares para começar" (cold start). Depois de algumas avaliações ele passa a receber recomendações personalizadas.

### 4. Demonstração completa (todos os requisitos)

Cadastra gêneros, filmes e usuários, registra assistidos e avaliações e mostra a sequência **MISS → HIT → invalidação → MISS → HIT**:

```bash
docker compose exec api python -m scripts.demo_flow
```

Exemplo de saída:
```
=== Recomendações (1ª vez)  [GET /users/alice-94f2/recommendations] -> 200
    X-Cache: MISS  |  X-Cache-TTL: 300  |  tempo: 210.61 ms
=== Recomendações (2ª vez)  [GET /users/alice-94f2/recommendations] -> 200
    X-Cache: HIT  |  X-Cache-TTL: 300  |  tempo: 3.50 ms
=== Nova avaliação (invalida cache)  [POST /users/alice-94f2/ratings] -> 201
    chaves invalidadas: 1
```

Para ver os HIT/MISS no log da API:

```bash
docker compose logs -f api
```

### 5. Testes de desempenho

**a) Benchmark em Python** (3 cenários: sem cache, cache frio e cache quente). Roda em um container separado e salva os resultados em `results/`.

Windows (PowerShell):
```bash
docker compose run --rm --no-deps -v "${PWD}/results:/code/results" api python -m scripts.benchmark --base-url http://api:8000 --requests 2000 --concurrency 50
```

Linux/macOS/Git Bash:
```bash
docker compose run --rm --no-deps -v "$(pwd)/results:/code/results" api python -m scripts.benchmark --base-url http://api:8000 --requests 2000 --concurrency 50
```

**b) ApacheBench** (gerador de carga em C, mais preciso para medir o teto da API), comparando com e sem cache:

Windows (PowerShell):
```bash
powershell -ExecutionPolicy Bypass -File .\scripts\ab_compare.ps1 -N 2000 -C 50
```

Linux/macOS/Git Bash:
```bash
bash scripts/ab_compare.sh 2000 50
```

**c) Análise de dados** (EDA da base, avaliação offline do recomendador com Precision/Recall/nDCG@10 contra modelos de referência, simulação do efeito do TTL e estatística dos testes de carga). Rode depois do benchmark. Gera `results/analise_dados.md`, `results/analise_dados.json` e os gráficos em `results/graficos/`:

```bash
docker compose run --rm --no-deps -v "${PWD}/results:/code/results" api python -m scripts.analise_dados
```

**d) Locust** (opcional, com interface web): `pip install locust`, depois `locust -f scripts/locustfile.py --host http://localhost:8000` e abra http://localhost:8089.

### 6. Parar o ambiente

```bash
docker compose down
```

Use `docker compose down -v` para apagar também os dados do Neo4j.

---

## Alternativa: API rodando fora do Docker

Útil para depurar. O Neo4j e o Redis continuam no Docker:

```bash
docker compose up -d neo4j redis
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.seed --reset
uvicorn app.main:app --reload --port 8000
```

As configurações padrão (`bolt://localhost:7687`, `redis://localhost:6379/0`) já apontam para os containers. Para alterá-las, defina as variáveis listadas em `.env.example`. Se o container `streamrec-api` estiver rodando, pare-o antes (`docker compose stop api`) para liberar a porta 8000.

---

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/health` | Verifica Neo4j e Redis |
| POST | `/users` | Cadastra usuário `{id?, name, age?}` |
| GET | `/users` | Lista usuários (`skip`, `limit`) |
| GET | `/users/{id}` | Detalha o usuário com o histórico de filmes e notas |
| POST | `/users/{id}/watched` | Registra filme assistido `{movie_id}` (invalida o cache do usuário) |
| POST | `/users/{id}/ratings` | Registra avaliação `{movie_id, score 1-5}` (invalida o cache do usuário) |
| GET | `/users/{id}/recommendations` | Recomendações (`limit`, `use_cache=true/false`) |
| POST | `/genres` | Registra gênero `{name}` |
| GET | `/genres` | Lista gêneros com a quantidade de filmes |
| POST | `/movies` | Cadastra filme `{id?, title, year?, genres[]}` (invalida todo o cache de recomendações) |
| GET | `/movies` | Lista filmes (filtro `genre`) |
| GET | `/movies/top` | Filmes em alta (nota média × popularidade), com cache de 60 s |
| GET | `/movies/{id}` | Detalha o filme com a nota média |
| POST | `/movies/{id}/genres` | Associa gêneros ao filme |
| GET | `/cache/stats` | HIT, MISS, hit ratio, consultas ao Neo4j, chaves em cache e memória |
| POST | `/cache/stats/reset` | Zera as métricas |
| DELETE | `/cache/users/{id}` | Invalida o cache de um usuário |
| DELETE | `/cache` | Invalida todo o cache de recomendações |

### Exemplos com curl

```bash
curl -X POST localhost:8000/genres -H "Content-Type: application/json" -d "{\"name\":\"Drama\"}"
curl -X POST localhost:8000/movies -H "Content-Type: application/json" -d "{\"id\":\"f1\",\"title\":\"Interestelar\",\"year\":2014,\"genres\":[\"Ficção Científica\",\"Drama\"]}"
curl -X POST localhost:8000/users  -H "Content-Type: application/json" -d "{\"id\":\"ana\",\"name\":\"Ana\",\"age\":28}"
curl -X POST localhost:8000/users/ana/watched -H "Content-Type: application/json" -d "{\"movie_id\":\"f1\"}"
curl -X POST localhost:8000/users/ana/ratings -H "Content-Type: application/json" -d "{\"movie_id\":\"f1\",\"score\":5}"
curl -i "localhost:8000/users/u1/recommendations?limit=5"
curl localhost:8000/cache/stats
curl -X DELETE localhost:8000/cache/users/u1
```

> No PowerShell use `curl.exe` (e não `curl`), ou simplesmente o Swagger em `/docs`.

Exemplo de resposta de recomendação:

```json
{
  "user_id": "u1",
  "strategy": "personalized",
  "cache": "HIT",
  "ttl_seconds": 287,
  "generated_at": "2026-09-28T12:30:01.120Z",
  "items": [
    {"movie_id": "m42", "title": "O Reino Secreto 42", "year": 2011,
     "genres": ["Drama", "Suspense"], "score": 61.5, "avg_rating": 4.2,
     "reasons": ["usuarios_similares", "genero_favorito"]}
  ]
}
```

---

## Estrutura do projeto

```
.
├── app/
│   ├── main.py                  # FastAPI: lifespan, middleware de tempo, rotas
│   ├── config.py                # Variáveis de ambiente
│   ├── database.py              # Driver assíncrono do Neo4j e constraints
│   ├── cache.py                 # Redis: get/set com TTL, invalidação, métricas
│   ├── schemas.py               # Modelos Pydantic
│   ├── services/recommendation.py  # Algoritmo (Cypher) e cache-aside
│   └── routers/                 # users, movies, genres, cache_admin
├── scripts/
│   ├── seed.py                  # Carga de dados sintéticos
│   ├── demo_flow.py             # Demonstração ponta a ponta
│   ├── benchmark.py             # Teste de desempenho (3 cenários)
│   ├── ab_compare.ps1 / .sh     # ApacheBench com e sem cache
│   └── locustfile.py            # Teste de carga com Locust
├── web/
│   ├── public/                  # Front-end: index.html, css/style.css, js/app.js
│   └── nginx.conf               # Serve o site e faz proxy de /api para a FastAPI
├── docs/                        # Arquitetura e análise de desempenho
├── results/                     # Saídas dos testes de desempenho
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Problemas comuns

| Sintoma | Solução |
|---|---|
| `failed to connect to the docker API` | Abra o Docker Desktop e espere ele iniciar |
| Porta 7474, 7687, 6379 ou 8000 em uso | Pare o serviço que usa a porta ou altere o mapeamento em `docker-compose.yml` |
| A API reinicia logo após subir | O Neo4j demora cerca de 20 s na primeira vez; confira com `docker compose logs neo4j` |
| Recomendações vazias ou `strategy: popular` | Rode o seed; usuários sem histórico recebem os filmes populares (cold start) |
