"""Motor de recomendação + estratégia de cache (cache-aside).

Fluxo:
  1. Monta a chave  rec:user:<id>:limit:<n>
  2. Procura no Redis  -> encontrou  = CACHE HIT  (não consulta o Neo4j)
                       -> não achou  = CACHE MISS (consulta o Neo4j e grava com TTL)
"""
import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from app.cache import cache, recommendation_key
from app.config import settings
from app.database import db

logger = logging.getLogger("recommendation")

# Algoritmo híbrido em uma única consulta Cypher:
#  (a) filtragem colaborativa: usuários que gostaram (nota >= 4) dos mesmos filmes
#      que o usuário alvo -> filmes que ELES gostaram e o alvo ainda não viu;
#  (b) baseada em conteúdo: afinidade do usuário com cada gênero (filmes vistos,
#      ponderados pela nota) -> filmes desses gêneros ainda não vistos;
#  (c) o score final soma (a) + (b) e dá um bônus pela nota média global do filme.
COLLABORATIVE_BRANCH = """
    MATCH (u)-[r1:RATED]->(:Movie)<-[r2:RATED]-(other:User)
    WHERE other <> u AND r1.score >= 4 AND r2.score >= 4
    WITH u, other, count(*) AS similarity
    ORDER BY similarity DESC
    LIMIT 25
    MATCH (other)-[r3:RATED]->(rec:Movie)
    WHERE r3.score >= 4 AND NOT EXISTS { (u)-[:WATCHED]->(rec) }
    RETURN rec, sum(similarity * r3.score) * 1.0 AS score, 'usuarios_similares' AS source
"""

CONTENT_BRANCH = """
    MATCH (u)-[:WATCHED]->(m:Movie)-[:IN_GENRE]->(g:Genre)
    OPTIONAL MATCH (u)-[r:RATED]->(m)
    WITH u, g, sum(CASE WHEN r IS NULL THEN 1.0 ELSE r.score - 2.5 END) AS affinity
    WHERE affinity > 0
    MATCH (g)<-[:IN_GENRE]-(rec:Movie)
    WHERE NOT EXISTS { (u)-[:WATCHED]->(rec) }
    RETURN rec, sum(affinity) * 2.0 AS score, 'genero_favorito' AS source
"""

RANKING_TAIL = """
WITH rec, sum(score) AS score, collect(DISTINCT source) AS reasons
OPTIONAL MATCH (rec)<-[ar:RATED]-(:User)
WITH rec, score, reasons, avg(ar.score) AS avg_rating
WITH rec, reasons, avg_rating, score + coalesce(avg_rating, 0) * 5 AS final
ORDER BY final DESC
LIMIT $limit
OPTIONAL MATCH (rec)-[:IN_GENRE]->(g:Genre)
RETURN rec.id AS movie_id, rec.title AS title, rec.year AS year,
       collect(g.name) AS genres, round(final, 2) AS score,
       round(coalesce(avg_rating, 0.0), 2) AS avg_rating, reasons
ORDER BY score DESC
"""


def build_query(*branches: str) -> str:
    """Monta a consulta de recomendação a partir de um ou mais sinais (usado também na avaliação offline)."""
    union = "\n  UNION ALL\n".join(branches)
    return "MATCH (u:User {id: $user_id})\nCALL (u) {" + union + "}" + RANKING_TAIL


PERSONALIZED_QUERY = build_query(COLLABORATIVE_BRANCH, CONTENT_BRANCH)

# Cold start: usuário sem histórico -> filmes mais bem avaliados que ele não viu.
POPULAR_QUERY = """
MATCH (u:User {id: $user_id})
MATCH (rec:Movie)<-[r:RATED]-(:User)
WHERE NOT EXISTS { (u)-[:WATCHED]->(rec) }
WITH rec, avg(r.score) AS avg_rating, count(r) AS votes
WHERE votes >= 3
WITH rec, avg_rating, votes, avg_rating * log10(votes + 1) AS final
ORDER BY final DESC
LIMIT $limit
OPTIONAL MATCH (rec)-[:IN_GENRE]->(g:Genre)
RETURN rec.id AS movie_id, rec.title AS title, rec.year AS year,
       collect(g.name) AS genres, round(final, 2) AS score,
       round(avg_rating, 2) AS avg_rating, ['populares'] AS reasons
ORDER BY score DESC
"""


async def _compute(user_id: str, limit: int) -> dict:
    items = await db.run(PERSONALIZED_QUERY, user_id=user_id, limit=limit)
    strategy = "personalized"
    if not items:
        exists = await db.run("MATCH (u:User {id: $id}) RETURN u.id AS id", id=user_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Usuário '{user_id}' não encontrado")
        items = await db.run(POPULAR_QUERY, user_id=user_id, limit=limit)
        strategy = "popular"
    return {
        "user_id": user_id,
        "strategy": strategy,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }


async def get_recommendations(user_id: str, limit: int, use_cache: bool = True) -> dict:
    if not use_cache:
        payload = await _compute(user_id, limit)
        return {**payload, "cache": "BYPASS", "ttl_seconds": None}

    key = recommendation_key(user_id, limit)
    cached, ttl = await cache.get_with_ttl(key)
    if cached is not None:
        await cache.record_hit()
        logger.info("CACHE HIT  key=%s ttl_restante=%ss", key, ttl)
        return {**cached, "cache": "HIT", "ttl_seconds": ttl}

    await cache.record_miss()
    logger.info("CACHE MISS key=%s -> consultando Neo4j", key)
    payload = await _compute(user_id, limit)
    await cache.set_json(key, payload, settings.cache_ttl_seconds)
    return {**payload, "cache": "MISS", "ttl_seconds": settings.cache_ttl_seconds}
