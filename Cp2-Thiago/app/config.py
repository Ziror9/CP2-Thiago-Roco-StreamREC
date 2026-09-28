"""Configurações da aplicação, lidas de variáveis de ambiente."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "streaming123")
    neo4j_pool_size: int = int(os.getenv("NEO4J_POOL_SIZE", "50"))

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Tempo de vida (segundos) das recomendações em cache
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
