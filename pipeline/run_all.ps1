# Process all CCTV clips and ingest into the API.
# Usage: .\pipeline\run_all.ps1 [-Overwrite]

param(
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$CamDir = "D:\purplle\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage"
$StoreId = "STORE_BLR_002"
$EventsFile = "pipeline\events_output.jsonl"

if ($Overwrite -and (Test-Path $EventsFile)) {
    Remove-Item $EventsFile -Force
    Write-Host "Cleared $EventsFile"
}

$overwriteFlag = if ($Overwrite) { "--overwrite" } else { "" }
$first = $true

foreach ($n in 1..5) {
    $video = Join-Path $CamDir "CAM $n.mp4"
    if (-not (Test-Path $video)) {
        Write-Warning "Skip missing: $video"
        continue
    }
    Write-Host "`n=== Processing CAM $n ===" -ForegroundColor Cyan
    if ($first -and $Overwrite) {
        python pipeline/detect.py --video $video --store_id $StoreId --overwrite
        $first = $false
    }
    else {
        python pipeline/detect.py --video $video --store_id $StoreId
    }
}

$lines = (Get-Content $EventsFile | Where-Object { $_.Trim() }).Count
Write-Host "`nTotal events in $EventsFile : $lines" -ForegroundColor Green

Write-Host "`nIngesting to API..." -ForegroundColor Cyan
python pipeline/ingest_events.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Ingest failed. If using Docker, rebuild first:" -ForegroundColor Yellow
    Write-Host "  docker compose up --build -d" -ForegroundColor Yellow
    Write-Host "Or ingest into local API: python -m uvicorn app.main:app --port 8000" -ForegroundColor Yellow
}

Write-Host "`nDone. Open http://localhost:8000/" -ForegroundColor Green
