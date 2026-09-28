"""Acesso ao Neo4j usando o driver assíncrono oficial (pool de conexões compartilhado)."""
import logging

from neo4j import AsyncDriver, AsyncGraphDatabase, RoutingControl

from app.cache import cache
from app.config import settings

logger = logging.getLogger("graphdb")

SCHEMA_QUERIES = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT movie_id IF NOT EXISTS FOR (m:Movie) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT genre_name IF NOT EXISTS FOR (g:Genre) REQUIRE g.name IS UNIQUE",
    "CREATE INDEX movie_title IF NOT EXISTS FOR (m:Movie) ON (m.title)",
]


class Neo4jDB:
    def __init__(self) -> None:
        self.driver: AsyncDriver | None = None

    async def connect(self) -> None:
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_pool_size,
        )
        await self.driver.verify_connectivity()
        for query in SCHEMA_QUERIES:
            await self.driver.execute_query(query)
        logger.info("Conectado ao Neo4j em %s", settings.neo4j_uri)

    async def close(self) -> None:
        if self.driver:
            await self.driver.close()

    async def run(self, query: str, write: bool = False, **params) -> list[dict]:
        """Executa uma consulta Cypher e devolve as linhas como dicionários.

        Cada chamada é contabilizada no Redis para medir quantas consultas
        o cache evitou.
        """
        records, _, _ = await self.driver.execute_query(
            query,
            parameters_=params,
            routing_=RoutingControl.WRITE if write else RoutingControl.READ,
        )
        await cache.record_neo4j_query()
        return [r.data() for r in records]


db = Neo4jDB()
