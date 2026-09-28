"""Teste de desempenho: compara a API SEM cache x COM cache sob concorrência.

Cenários executados (mesma carga em todos):
  1. sem_cache   -> use_cache=false  (toda requisição vai ao Neo4j)
  2. cache_frio  -> cache limpo antes; as primeiras requisições de cada usuário são MISS
  3. cache_quente-> cache já populado; praticamente 100% HIT

Uso:
    python -m scripts.benchmark --requests 2000 --concurrency 50 --users 200
Os resultados são salvos em results/benchmark_<data>.json e results/benchmark_latest.md
"""
import argparse
import asyncio
import json
import random
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    data = sorted(data)
    k = (len(data) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(data) - 1)
    return data[f] + (data[c] - data[f]) * (k - f)


async def run_scenario(client: httpx.AsyncClient, name: str, user_ids: list[str],
                       total: int, concurrency: int, use_cache: bool, limit: int) -> dict:
    await client.post("/cache/stats/reset")
    latencies: list[float] = []
    status: dict[str, int] = {}
    cache_hdr: dict[str, int] = {}
    sem = asyncio.Semaphore(concurrency)
    rng = random.Random(7)  # mesma sequência de usuários em todos os cenários
    plan = [rng.choice(user_ids) for _ in range(total)]

    async def one(uid: str):
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.get(f"/users/{uid}/recommendations",
                                     params={"limit": limit, "use_cache": str(use_cache).lower()})
            except httpx.HTTPError as exc:
                status[type(exc).__name__] = status.get(type(exc).__name__, 0) + 1
                return
            latencies.append((time.perf_counter() - t0) * 1000)
            status[str(r.status_code)] = status.get(str(r.status_code), 0) + 1
            h = r.headers.get("X-Cache", "-")
            cache_hdr[h] = cache_hdr.get(h, 0) + 1

    t_start = time.perf_counter()
    await asyncio.gather(*(one(u) for u in plan))
    elapsed = time.perf_counter() - t_start
    stats = (await client.get("/cache/stats")).json()

    result = {
        "scenario": name,
        "requests": total,
        "concurrency": concurrency,
        "duration_s": round(elapsed, 2),
        "throughput_rps": round(total / elapsed, 1),
        "latency_ms": {
            "min": round(min(latencies), 2),
            "avg": round(statistics.mean(latencies), 2),
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "p99": round(percentile(latencies, 99), 2),
            "max": round(max(latencies), 2),
        },
        "status_codes": status,
        "x_cache": cache_hdr,
        "cache_hits": stats["hits"],
        "cache_misses": stats["misses"],
        "hit_ratio": stats["hit_ratio"],
        "neo4j_queries": stats["neo4j_queries"],
        "latencies_raw_ms": [round(x, 2) for x in latencies],  # usado na análise estatística
    }
    lat = result["latency_ms"]
    print(f"  {name:<13} {result['throughput_rps']:>8} req/s | avg {lat['avg']:>8} ms | "
          f"p95 {lat['p95']:>8} ms | p99 {lat['p99']:>8} ms | HIT {stats['hits']:>5} | "
          f"MISS {stats['misses']:>5} | consultas Neo4j {stats['neo4j_queries']:>5}")
    return result


def to_markdown(results: list[dict], meta: dict) -> str:
    base = results[0]
    lines = [
        f"# Resultado do benchmark ({meta['timestamp']})",
        "",
        f"- Requisições por cenário: **{meta['requests']}** | Concorrência: **{meta['concurrency']}** "
        f"| Usuários distintos: **{meta['users']}** | limit={meta['limit']}",
        "",
        "| Cenário | Throughput (req/s) | Média (ms) | p50 (ms) | p95 (ms) | p99 (ms) | HIT | MISS | Hit ratio | Consultas Neo4j |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        l = r["latency_ms"]
        lines.append(f"| {r['scenario']} | {r['throughput_rps']} | {l['avg']} | {l['p50']} | {l['p95']} | "
                     f"{l['p99']} | {r['cache_hits']} | {r['cache_misses']} | {r['hit_ratio']:.0%} | {r['neo4j_queries']} |")
    lines.append("")
    for r in results[1:]:
        speed = r["throughput_rps"] / base["throughput_rps"] if base["throughput_rps"] else 0
        lat_red = 1 - r["latency_ms"]["avg"] / base["latency_ms"]["avg"] if base["latency_ms"]["avg"] else 0
        q_red = 1 - r["neo4j_queries"] / base["neo4j_queries"] if base["neo4j_queries"] else 0
        lines.append(f"- **{r['scenario']}** vs sem_cache: throughput **{speed:.1f}x**, "
                     f"latência média **-{lat_red:.0%}**, consultas ao Neo4j **-{q_red:.0%}**")
    return "\n".join(lines) + "\n"


async def main(args):
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60,
                                 limits=httpx.Limits(max_connections=args.concurrency * 2)) as client:
        (await client.get("/health")).raise_for_status()
        user_ids = [f"u{i}" for i in range(1, args.users + 1)]
        print(f"Benchmark: {args.requests} req/cenário, concorrência {args.concurrency}, {args.users} usuários\n")

        # aquecimento (JIT do Neo4j, pools de conexão)
        await asyncio.gather(*(client.get(f"/users/{u}/recommendations", params={"use_cache": "false"})
                               for u in user_ids[:20]))

        results = [await run_scenario(client, "sem_cache", user_ids, args.requests, args.concurrency, False, args.limit)]
        await client.delete("/cache")
        results.append(await run_scenario(client, "cache_frio", user_ids, args.requests, args.concurrency, True, args.limit))
        results.append(await run_scenario(client, "cache_quente", user_ids, args.requests, args.concurrency, True, args.limit))

    meta = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "requests": args.requests,
            "concurrency": args.concurrency, "users": args.users, "limit": args.limit}
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = json.dumps({"meta": meta, "results": results}, indent=2, ensure_ascii=False)
    (RESULTS_DIR / f"benchmark_{stamp}.json").write_text(payload, encoding="utf-8")
    (RESULTS_DIR / "benchmark_latest.json").write_text(payload, encoding="utf-8")
    md = to_markdown(results, meta)
    (RESULTS_DIR / "benchmark_latest.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"Resultados salvos em {RESULTS_DIR}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--requests", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--users", type=int, default=200)
    p.add_argument("--limit", type=int, default=10)
    asyncio.run(main(p.parse_args()))
