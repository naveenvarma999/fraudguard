# Run from PowerShell: .\scripts\Deploy-Docker.ps1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$previousKey = $env:API_KEY
$previousMonitorKey = $env:MONITOR_KEY
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker is not installed or is not on PATH. Install Docker Desktop and reopen PowerShell.'
    }
    Write-Host 'Checking the Docker engine...'
    docker version --format '{{.Server.Version}}'
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot reach Docker. Open Docker Desktop from Start, wait for the engine to run, and retry this script in your own PowerShell window.'
    }
    $engineType = docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or "$engineType".Trim() -ne 'linux') {
        throw 'This image needs Linux containers. Switch Docker Desktop to Linux containers, then retry.'
    }
    $secretFile = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $secretFile)) {
        $keyBytes = New-Object byte[] 32
        $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $generator.GetBytes($keyBytes) } finally { $generator.Dispose() }
        $newKey = [Convert]::ToBase64String($keyBytes)
        # CreateNew prevents concurrent launches from replacing an existing key.
        $stream = [System.IO.File]::Open($secretFile, [System.IO.FileMode]::CreateNew)
        try {
            $bytes = [System.Text.Encoding]::ASCII.GetBytes("API_KEY=$newKey`n")
            $stream.Write($bytes, 0, $bytes.Length)
        } finally { $stream.Dispose() }
        Write-Host 'Created the API key in .env. Keep this file private; it is excluded from Git and Docker builds.'
    }
    $keyLine = @(Get-Content -LiteralPath $secretFile | Where-Object { $_ -match '^API_KEY=' })
    if ($keyLine.Count -ne 1) {
        throw 'The .env file must contain exactly one API_KEY= value. Existing configuration was preserved.'
    }
    $env:API_KEY = $keyLine[0].Substring(8).Trim()
    if ($env:API_KEY.Length -lt 16) {
        throw 'The API_KEY in .env must contain at least 16 characters.'
    }
    $monitorLines = @(Get-Content -LiteralPath $secretFile | Where-Object { $_ -match '^MONITOR_KEY=' })
    if ($monitorLines.Count -eq 0) {
        $monitorBytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($monitorBytes) } finally { $rng.Dispose() }
        Add-Content -LiteralPath $secretFile -Encoding ascii -Value ("`nMONITOR_KEY=" + [Convert]::ToBase64String($monitorBytes))
        $monitorLines = @(Get-Content -LiteralPath $secretFile | Where-Object { $_ -match '^MONITOR_KEY=' })
    }
    if ($monitorLines.Count -ne 1) { throw 'Exactly one MONITOR_KEY is required in .env.' }
    $env:MONITOR_KEY = $monitorLines[0].Substring(12).Trim()
    if ($env:MONITOR_KEY.Length -lt 16 -or $env:MONITOR_KEY -eq $env:API_KEY) { throw 'MONITOR_KEY must be distinct and at least 16 characters.' }
    $mfaLines = @(Get-Content -LiteralPath $secretFile | Where-Object { $_ -match '^MFA_ENCRYPTION_KEY=' })
    if ($mfaLines.Count -eq 0) {
        $mfaBytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($mfaBytes) } finally { $rng.Dispose() }
        Add-Content -LiteralPath $secretFile -Encoding ascii -Value ("`nMFA_ENCRYPTION_KEY=" + [Convert]::ToBase64String($mfaBytes).Replace('+','-').Replace('/','_'))
    }
    Write-Host 'Building the image and starting FraudGuard. The first build may take several minutes...'
    docker compose up --build --detach --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) {
        throw 'Build or startup failed. Run docker compose logs --tail 100 api from the project folder to inspect startup errors.'
    }
    $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready' -TimeoutSec 10
    if ($ready.status -ne 'ready') { throw 'The service did not report ready.' }
    $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/v1/predict' -Method Post `
        -Headers @{'X-API-Key' = $env:API_KEY} -ContentType 'application/json' `
        -InFile (Join-Path $projectRoot 'artifacts\benchmark\example_request.json') -TimeoutSec 15
    if (-not $result.model_version -or @($result.predictions).Count -ne 1) {
        throw 'The prediction smoke test returned an unexpected response.'
    }
    docker compose ps
    Write-Host "Prediction verified. Model version: $($result.model_version)"
    Write-Host 'FraudGuard is running: http://localhost:8000/'
    Write-Host 'The API key is stored in .env. Stop the service with: docker compose down'
} finally {
    $env:API_KEY = $previousKey
    $env:MONITOR_KEY = $previousMonitorKey
    Pop-Location
}
