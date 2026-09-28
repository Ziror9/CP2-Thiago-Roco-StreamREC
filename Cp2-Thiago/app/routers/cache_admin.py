from fastapi import APIRouter

from app.cache import REC_PREFIX, cache, user_recommendation_pattern

router = APIRouter(prefix="/cache", tags=["Cache"])


@router.get("/stats", summary="Métricas: HIT, MISS, hit ratio, consultas ao Neo4j")
async def stats():
    return await cache.stats()


@router.post("/stats/reset", summary="Zerar métricas")
async def reset_stats():
    await cache.reset_stats()
    return {"status": "ok"}


@router.delete("/users/{user_id}", summary="Invalidar cache de um usuário")
async def invalidate_user(user_id: str):
    removed = await cache.delete_pattern(user_recommendation_pattern(user_id))
    return {"user_id": user_id, "keys_removed": removed}


@router.delete("", summary="Invalidar todo o cache de recomendações")
async def invalidate_all():
    removed = await cache.delete_pattern(f"{REC_PREFIX}*")
    return {"keys_removed": removed}
