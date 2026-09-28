"""Ponto de entrada da API de Recomendação (FastAPI)."""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.cache import cache
from app.config import settings
from app.database import db
from app.routers import cache_admin, genres, movies, users

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
# O driver do Neo4j é muito verboso em INFO (avisos de schema etc.)
logging.getLogger("neo4j").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache.connect()
    await db.connect()
    yield
    await db.close()
    await cache.close()


app = FastAPI(
    title="StreamRec - API de Recomendação",
    description="Recomendação personalizada de filmes com **Neo4j** (grafo) e **Redis** (cache).",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def process_time_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
    return response


app.include_router(users.router)
app.include_router(movies.router)
app.include_router(genres.router)
app.include_router(cache_admin.router)


@app.get("/health", tags=["Infra"])
async def health():
    await cache.client.ping()
    await db.driver.verify_connectivity()
    return {"status": "ok", "neo4j": "up", "redis": "up"}
