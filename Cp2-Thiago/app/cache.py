"""Camada de cache (Redis).

Responsável por:
- ler/gravar recomendações serializadas em JSON com TTL;
- contabilizar CACHE HIT / CACHE MISS e consultas ao Neo4j (métricas);
- invalidar chaves por padrão (ex.: todas as recomendações de um usuário).
"""
import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger("cache")

STATS_HITS = "stats:cache:hits"
STATS_MISSES = "stats:cache:misses"
STATS_NEO4J = "stats:neo4j:queries"
STATS_INVALIDATIONS = "stats:cache:invalidations"

REC_PREFIX = "rec:user:"


def recommendation_key(user_id: str, limit: int) -> str:
    """Chave padronizada: rec:user:<id>:limit:<n>."""
    return f"{REC_PREFIX}{user_id}:limit:{limit}"


def user_recommendation_pattern(user_id: str) -> str:
    return f"{REC_PREFIX}{user_id}:*"


class Cache:
    def __init__(self) -> None:
        self.client: redis.Redis | None = None

    async def connect(self) -> None:
        self.client = redis.from_url(
            settings.redis_url, decode_responses=True, max_connections=200
        )
        await self.client.ping()
        logger.info("Conectado ao Redis em %s", settings.redis_url)

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()

    # ------------------------------------------------------------------ leitura/escrita
    async def get_with_ttl(self, key: str) -> tuple[Any | None, int]:
        """Retorna (valor, ttl_restante) em uma única ida ao Redis (pipeline)."""
        async with self.client.pipeline(transaction=False) as pipe:
            pipe.get(key)
            pipe.ttl(key)
            raw, ttl = await pipe.execute()
        return (json.loads(raw) if raw is not None else None), ttl

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        await self.client.set(key, json.dumps(value, default=str), ex=ttl)

    # ------------------------------------------------------------------ invalidação
    async def delete_pattern(self, pattern: str) -> int:
        """Remove todas as chaves que casam com o padrão (usa SCAN, não bloqueia o Redis)."""
        deleted = 0
        batch: list[str] = []
        async for key in self.client.scan_iter(match=pattern, count=500):
            batch.append(key)
            if len(batch) >= 500:
                deleted += await self.client.unlink(*batch)
                batch.clear()
        if batch:
            deleted += await self.client.unlink(*batch)
        if deleted:
            await self.client.incrby(STATS_INVALIDATIONS, deleted)
        logger.info("CACHE INVALIDATE pattern=%s removidas=%d", pattern, deleted)
        return deleted

    # ------------------------------------------------------------------ métricas
    async def record_hit(self) -> None:
        await self.client.incr(STATS_HITS)

    async def record_miss(self) -> None:
        await self.client.incr(STATS_MISSES)

    async def record_neo4j_query(self) -> None:
        await self.client.incr(STATS_NEO4J)

    async def count_keys(self, pattern: str) -> int:
        total = 0
        async for _ in self.client.scan_iter(match=pattern, count=1000):
            total += 1
        return total

    async def stats(self) -> dict:
        hits, misses, neo4j, inval = await self.client.mget(
            STATS_HITS, STATS_MISSES, STATS_NEO4J, STATS_INVALIDATIONS
        )
        hits, misses = int(hits or 0), int(misses or 0)
        total = hits + misses
        memory = await self.client.info("memory")
        return {
            "hits": hits,
            "misses": misses,
            "hit_ratio": round(hits / total, 4) if total else 0.0,
            "neo4j_queries": int(neo4j or 0),
            "invalidated_keys": int(inval or 0),
            "cached_recommendation_keys": await self.count_keys(f"{REC_PREFIX}*"),
            "ttl_seconds": settings.cache_ttl_seconds,
            "redis_used_memory": memory.get("used_memory_human"),
        }

    async def reset_stats(self) -> None:
        await self.client.delete(STATS_HITS, STATS_MISSES, STATS_NEO4J, STATS_INVALIDATIONS)


cache = Cache()
