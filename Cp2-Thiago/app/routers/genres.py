from fastapi import APIRouter, HTTPException
from neo4j.exceptions import ConstraintError

from app.database import db
from app.schemas import Genre, GenreCreate

router = APIRouter(prefix="/genres", tags=["Gêneros"])


@router.post("", response_model=Genre, status_code=201, summary="Registrar gênero")
async def create_genre(body: GenreCreate):
    try:
        rows = await db.run(
            "CREATE (g:Genre {name: $name}) RETURN g.name AS name, 0 AS movies",
            write=True, name=body.name.strip(),
        )
    except ConstraintError:
        raise HTTPException(409, f"Gênero '{body.name}' já existe")
    return rows[0]


@router.get("", response_model=list[Genre], summary="Listar gêneros")
async def list_genres():
    return await db.run(
        "MATCH (g:Genre) OPTIONAL MATCH (g)<-[:IN_GENRE]-(m:Movie) "
        "RETURN g.name AS name, count(m) AS movies ORDER BY movies DESC, name"
    )
