$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Run([scriptblock]$Action) { & $Action; if ($LASTEXITCODE -ne 0) { throw "Command failed: $LASTEXITCODE" } }
Run { docker info }
Run { docker run --rm hello-world }
New-Item -ItemType Directory -Force results | Out-Null
function Check-Api {
    $base = 'http://localhost:8080'
    for ($i=0;$i -lt 90;$i++) { try { $h=Invoke-RestMethod "$base/health" -TimeoutSec 3; if ($h.status -eq 'ok') { break } } catch {}; Start-Sleep 2 }
    $h=Invoke-RestMethod "$base/health"
    $h | ConvertTo-Json
    foreach ($path in @('/','/products/1','/products?category_id=1','/products/search?q=abc','/stats/top-products')) { $r=Invoke-WebRequest -UseBasicParsing "$base$path"; Write-Host "$path HTTP $($r.StatusCode)" }
    $o=Invoke-RestMethod "$base/orders" -Method Post -ContentType 'application/json' -Body '{"product_id":1,"quantity":1}'
    $o | ConvertTo-Json
    Invoke-RestMethod "$base/orders/$($o.id)" | ConvertTo-Json
    try { Invoke-WebRequest -UseBasicParsing "$base/orders/999999999" | Out-Null; throw 'Expected 404' } catch { if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }; Write-Host 'Missing order: HTTP 404' }
}
function Measure([string]$Name,[bool]$Failure=$false) {
    $log=Join-Path $PSScriptRoot "results/$Name.txt"
    $err=Join-Path $PSScriptRoot "results/$Name.stderr.txt"
    $p=Start-Process docker -ArgumentList @('compose','run','--rm','k6','run','/scripts/load.js') -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError $err
    $start=Get-Date; $stopped=$false; $restarted=$false
    try {
        while (!$p.HasExited) {
            $elapsed=((Get-Date)-$start).TotalSeconds
            if ($Failure -and !$stopped -and $elapsed -ge 20) { docker compose stop app2; $stopped=$true; Invoke-RestMethod 'http://localhost:8080/health' | ConvertTo-Json | Tee-Object -FilePath results/failover-health.txt }
            if ($Failure -and $stopped -and !$restarted -and $elapsed -ge 40) { docker compose start app2; $restarted=$true }
            docker stats --no-stream | Out-File "results/$Name-stats.txt" -Append
            Start-Sleep 2
            $p.Refresh()
        }
    } finally { if ($Failure) { docker compose start app2 } }
    Get-Content $log; Get-Content $err
    Write-Host "k6 exit code: $($p.ExitCode)"
    if ($p.ExitCode -notin @(0,99)) { throw 'k6 execution failed' }
}
Run { git switch --detach lab-1 }
Run { docker compose config -q }
Run { docker compose up -d --build --remove-orphans }
docker compose ps
Run { docker compose exec -T postgres psql -U shop -d shop -c 'SELECT (SELECT count(*) FROM products) AS products, (SELECT count(*) FROM orders) AS orders;' }
Check-Api | Tee-Object -FilePath results/lab-1-api.txt
Measure 'lab-1'
Run { git switch main }
Run { docker compose config -q }
Run { docker compose up -d --build --remove-orphans }
Check-Api | Tee-Object -FilePath results/lab-2-api.txt
1..15 | ForEach-Object { Invoke-RestMethod 'http://localhost:8080/' } | Tee-Object -FilePath results/lab-2-workers.txt
Measure 'lab-2-failover' $true
Measure 'lab-2'
Run { git log --oneline --decorate }
Write-Host 'Results are in results/. Capture real screenshots and fill the measurement journal.'
