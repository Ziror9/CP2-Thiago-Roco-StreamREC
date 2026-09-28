"""Popula o Neo4j com dados sintéticos (gêneros, filmes, usuários, visualizações e notas).

Uso:
    python -m scripts.seed --reset --users 200 --movies 300

Cada usuário recebe 2-3 gêneros favoritos; ~70% dos filmes que assiste são
desses gêneros e as notas são mais altas neles. Isso cria padrões reais para
o algoritmo de recomendação encontrar.
"""
import argparse
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis.asyncio as redis  # noqa: E402
from neo4j import AsyncGraphDatabase  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SCHEMA_QUERIES  # noqa: E402

GENRES = [
    "Ação", "Aventura", "Animação", "Comédia", "Crime", "Documentário",
    "Drama", "Fantasia", "Ficção Científica", "Romance", "Suspense", "Terror",
]
ADJ = ["Último", "Grande", "Perdido", "Silencioso", "Eterno", "Sombrio", "Dourado",
       "Secreto", "Invisível", "Selvagem", "Proibido", "Distante", "Vermelho", "Frio"]
NOUNS = ["Horizonte", "Império", "Segredo", "Caminho", "Destino", "Oceano", "Código",
         "Reino", "Planeta", "Verão", "Labirinto", "Guardião", "Sinal", "Legado", "Voo"]
FIRST = ["Ana", "Bruno", "Carla", "Diego", "Eduarda", "Felipe", "Gabriela", "Heitor",
         "Isabela", "João", "Karen", "Lucas", "Mariana", "Nicolas", "Olívia", "Pedro",
         "Rafaela", "Samuel", "Tatiana", "Vitor"]
LAST = ["Silva", "Souza", "Oliveira", "Santos", "Lima", "Costa", "Pereira", "Almeida",
        "Ferreira", "Rodrigues", "Gomes", "Martins"]


def build_dataset(n_users: int, n_movies: int, rng: random.Random):
    movies = []
    by_genre: dict[str, list[str]] = {g: [] for g in GENRES}
    for i in range(1, n_movies + 1):
        genres = rng.sample(GENRES, rng.choice([1, 2, 2, 3]))
        mid = f"m{i}"
        movies.append({
            "id": mid,
            "title": f"O {rng.choice(NOUNS)} {rng.choice(ADJ)} {i}",
            "year": rng.randint(1980, 2025),
            "genres": genres,
        })
        for g in genres:
            by_genre[g].append(mid)

    users, watched, ratings = [], [], []
    all_ids = [m["id"] for m in movies]
    for i in range(1, n_users + 1):
        uid = f"u{i}"
        users.append({"id": uid, "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                      "age": rng.randint(16, 70)})
        favorites = rng.sample(GENRES, rng.choice([2, 3]))
        fav_pool = sorted({m for g in favorites for m in by_genre[g]})
        seen: set[str] = set()
        target = rng.randint(15, 40)
        while len(seen) < target:
            pool = fav_pool if rng.random() < 0.7 else all_ids
            seen.add(rng.choice(pool))
        for mid in seen:
            watched.append({"u": uid, "m": mid})
            if rng.random() < 0.7:
                fav = mid in fav_pool
                score = rng.choices([3, 4, 5], [2, 4, 4])[0] if fav else rng.choices([1, 2, 3, 4], [2, 3, 3, 1])[0]
                ratings.append({"u": uid, "m": mid, "s": score})
    return users, movies, watched, ratings


async def chunked(session_run, query, rows, size=2000):
    for i in range(0, len(rows), size):
        await session_run(query, rows=rows[i:i + size])


async def main(args):
    rng = random.Random(args.seed)
    users, movies, watched, ratings = build_dataset(args.users, args.movies, rng)

    driver = AsyncGraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    await driver.verify_connectivity()

    async def run(q, **p):
        await driver.execute_query(q, parameters_=p)

    if args.reset:
        print("Limpando base...")
        # IN TRANSACTIONS exige transação implícita (auto-commit): usa session.run
        async with driver.session() as session:
            result = await session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS")
            await result.consume()
    for q in SCHEMA_QUERIES:
        await run(q)

    print(f"Gêneros: {len(GENRES)}")
    await run("UNWIND $rows AS name MERGE (:Genre {name: name})", rows=GENRES)
    print(f"Filmes: {len(movies)}")
    await chunked(run, """
        UNWIND $rows AS row
        MERGE (m:Movie {id: row.id}) SET m.title = row.title, m.year = row.year
        WITH m, row UNWIND row.genres AS gname
        MATCH (g:Genre {name: gname}) MERGE (m)-[:IN_GENRE]->(g)
    """, movies)
    print(f"Usuários: {len(users)}")
    await chunked(run, "UNWIND $rows AS row MERGE (u:User {id: row.id}) SET u.name = row.name, u.age = row.age", users)
    print(f"Assistidos: {len(watched)}")
    await chunked(run, """
        UNWIND $rows AS row
        MATCH (u:User {id: row.u}), (m:Movie {id: row.m})
        MERGE (u)-[w:WATCHED]->(m) ON CREATE SET w.at = datetime(), w.times = 1
    """, watched)
    print(f"Avaliações: {len(ratings)}")
    await chunked(run, """
        UNWIND $rows AS row
        MATCH (u:User {id: row.u}), (m:Movie {id: row.m})
        MERGE (u)-[r:RATED]->(m) SET r.score = row.s, r.at = datetime()
    """, ratings)
    await driver.close()

    # Dados mudaram: invalida recomendações antigas.
    r = redis.from_url(settings.redis_url, decode_responses=True)
    removed = 0
    async for key in r.scan_iter(match="rec:*", count=1000):
        removed += await r.unlink(key)
    await r.aclose()
    print(f"Cache invalidado ({removed} chaves). Seed concluído!")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--movies", type=int, default=300)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--reset", action="store_true", help="Apaga todos os dados antes")
    asyncio.run(main(p.parse_args()))
