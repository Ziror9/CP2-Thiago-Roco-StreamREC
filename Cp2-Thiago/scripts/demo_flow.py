"""Demonstração ponta a ponta de todos os requisitos pela API REST.

Cadastra gêneros, filmes e usuários, registra assistidos e avaliações,
e mostra CACHE MISS -> CACHE HIT -> invalidação -> CACHE MISS.

Uso:  python -m scripts.demo_flow
"""
import argparse
import sys
import uuid

import httpx


def show(title: str, r: httpx.Response):
    print(f"\n=== {title}  [{r.request.method} {r.request.url.path}] -> {r.status_code}")
    if "X-Cache" in r.headers:
        print(f"    X-Cache: {r.headers['X-Cache']}  |  X-Cache-TTL: {r.headers.get('X-Cache-TTL')}"
              f"  |  tempo: {r.headers.get('X-Process-Time-ms')} ms")
    return r.json()


def main(base_url: str):
    tag = uuid.uuid4().hex[:4]
    c = httpx.Client(base_url=base_url, timeout=30)
    c.get("/health").raise_for_status()

    for g in ["Ficção Científica", "Drama", "Comédia"]:
        c.post("/genres", json={"name": g})  # 409 se já existir: tudo bem
    show("Gêneros", c.get("/genres"))

    movies = [
        ("Interestelar", 2014, ["Ficção Científica", "Drama"]),
        ("A Chegada", 2016, ["Ficção Científica", "Drama"]),
        ("Duna", 2021, ["Ficção Científica"]),
        ("Matrix", 1999, ["Ficção Científica", "Ação"]),
        ("Se Beber Não Case", 2009, ["Comédia"]),
    ]
    ids = {}
    for title, year, genres in movies:
        m = show("Cadastrar filme", c.post("/movies", json={"id": f"demo-{tag}-{len(ids)}", "title": title,
                                                             "year": year, "genres": genres}))
        ids[title] = m["id"]

    alice = show("Cadastrar usuário", c.post("/users", json={"id": f"alice-{tag}", "name": "Alice", "age": 25}))["id"]
    bob = show("Cadastrar usuário", c.post("/users", json={"id": f"bob-{tag}", "name": "Bob", "age": 31}))["id"]

    # Bob e Alice têm gosto parecido; Bob também gostou de Duna e Matrix.
    for uid, title, score in [(alice, "Interestelar", 5), (alice, "A Chegada", 4),
                              (bob, "Interestelar", 5), (bob, "A Chegada", 5),
                              (bob, "Duna", 5), (bob, "Matrix", 4)]:
        show("Registrar avaliação", c.post(f"/users/{uid}/ratings", json={"movie_id": ids[title], "score": score}))
    show("Registrar assistido", c.post(f"/users/{alice}/watched", json={"movie_id": ids["Se Beber Não Case"]}))

    r1 = show("Recomendações (1ª vez)", c.get(f"/users/{alice}/recommendations", params={"limit": 5}))
    for it in r1["items"]:
        print(f"    - {it['title']:<25} score={it['score']:<8} motivos={it['reasons']}")
    show("Recomendações (2ª vez)", c.get(f"/users/{alice}/recommendations", params={"limit": 5}))

    inv = show("Nova avaliação (invalida cache)", c.post(f"/users/{alice}/ratings",
                                                         json={"movie_id": ids["Duna"], "score": 5}))
    print(f"    chaves invalidadas: {inv['cache_keys_invalidated']}")
    show("Recomendações (após invalidação)", c.get(f"/users/{alice}/recommendations", params={"limit": 5}))
    show("Recomendações (de novo)", c.get(f"/users/{alice}/recommendations", params={"limit": 5}))

    print(f"\n    removidas: {show('Invalidação manual', c.delete(f'/cache/users/{alice}'))['keys_removed']}")
    print("\n", show("Métricas do cache", c.get("/cache/stats")))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    try:
        main(p.parse_args().base_url)
    except httpx.ConnectError:
        sys.exit("API não está no ar. Rode: docker compose up -d")
