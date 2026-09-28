import uuid

from fastapi import APIRouter, HTTPException, Query
from neo4j.exceptions import ConstraintError

from app.cache import REC_PREFIX, cache
from app.database import db
from app.schemas import Movie, MovieCreate, MovieGenres

router = APIRouter(prefix="/movies", tags=["Filmes"])

TOP_PREFIX = "top:movies:"
TOP_TTL_SECONDS = 60

MOVIE_RETURN = """
OPTIONAL MATCH (m)-[:IN_GENRE]->(g:Genre)
WITH m, collect(DISTINCT g.name) AS genres
OPTIONAL MATCH (m)<-[r:RATED]-(:User)
RETURN m.id AS id, m.title AS title, m.year AS year, genres,
       round(avg(r.score), 2) AS avg_rating, count(r) AS ratings
"""


@router.post("", response_model=Movie, status_code=201, summary="Cadastrar filme")
async def create_movie(body: MovieCreate):
    movie_id = body.id or f"m-{uuid.uuid4().hex[:8]}"
    try:
        rows = await db.run(
            """
            CREATE (m:Movie {id: $id, title: $title, year: $year})
            FOREACH (gname IN $genres |
                MERGE (g:Genre {name: gname})
                MERGE (m)-[:IN_GENRE]->(g))
            WITH m
            """ + MOVIE_RETURN,
            write=True, id=movie_id, title=body.title, year=body.year, genres=body.genres,
        )
    except ConstraintError:
        raise HTTPException(409, f"Filme '{movie_id}' já existe")
    # Catálogo mudou: um filme novo pode entrar na recomendação de qualquer usuário.
    await cache.delete_pattern(f"{REC_PREFIX}*")
    await cache.delete_pattern(f"{TOP_PREFIX}*")
    return rows[0]


@router.get("", response_model=list[Movie], summary="Listar filmes")
async def list_movies(
    genre: str | None = Query(None, description="Filtrar por gênero"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    return await db.run(
        """
        MATCH (m:Movie)
        WHERE $genre IS NULL OR EXISTS { (m)-[:IN_GENRE]->(:Genre {name: $genre}) }
        WITH m ORDER BY m.id SKIP $skip LIMIT $limit
        """ + MOVIE_RETURN + " ORDER BY id",
        genre=genre, skip=skip, limit=limit,
    )


@router.get("/top", response_model=list[Movie], summary="Filmes em alta (mais bem avaliados, com cache)")
async def top_movies(limit: int = Query(20, ge=1, le=100)):
    key = f"{TOP_PREFIX}{limit}"
    cached, _ = await cache.get_with_ttl(key)
    if cached is not None:
        return cached
    rows = await db.run(
        """
        MATCH (m:Movie)<-[r:RATED]-(:User)
        WITH m, avg(r.score) AS a, count(r) AS n
        WHERE n >= 3
        WITH m, a * log10(n + 1) AS rank ORDER BY rank DESC LIMIT $limit
        """ + MOVIE_RETURN + " ORDER BY avg_rating * log10(ratings + 1) DESC",
        limit=limit,
    )
    await cache.set_json(key, rows, TOP_TTL_SECONDS)
    return rows


@router.get("/{movie_id}", response_model=Movie, summary="Detalhar filme")
async def get_movie(movie_id: str):
    rows = await db.run("MATCH (m:Movie {id: $id})" + MOVIE_RETURN, id=movie_id)
    if not rows:
        raise HTTPException(404, f"Filme '{movie_id}' não encontrado")
    return rows[0]


@router.post("/{movie_id}/genres", response_model=Movie, summary="Associar gêneros a um filme")
async def add_genres(movie_id: str, body: MovieGenres):
    rows = await db.run(
        """
        MATCH (m:Movie {id: $id})
        FOREACH (gname IN $genres |
            MERGE (g:Genre {name: gname})
            MERGE (m)-[:IN_GENRE]->(g))
        WITH m
        """ + MOVIE_RETURN,
        write=True, id=movie_id, genres=body.genres,
    )
    if not rows:
        raise HTTPException(404, f"Filme '{movie_id}' não encontrado")
    await cache.delete_pattern(f"{REC_PREFIX}*")
    await cache.delete_pattern(f"{TOP_PREFIX}*")
    return rows[0]
