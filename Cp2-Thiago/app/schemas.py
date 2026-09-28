"""Modelos de entrada/saída (Pydantic)."""
from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- Usuários
class UserCreate(BaseModel):
    id: str | None = Field(None, description="Opcional. Se omitido, é gerado automaticamente.", examples=["u1"])
    name: str = Field(..., min_length=1, examples=["Ana Souza"])
    age: int | None = Field(None, ge=0, le=120, examples=[28])


class User(BaseModel):
    id: str
    name: str
    age: int | None = None


# ---------------------------------------------------------------- Gêneros
class GenreCreate(BaseModel):
    name: str = Field(..., min_length=1, examples=["Ficção Científica"])


class Genre(BaseModel):
    name: str
    movies: int = 0


# ---------------------------------------------------------------- Filmes
class MovieCreate(BaseModel):
    id: str | None = Field(None, examples=["m1"])
    title: str = Field(..., min_length=1, examples=["Interestelar"])
    year: int | None = Field(None, ge=1880, le=2100, examples=[2014])
    genres: list[str] = Field(default_factory=list, examples=[["Ficção Científica", "Drama"]])


class MovieGenres(BaseModel):
    genres: list[str] = Field(..., min_length=1)


class Movie(BaseModel):
    id: str
    title: str
    year: int | None = None
    genres: list[str] = []
    avg_rating: float | None = None
    ratings: int = 0


# ---------------------------------------------------------------- Interações
class WatchCreate(BaseModel):
    movie_id: str = Field(..., examples=["m1"])


class RatingCreate(BaseModel):
    movie_id: str = Field(..., examples=["m1"])
    score: int = Field(..., ge=1, le=5, description="Nota de 1 a 5", examples=[5])


class InteractionResult(BaseModel):
    user_id: str
    movie_id: str
    type: str
    score: int | None = None
    cache_keys_invalidated: int


# ---------------------------------------------------------------- Recomendações
class RecommendationItem(BaseModel):
    movie_id: str
    title: str
    year: int | None = None
    genres: list[str]
    score: float
    avg_rating: float
    reasons: list[str]


class RecommendationResponse(BaseModel):
    user_id: str
    strategy: str = Field(..., description="personalized | popular (cold start)")
    cache: str = Field(..., description="HIT | MISS | BYPASS")
    ttl_seconds: int | None = Field(None, description="Tempo restante no cache")
    generated_at: datetime
    items: list[RecommendationItem]
