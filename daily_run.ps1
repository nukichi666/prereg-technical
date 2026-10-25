# フェーズB 日次実行：新しい日のデータ取得 → 再集計 → 台帳追記 → GitHubへ反映
# タスクスケジューラから毎日実行する。途中で失敗しても、次回に未取得の日から再開する。
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot "daily_run.log"
function Log($m) { $t = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); "$t $m" | Tee-Object -FilePath $log -Append }

$first = [datetime]"2026-10-01"
$yesterday = (Get-Date).ToUniversalTime().Date.AddDays(-1)
New-Item -ItemType Directory -Force .\raw\dukascopy\.done, .\raw\binance | Out-Null

# --- Dukascopy（UTCの1日ずつ。取得済みの日は .done に印を残す）
for ($d = $first; $d -le $yesterday; $d = $d.AddDays(1)) {
  $from = $d.ToString("yyyy-MM-dd"); $to = $d.AddDays(1).ToString("yyyy-MM-dd")
  foreach ($i in "xauusd","usdjpy") { foreach ($p in "bid","ask") {
    $mark = ".\raw\dukascopy\.done\$i-$p-$from"
    if (Test-Path $mark) { continue }
    npx.cmd --yes dukascopy-node -i $i -from $from -to $to -t m1 -p $p -f csv -dir .\raw\dukascopy | Out-Null
    if ($LASTEXITCODE -eq 0) { New-Item -ItemType File $mark | Out-Null; Log "dukascopy $i $p $from OK" }
    else { Log "dukascopy $i $p $from FAILED (次回再試行)" }
  }}
}

# --- Binance（日次ファイル。公開前の日は次回に再試行）
$base = "https://data.binance.vision/data/spot/daily/klines"
for ($d = $first; $d -le $yesterday; $d = $d.AddDays(1)) {
  $ds = $d.ToString("yyyy-MM-dd")
  foreach ($s in "BTCUSDT","ETHUSDT") {
    if (Test-Path ".\raw\binance\$s-1m-$ds.csv") { continue }
    $zip = ".\raw\binance\$s-1m-$ds.zip"
    try {
      Invoke-WebRequest "$base/$s/1m/$s-1m-$ds.zip" -OutFile $zip -ErrorAction Stop
      Expand-Archive $zip -DestinationPath .\raw\binance -Force; Remove-Item $zip
      Log "binance $s $ds OK"
    } catch { Log "binance $s $ds 未公開または失敗 (次回再試行)"; Remove-Item $zip -ErrorAction SilentlyContinue }
  }
}

# --- 再集計と台帳追記
python resample.py *>> $log
python log_signals.py *>> $log
if ($LASTEXITCODE -ne 0) { Log "log_signals.py FAILED"; exit 1 }

# --- GitHubへ反映（台帳・訂正・実行記録のみ）
git add signals corrections.csv run_log.csv 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  git commit -m "phase B ledger $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd'))" | Out-Null
  git push origin main *>> $log
  Log "git push exit=$LASTEXITCODE"
}

# --- 日曜（UTC）は今週分のファイルに OpenTimestamps を付ける（ots コマンドがある場合）
if ((Get-Date).ToUniversalTime().DayOfWeek -eq "Sunday" -and (Get-Command ots -ErrorAction SilentlyContinue)) {
  Get-ChildItem .\signals\*.csv, .\run_log.csv, .\corrections.csv -ErrorAction SilentlyContinue |
    Where-Object { -not (Test-Path "$($_.FullName).ots") -or $_.Name -in "run_log.csv","corrections.csv" } |
    ForEach-Object { Remove-Item "$($_.FullName).ots" -ErrorAction SilentlyContinue; ots stamp $_.FullName }
  git add signals\*.ots *.ots 2>$null
  git commit -m "weekly OpenTimestamps" | Out-Null; git push origin main *>> $log
  Log "weekly ots done"
}
Log "done"
