"""Teste de carga alternativo com interface web (Locust).

    locust -f scripts/locustfile.py --host http://localhost:8000
    # abra http://localhost:8089
"""
import random

from locust import HttpUser, between, task


class StreamingUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(10)
    def recommendations_cached(self):
        uid = f"u{random.randint(1, 200)}"
        self.client.get(f"/users/{uid}/recommendations?limit=10", name="/recommendations [cache]")

    @task(2)
    def recommendations_no_cache(self):
        uid = f"u{random.randint(1, 200)}"
        self.client.get(f"/users/{uid}/recommendations?limit=10&use_cache=false",
                        name="/recommendations [sem cache]")

    @task(1)
    def rate_movie(self):
        uid = f"u{random.randint(1, 200)}"
        self.client.post(f"/users/{uid}/ratings",
                         json={"movie_id": f"m{random.randint(1, 300)}", "score": random.randint(1, 5)},
                         name="/ratings")
