import uuid

from fastapi import APIRouter, HTTPException, Query, Response
from neo4j.exceptions import ConstraintError

from app.cache import cache, user_recommendation_pattern
from app.database import db
from app.schemas import (
    InteractionResult,
    RatingCreate,
    RecommendationResponse,
    User,
    UserCreate,
    WatchCreate,
)
from app.services.recommendation import get_recommendations

router = APIRouter(prefix="/users", tags=["Usuários"])


@router.post("", response_model=User, status_code=201, summary="Cadastrar usuário")
async def create_user(body: UserCreate):
    user_id = body.id or f"u-{uuid.uuid4().hex[:8]}"
    try:
        rows = await db.run(
            "CREATE (u:User {id: $id, name: $name, age: $age}) "
            "RETURN u.id AS id, u.name AS name, u.age AS age",
            write=True, id=user_id, name=body.name, age=body.age,
        )
    except ConstraintError:
        raise HTTPException(409, f"Usuário '{user_id}' já existe")
    return rows[0]


@router.get("", response_model=list[User], summary="Listar usuários")
async def list_users(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500)):
    return await db.run(
        "MATCH (u:User) RETURN u.id AS id, u.name AS name, u.age AS age "
        "ORDER BY u.id SKIP $skip LIMIT $limit",
        skip=skip, limit=limit,
    )


@router.get("/{user_id}", summary="Detalhar usuário e histórico")
async def get_user(user_id: str):
    rows = await db.run(
        """
        MATCH (u:User {id: $id})
        OPTIONAL MATCH (u)-[w:WATCHED]->(m:Movie)
        OPTIONAL MATCH (u)-[r:RATED]->(m)
        WITH u, m, w, r ORDER BY w.at DESC
        RETURN u.id AS id, u.name AS name, u.age AS age,
               [x IN collect(CASE WHEN m IS NULL THEN NULL ELSE
                   {movie_id: m.id, title: m.title, score: r.score, watched_at: toString(w.at)} END)
                WHERE x IS NOT NULL] AS history
        """,
        id=user_id,
    )
    if not rows:
        raise HTTPException(404, f"Usuário '{user_id}' não encontrado")
    return rows[0]


@router.post("/{user_id}/watched", response_model=InteractionResult, status_code=201,
             summary="Registrar filme assistido")
async def register_watch(user_id: str, body: WatchCreate):
    rows = await db.run(
        """
        MATCH (u:User {id: $uid}), (m:Movie {id: $mid})
        MERGE (u)-[w:WATCHED]->(m)
          ON CREATE SET w.at = datetime(), w.times = 1
          ON MATCH  SET w.at = datetime(), w.times = w.times + 1
        RETURN u.id AS user_id, m.id AS movie_id
        """,
        write=True, uid=user_id, mid=body.movie_id,
    )
    if not rows:
        raise HTTPException(404, "Usuário ou filme não encontrado")
    invalidated = await cache.delete_pattern(user_recommendation_pattern(user_id))
    return {**rows[0], "type": "WATCHED", "cache_keys_invalidated": invalidated}


@router.post("/{user_id}/ratings", response_model=InteractionResult, status_code=201,
             summary="Registrar avaliação (1 a 5)")
async def register_rating(user_id: str, body: RatingCreate):
    # Avaliar implica ter assistido: garante também a relação WATCHED.
    rows = await db.run(
        """
        MATCH (u:User {id: $uid}), (m:Movie {id: $mid})
        MERGE (u)-[r:RATED]->(m)
        SET r.score = $score, r.at = datetime()
        MERGE (u)-[w:WATCHED]->(m)
          ON CREATE SET w.at = datetime(), w.times = 1
        RETURN u.id AS user_id, m.id AS movie_id, r.score AS score
        """,
        write=True, uid=user_id, mid=body.movie_id, score=body.score,
    )
    if not rows:
        raise HTTPException(404, "Usuário ou filme não encontrado")
    invalidated = await cache.delete_pattern(user_recommendation_pattern(user_id))
    return {**rows[0], "type": "RATED", "cache_keys_invalidated": invalidated}


@router.get("/{user_id}/recommendations", response_model=RecommendationResponse,
            summary="Gerar recomendações personalizadas (com cache)")
async def recommendations(
    user_id: str,
    response: Response,
    limit: int = Query(10, ge=1, le=50),
    use_cache: bool = Query(True, description="false = ignora o Redis (usado no teste de desempenho)"),
):
    result = await get_recommendations(user_id, limit, use_cache)
    response.headers["X-Cache"] = result["cache"]
    if result["ttl_seconds"] is not None:
        response.headers["X-Cache-TTL"] = str(result["ttl_seconds"])
    return result
