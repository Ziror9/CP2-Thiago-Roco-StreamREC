#!/usr/bin/env bash
# Teste de carga com ApacheBench (imagem oficial httpd), COM x SEM cache.
# Uso: ./scripts/ab_compare.sh [N=2000] [C=50] [USER=u1]
N=${1:-2000}; C=${2:-50}; USER_ID=${3:-u1}
NET=$(docker inspect streamrec-api --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
mkdir -p results; OUT=results/ab_latest.txt
echo "ApacheBench n=$N c=$C usuario=$USER_ID $(date '+%F %T')" > "$OUT"
docker run --rm --network "$NET" httpd:alpine ab -q -n 1 "http://api:8000/users/$USER_ID/recommendations?limit=10" >/dev/null
for q in use_cache=true use_cache=false; do
  echo -e "\n===== $q =====" | tee -a "$OUT"
  docker run --rm --network "$NET" httpd:alpine ab -q -k -n "$N" -c "$C" \
    "http://api:8000/users/$USER_ID/recommendations?limit=10&$q" 2>/dev/null |
    grep -E "Complete requests|Failed|Requests per|Time per request:.*\(mean\)$|  50%|  95%|  99%|100%" | tee -a "$OUT"
done
echo -e "\nResultado salvo em $OUT"
