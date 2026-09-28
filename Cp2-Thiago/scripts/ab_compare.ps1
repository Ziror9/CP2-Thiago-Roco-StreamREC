# Teste de carga com ApacheBench (imagem oficial httpd), COM x SEM cache.
# Uso (na raiz do projeto, com a stack no ar):  .\scripts\ab_compare.ps1 -N 2000 -C 50
param([int]$N = 2000, [int]$C = 50, [string]$User = "u1")

$network = (docker inspect streamrec-api --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
New-Item -ItemType Directory -Force results | Out-Null
$out = "results/ab_latest.txt"
"ApacheBench  n=$N  c=$C  usuario=$User  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File $out -Encoding utf8

# aquece o cache do usuário
docker run --rm --network $network httpd:alpine ab -q -n 1 "http://api:8000/users/$User/recommendations?limit=10" | Out-Null

foreach ($q in "use_cache=true", "use_cache=false") {
    "`n===== $q =====" | Tee-Object -FilePath $out -Append
    docker run --rm --network $network httpd:alpine ab -q -k -n $N -c $C "http://api:8000/users/$User/recommendations?limit=10&$q" 2>$null |
        Select-String "Complete requests|Failed|Requests per|Time per request:.*\(mean\)$|  50%|  95%|  99%|100%" |
        ForEach-Object { $_.Line } | Tee-Object -FilePath $out -Append
}
"`nResultado salvo em $out"
