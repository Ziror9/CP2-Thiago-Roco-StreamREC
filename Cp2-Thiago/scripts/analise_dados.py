"""Análise de dados do StreamRec (visão de Ciência de Dados).

Etapas:
  1. EDA do grafo  - distribuição das notas, esparsidade, cauda longa, gêneros;
  2. Avaliação offline do recomendador (holdout por usuário, Precision/Recall/nDCG/HitRate@K)
     comparando: Híbrido (produção), Colaborativo, Conteúdo, Popularidade e Aleatório;
  3. Simulação do cache: hit ratio e "idade" da recomendação servida em função do TTL;
  4. Estatística dos testes de carga (lê results/benchmark_latest.json, se existir).

A avaliação roda cada usuário dentro de uma transação que é DESFEITA (rollback):
as avaliações "escondidas" são removidas só durante o cálculo, a base não é alterada.

Uso:
    docker compose run --rm --no-deps -v "${PWD}/results:/code/results" api python -m scripts.analise_dados
Saídas: results/analise_dados.json, results/analise_dados.md e results/graficos/*.png
"""
import json
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.recommendation import (  # noqa: E402
    COLLABORATIVE_BRANCH, CONTENT_BRANCH, POPULAR_QUERY, build_query,
)

RESULTS = Path(__file__).resolve().parent.parent / "results"
CHARTS = RESULTS / "graficos"
K = 10
SEED = 42

# ---------------------------------------------------------------- estilo dos gráficos
BLUE, ORANGE, AQUA, YELLOW, MAGENTA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
GRAY = "#b5b3ad"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e8e7e3", "#fcfcfb"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.edgecolor": GRID, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlelocation": "left",
})


def save(fig, name):
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(CHARTS / f"{name}.png", dpi=160)
    plt.close(fig)
    print(f"  gráfico: graficos/{name}.png")


# ================================================================== 1. EDA
def load_data(driver):
    q = lambda c, **p: driver.execute_query(c, parameters_=p)[0]  # noqa: E731
    ratings = pd.DataFrame([r.data() for r in q(
        "MATCH (u:User)-[r:RATED]->(m:Movie) RETURN u.id AS user, m.id AS movie, r.score AS score")])
    watched = pd.DataFrame([r.data() for r in q(
        "MATCH (u:User)-[:WATCHED]->(m:Movie) RETURN u.id AS user, m.id AS movie")])
    movie_genres = pd.DataFrame([r.data() for r in q(
        "MATCH (m:Movie)-[:IN_GENRE]->(g:Genre) RETURN m.id AS movie, g.name AS genre")])
    users = [r["id"] for r in q("MATCH (u:User) RETURN u.id AS id")]
    movies = [r["id"] for r in q("MATCH (m:Movie) RETURN m.id AS id")]
    return ratings, watched, movie_genres, users, movies


def eda(ratings, watched, movie_genres, users, movies):
    print("\n[1/4] Análise exploratória")
    n_u, n_m = len(users), len(movies)
    per_user = ratings.groupby("user").size().reindex(users, fill_value=0)
    per_movie = ratings.groupby("movie").size().reindex(movies, fill_value=0).sort_values(ascending=False)
    top20 = per_movie.head(max(1, int(0.2 * n_m))).sum() / max(1, per_movie.sum())
    genre_stats = (ratings.merge(movie_genres, on="movie").groupby("genre")["score"]
                   .agg(["mean", "count"]).rename(columns={"mean": "nota_media", "count": "avaliacoes"}))
    genre_stats["filmes"] = movie_genres.groupby("genre").size()
    genre_stats = genre_stats.sort_values("filmes", ascending=False)

    stats = {
        "usuarios": n_u, "filmes": n_m, "generos": int(movie_genres["genre"].nunique()),
        "assistidos": len(watched), "avaliacoes": len(ratings),
        "taxa_avaliacao": round(len(ratings) / max(1, len(watched)), 3),
        "densidade_matriz": round(len(ratings) / (n_u * n_m), 4),
        "esparsidade_matriz": round(1 - len(ratings) / (n_u * n_m), 4),
        "nota_media": round(ratings["score"].mean(), 3),
        "nota_desvio": round(ratings["score"].std(), 3),
        "dist_notas": {int(k): int(v) for k, v in ratings["score"].value_counts().sort_index().items()},
        "aval_por_usuario": {"media": round(per_user.mean(), 1), "mediana": float(per_user.median()),
                             "min": int(per_user.min()), "max": int(per_user.max())},
        "aval_por_filme": {"media": round(per_movie.mean(), 1), "mediana": float(per_movie.median()),
                           "min": int(per_movie.min()), "max": int(per_movie.max())},
        "share_top20_filmes": round(float(top20), 3),
        "generos_por_filme": round(movie_genres.groupby("movie").size().mean(), 2),
        "por_genero": {g: {"filmes": int(r.filmes), "avaliacoes": int(r.avaliacoes), "nota_media": round(r.nota_media, 2)}
                       for g, r in genre_stats.iterrows()},
    }
    print(f"  {n_u} usuários, {n_m} filmes, {len(ratings)} avaliações, esparsidade {stats['esparsidade_matriz']:.1%}")

    # --- distribuição das notas
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    dist = ratings["score"].value_counts().sort_index()
    bars = ax.bar(dist.index, dist.values, color=BLUE, width=0.62)
    for b, v in zip(bars, dist.values):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v / dist.sum():.0%}", ha="center", va="bottom", color=INK2, fontsize=9)
    ax.set_title("Distribuição das notas (1 a 5)")
    ax.set_xlabel("Nota"); ax.set_ylabel("Avaliações"); ax.grid(axis="x", visible=False)
    save(fig, "01_distribuicao_notas")

    # --- avaliações por usuário
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    ax.hist(per_user.values, bins=range(0, per_user.max() + 3, 2), color=BLUE, edgecolor=SURFACE, linewidth=2)
    ax.axvline(per_user.median(), color=INK2, linestyle="--", linewidth=1)
    ax.text(per_user.median() + 0.5, ax.get_ylim()[1] * 0.92, f"mediana = {per_user.median():.0f}", color=INK2, fontsize=9)
    ax.set_title("Avaliações por usuário"); ax.set_xlabel("Nº de avaliações"); ax.set_ylabel("Usuários")
    save(fig, "02_avaliacoes_por_usuario")

    # --- cauda longa
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    x = np.arange(1, n_m + 1)
    ax.fill_between(x, per_movie.values, color=BLUE, alpha=0.18, linewidth=0)
    ax.plot(x, per_movie.values, color=BLUE, linewidth=2)
    cut = int(0.2 * n_m)
    ax.axvline(cut, color=INK2, linestyle="--", linewidth=1)
    ax.text(cut + 4, per_movie.max() * 0.85, f"20% dos filmes concentram\n{top20:.0%} das avaliações", color=INK2, fontsize=9)
    ax.set_title("Concentração de popularidade dos filmes")
    ax.set_xlabel("Filmes ordenados por nº de avaliações"); ax.set_ylabel("Avaliações")
    save(fig, "03_popularidade_filmes")

    # --- filmes por gênero
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    gs = genre_stats.sort_values("filmes")
    ax.barh(gs.index, gs["filmes"], color=BLUE, height=0.62)
    for i, (f, m) in enumerate(zip(gs["filmes"], gs["nota_media"])):
        ax.text(f + 0.6, i, f"{int(f)}  (nota média {m:.2f})", va="center", color=INK2, fontsize=8.5)
    ax.set_xlim(0, gs["filmes"].max() * 1.45)
    ax.set_title("Filmes por gênero"); ax.set_xlabel("Filmes"); ax.grid(axis="y", visible=False)
    save(fig, "04_filmes_por_genero")
    return stats


# ================================================================== 2. Avaliação offline
MODELS = {
    "Híbrido (produção)": build_query(COLLABORATIVE_BRANCH, CONTENT_BRANCH),
    "Colaborativo": build_query(COLLABORATIVE_BRANCH),
    "Conteúdo (gênero)": build_query(CONTENT_BRANCH),
    "Popularidade": POPULAR_QUERY,
    "Aleatório": None,
}


def ndcg_at_k(recs, relevant, k=K):
    dcg = sum(1 / math.log2(i + 2) for i, m in enumerate(recs[:k]) if m in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / ideal if ideal else 0.0


def evaluate(driver, ratings, movies, n_users=120, holdout=0.2):
    print("\n[2/4] Avaliação offline (holdout por usuário, transação com rollback)")
    rng = random.Random(SEED)
    liked = ratings[ratings.score >= 4].groupby("user")["movie"].apply(list)
    eligible = sorted(u for u, ms in liked.items() if len(ms) >= 5)
    sample = rng.sample(eligible, min(n_users, len(eligible)))
    popularity = ratings.groupby("movie").size()
    per_model = {m: defaultdict(list) for m in MODELS}
    rec_items = {m: Counter() for m in MODELS}

    with driver.session() as s:
        for i, uid in enumerate(sample, 1):
            ms = sorted(liked[uid]); rng.shuffle(ms)
            test = set(ms[: max(1, round(holdout * len(ms)))])
            tx = s.begin_transaction()
            try:
                tx.run("MATCH (u:User {id:$u})-[r:RATED|WATCHED]->(m:Movie) WHERE m.id IN $ids DELETE r",
                       u=uid, ids=list(test)).consume()
                seen = {r["id"] for r in tx.run("MATCH (:User {id:$u})-[:WATCHED]->(m) RETURN m.id AS id", u=uid)}
                for name, query in MODELS.items():
                    t0 = time.perf_counter()
                    if query is None:
                        recs = rng.sample([m for m in movies if m not in seen], K)
                    else:
                        recs = [r["movie_id"] for r in tx.run(query, user_id=uid, limit=K)]
                    per_model[name]["latency_ms"].append((time.perf_counter() - t0) * 1000)
                    hits = [m for m in recs if m in test]
                    per_model[name]["precision"].append(len(hits) / K)
                    per_model[name]["recall"].append(len(hits) / len(test))
                    per_model[name]["hit_rate"].append(1.0 if hits else 0.0)
                    per_model[name]["ndcg"].append(ndcg_at_k(recs, test))
                    per_model[name]["pop"].extend(popularity.get(m, 0) for m in recs)
                    rec_items[name].update(recs)
            finally:
                tx.rollback()  # nada é gravado
            if i % 30 == 0:
                print(f"  {i}/{len(sample)} usuários avaliados")

    ci = lambda v: 1.96 * statistics.stdev(v) / math.sqrt(len(v)) if len(v) > 1 else 0.0  # noqa: E731
    cat_pop = popularity.reindex(movies, fill_value=0).mean()
    table = {}
    for name, m in per_model.items():
        table[name] = {
            **{f"{k}@{K}": round(statistics.mean(m[k]), 4) for k in ("precision", "recall", "hit_rate", "ndcg")},
            **{f"{k}@{K}_ic95": round(ci(m[k]), 4) for k in ("precision", "recall", "ndcg")},
            "cobertura_catalogo": round(len(rec_items[name]) / len(movies), 3),
            "vies_popularidade": round(statistics.mean(m["pop"]) / cat_pop, 2),
            "latencia_media_ms": round(statistics.mean(m["latency_ms"]), 1),
        }
        print(f"  {name:<20} P@{K}={table[name][f'precision@{K}']:.3f}  R@{K}={table[name][f'recall@{K}']:.3f}  "
              f"nDCG={table[name][f'ndcg@{K}']:.3f}  Hit={table[name][f'hit_rate@{K}']:.2f}")

    # --- gráfico: small multiples, uma métrica por painel, IC 95%
    names = list(MODELS)[::-1]
    colors = [BLUE if n.startswith("Híbrido") else GRAY for n in names]
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4), sharey=True)
    for ax, (key, label) in zip(axes, [("precision", f"Precision@{K}"), ("recall", f"Recall@{K}"), ("ndcg", f"nDCG@{K}")]):
        vals = [table[n][f"{key}@{K}"] for n in names]
        errs = [table[n][f"{key}@{K}_ic95"] for n in names]
        ax.barh(names, vals, xerr=errs, color=colors, height=0.6, error_kw={"ecolor": INK2, "elinewidth": 1, "capsize": 3})
        for i, v in enumerate(vals):
            ax.text(v + errs[i] + max(vals) * 0.03, i, f"{v:.3f}", va="center", fontsize=8.5, color=INK2)
        ax.set_title(label); ax.set_xlim(0, max(v + e for v, e in zip(vals, errs)) * 1.35); ax.grid(axis="y", visible=False)
    fig.suptitle(f"Avaliação offline — {len(sample)} usuários, holdout de {holdout:.0%} dos filmes curtidos (barras = IC 95%)",
                 x=0.01, ha="left", fontsize=10, color=INK2)
    save(fig, "05_avaliacao_offline")
    return {"usuarios_avaliados": len(sample), "k": K, "holdout": holdout, "modelos": table}


# ================================================================== 3. Simulação de TTL
def simulate_ttl(n_users=200, hours=8, seed=SEED):
    """Simula acessos (Poisson) e invalidações por usuário para medir o efeito do TTL.

    Hipóteses: cada usuário abre a home a uma taxa λ ~ lognormal (média ≈ 10/h, alguns muito ativos)
    e gera eventos que invalidam o cache (avaliar/assistir) a uma taxa de 1,5/h.
    """
    print("\n[3/4] Simulação do cache em função do TTL")
    rng = np.random.default_rng(seed)
    horizon = hours * 3600
    lam = rng.lognormal(mean=math.log(8), sigma=0.8, size=n_users) / 3600  # req/s
    inv_rate = 1.5 / 3600
    events = []
    for u in range(n_users):
        req = np.cumsum(rng.exponential(1 / lam[u], size=int(lam[u] * horizon * 2) + 5))
        inv = np.cumsum(rng.exponential(1 / inv_rate, size=int(inv_rate * horizon * 2) + 5))
        events.append(sorted([(t, "r") for t in req if t < horizon] + [(t, "i") for t in inv if t < horizon]))

    ttls = [0, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
    out = []
    for ttl in ttls:
        hits = misses = 0
        ages = []
        for ev in events:
            cached_at = None
            for t, kind in ev:
                if kind == "i":
                    cached_at = None
                elif cached_at is not None and t - cached_at < ttl:
                    hits += 1; ages.append(t - cached_at)
                else:
                    misses += 1; cached_at = t; ages.append(0.0)
        total = hits + misses
        out.append({"ttl": ttl, "hit_ratio": round(hits / total, 4), "consultas_neo4j_hora": round(misses / hours),
                    "requisicoes_hora": round(total / hours), "idade_media_s": round(statistics.mean(ages), 1)})
    for r in out:
        print(f"  TTL {r['ttl']:>5}s  hit ratio {r['hit_ratio']:.1%}  Neo4j/h {r['consultas_neo4j_hora']:>5}  idade média {r['idade_media_s']:>6}s")

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.4))
    xs = [r["ttl"] for r in out][1:]
    axes[0].plot(xs, [r["hit_ratio"] * 100 for r in out][1:], color=BLUE, linewidth=2, marker="o", markersize=5)
    axes[0].set_title("Hit ratio x TTL"); axes[0].set_ylabel("Hit ratio (%)")
    axes[1].plot(xs, [r["idade_media_s"] for r in out][1:], color=ORANGE, linewidth=2, marker="o", markersize=5)
    axes[1].set_title("Idade média da recomendação servida x TTL"); axes[1].set_ylabel("Segundos")
    sel = next(r for r in out if r["ttl"] == 300)
    for ax, y in zip(axes, [sel["hit_ratio"] * 100, sel["idade_media_s"]]):
        ax.set_xscale("log"); ax.set_xlabel("TTL (s, escala log)")
        ax.set_xticks(xs); ax.set_xticklabels([str(x) for x in xs], fontsize=8)
        ax.axvline(300, color=INK2, linestyle="--", linewidth=1)
        ax.annotate("TTL escolhido (300 s)", (300, ax.get_ylim()[1]), xytext=(-6, -14), textcoords="offset points",
                    fontsize=8.5, color=INK2, ha="right")
        ax.minorticks_off()
    save(fig, "06_simulacao_ttl")
    return {"hipoteses": {"usuarios": n_users, "horas": hours, "acessos_por_hora_mediana": 8, "invalidacoes_por_hora": 1.5},
            "resultados": out}


# ================================================================== 4. Desempenho
def performance():
    print("\n[4/4] Estatística dos testes de carga")
    f = RESULTS / "benchmark_latest.json"
    if not f.exists():
        print("  results/benchmark_latest.json não encontrado — rode scripts.benchmark antes.")
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    res = [r for r in data["results"] if r.get("latencies_raw_ms")]
    if not res:
        print("  benchmark sem latências brutas — rode novamente scripts.benchmark.")
        return None
    colors = {"sem_cache": ORANGE, "cache_frio": YELLOW, "cache_quente": BLUE}
    labels = {"sem_cache": "Sem cache", "cache_frio": "Cache frio", "cache_quente": "Cache quente"}

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.6))
    ax = axes[0]
    for r in res:
        v = np.sort(r["latencies_raw_ms"])
        ax.plot(v, np.arange(1, len(v) + 1) / len(v) * 100, color=colors[r["scenario"]], linewidth=2, label=labels[r["scenario"]])
    ax.set_xscale("log"); ax.set_xlabel("Latência (ms, escala log)"); ax.set_ylabel("% das requisições (acumulado)")
    ax.set_title("Distribuição acumulada da latência"); ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.axhline(95, color=GRID, linewidth=1)
    ax = axes[1]
    names = [labels[r["scenario"]] for r in res]
    q = [r["neo4j_queries"] for r in res]
    ax.bar(names, q, color=[colors[r["scenario"]] for r in res], width=0.55)
    for i, v in enumerate(q):
        ax.text(i, v, f"{v:,}".replace(",", "."), ha="center", va="bottom", fontsize=9, color=INK2)
    ax.set_title(f"Consultas ao Neo4j ({res[0]['requests']:,} requisições)".replace(",", ".")); ax.grid(axis="x", visible=False)
    save(fig, "07_desempenho")

    summary = {"meta": data["meta"], "cenarios": []}
    for r in res:
        v = np.array(r["latencies_raw_ms"])
        summary["cenarios"].append({
            "cenario": r["scenario"], "throughput_rps": r["throughput_rps"],
            "media": round(float(v.mean()), 1), "desvio": round(float(v.std()), 1),
            "p50": round(float(np.percentile(v, 50)), 1), "p90": round(float(np.percentile(v, 90)), 1),
            "p95": round(float(np.percentile(v, 95)), 1), "p99": round(float(np.percentile(v, 99)), 1),
            "cv": round(float(v.std() / v.mean()), 2),
            "hits": r["cache_hits"], "misses": r["cache_misses"], "hit_ratio": r["hit_ratio"],
            "neo4j_queries": r["neo4j_queries"],
            # Lei de Little: L = λ·W  -> concorrência efetiva estimada
            "little_L": round(r["throughput_rps"] * float(v.mean()) / 1000, 1),
        })
        print(f"  {r['scenario']:<13} média {v.mean():7.1f} ms  p95 {np.percentile(v, 95):7.1f}  CV {v.std() / v.mean():.2f}")
    return summary


# ================================================================== relatório
def to_markdown(rep):
    e, a = rep["eda"], rep["avaliacao"]
    lines = ["# Relatório de análise de dados — StreamRec", "",
             f"Gerado em {rep['gerado_em']} por `scripts/analise_dados.py`.", "",
             "## 1. Análise exploratória", "",
             f"- {e['usuarios']} usuários, {e['filmes']} filmes, {e['generos']} gêneros",
             f"- {e['assistidos']} relações WATCHED e {e['avaliacoes']} RATED (taxa de avaliação {e['taxa_avaliacao']:.0%})",
             f"- Matriz usuário x filme: densidade {e['densidade_matriz']:.2%}, esparsidade {e['esparsidade_matriz']:.2%}",
             f"- Nota média {e['nota_media']} (desvio {e['nota_desvio']})",
             f"- 20% dos filmes concentram {e['share_top20_filmes']:.0%} das avaliações", "",
             "![](graficos/01_distribuicao_notas.png)", "![](graficos/03_popularidade_filmes.png)", "",
             f"## 2. Avaliação offline (K={a['k']}, {a['usuarios_avaliados']} usuários, holdout {a['holdout']:.0%})", "",
             "| Modelo | Precision@10 | Recall@10 | nDCG@10 | HitRate@10 | Cobertura | Viés pop. | Latência (ms) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for n, m in a["modelos"].items():
        lines.append(f"| {n} | {m['precision@10']:.3f} ± {m['precision@10_ic95']:.3f} | {m['recall@10']:.3f} | "
                     f"{m['ndcg@10']:.3f} | {m['hit_rate@10']:.2f} | {m['cobertura_catalogo']:.0%} | {m['vies_popularidade']}x | {m['latencia_media_ms']} |")
    lines += ["", "![](graficos/05_avaliacao_offline.png)", "", "## 3. Simulação de TTL", "",
              "| TTL (s) | Hit ratio | Consultas Neo4j/h | Idade média (s) |", "|---:|---:|---:|---:|"]
    for r in rep["ttl"]["resultados"]:
        lines.append(f"| {r['ttl']} | {r['hit_ratio']:.1%} | {r['consultas_neo4j_hora']} | {r['idade_media_s']} |")
    lines += ["", "![](graficos/06_simulacao_ttl.png)", ""]
    if rep.get("desempenho"):
        lines += ["## 4. Testes de carga", "", "| Cenário | req/s | Média | Desvio | p50 | p95 | p99 | CV | Consultas Neo4j |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for c in rep["desempenho"]["cenarios"]:
            lines.append(f"| {c['cenario']} | {c['throughput_rps']} | {c['media']} | {c['desvio']} | {c['p50']} | {c['p95']} | {c['p99']} | {c['cv']} | {c['neo4j_queries']} |")
        lines += ["", "![](graficos/07_desempenho.png)", ""]
    return "\n".join(lines)


def main():
    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    driver.verify_connectivity()
    ratings, watched, movie_genres, users, movies = load_data(driver)
    rep = {"gerado_em": time.strftime("%Y-%m-%d %H:%M:%S")}
    rep["eda"] = eda(ratings, watched, movie_genres, users, movies)
    rep["avaliacao"] = evaluate(driver, ratings, movies)
    rep["ttl"] = simulate_ttl()
    rep["desempenho"] = performance()
    driver.close()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "analise_dados.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    (RESULTS / "analise_dados.md").write_text(to_markdown(rep), encoding="utf-8")
    print("\nRelatório salvo em results/analise_dados.md e results/analise_dados.json")


if __name__ == "__main__":
    main()
