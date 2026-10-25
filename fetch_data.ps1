# データ取得スクリプト（このフォルダで実行）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== pandas ==" -ForegroundColor Cyan
python -m pip install --quiet pandas

Write-Host "== Binance 接続テスト ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force .\raw\binance | Out-Null
$base = "https://data.binance.vision/data/spot/monthly/klines"
Invoke-WebRequest "$base/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip" -OutFile .\raw\binance\test.zip
Remove-Item .\raw\binance\test.zip
Write-Host "OK"

Write-Host "== Binance BTC/ETH 1分足（2023-10〜2026-09）==" -ForegroundColor Cyan
foreach ($s in "BTCUSDT","ETHUSDT") {
  $d = Get-Date "2023-10-01"
  while ($d -lt (Get-Date "2026-10-01")) {
    $f = "$s-1m-$($d.ToString('yyyy-MM')).zip"
    if (-not (Test-Path ".\raw\binance\$($f -replace '\.zip$','.csv')")) {
      Write-Host "  $f"
      Invoke-WebRequest "$base/$s/1m/$f" -OutFile ".\raw\binance\$f"
      Expand-Archive ".\raw\binance\$f" -DestinationPath ".\raw\binance" -Force
      Remove-Item ".\raw\binance\$f"
    }
    $d = $d.AddMonths(1)
  }
}

Write-Host "== Dukascopy XAUUSD/USDJPY 1分足 bid/ask ==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force .\raw\dukascopy | Out-Null
foreach ($i in "xauusd","usdjpy") { foreach ($p in "bid","ask") {
  Write-Host "  $i $p"
  npx --yes dukascopy-node -i $i -from 2023-10-01 -to 2026-10-01 -t m1 -p $p -f csv -dir .\raw\dukascopy
}}
Get-ChildItem .\raw\dukascopy | Select-Object Name, Length

Write-Host "== 再集計 ==" -ForegroundColor Cyan
python resample.py
