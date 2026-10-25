# フェーズB 日次実行：新しい日のデータ取得 → 再集計 → 台帳追記 → GitHubへ反映
# タスクスケジューラから毎日実行する。途中で失敗しても、次回に未取得の日から再開する。
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot "daily_run.log"
function Log($m) { $t = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); Write-Host "$t $m"; Add-Content -Path $log -Value "$t $m" -Encoding UTF8 }
function Run($exe, $arg) { & $exe $arg 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $log -Append -Encoding utf8; return $LASTEXITCODE }

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
if ((Run "python" "resample.py") -ne 0) { Log "resample.py FAILED"; exit 1 }
if ((Run "python" "log_signals.py") -ne 0) { Log "log_signals.py FAILED"; exit 1 }
Log (Get-Content $log -Tail 3 | Select-String "新規シグナル" | Select-Object -Last 1)

# --- GitHubへ反映（台帳・訂正・実行記録のみ）
# 存在するものだけ add（1つでも存在しないパスがあると git add 全体が失敗するため）
foreach ($f in "signals","corrections.csv","run_log.csv") { if (Test-Path $f) { git add $f 2>$null } }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  git commit -m "phase B ledger $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd'))" | Out-Null
  git push origin main 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
  Log "git push exit=$LASTEXITCODE"
}

# --- 日曜（UTC）は台帳ファイルに OpenTimestamps を付ける（stamp_ots.py。公式 ots コマンドは使わない）
if ((Get-Date).ToUniversalTime().DayOfWeek -eq "Sunday") {
  $targets = @(Get-ChildItem .\signals\*.csv -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path "$($_.FullName).ots") } | ForEach-Object { $_.FullName })
  foreach ($f in "run_log.csv","corrections.csv") { if (Test-Path $f) { $targets += (Resolve-Path $f).Path } }
  if ($targets.Count -gt 0) {
    if ((Run "python" (@("stamp_ots.py") + $targets)) -eq 0) {
      foreach ($f in "signals","run_log.csv.ots","corrections.csv.ots") { if (Test-Path $f) { git add $f 2>$null } }
      git diff --cached --quiet
      if ($LASTEXITCODE -ne 0) { git commit -m "weekly OpenTimestamps" | Out-Null; git push origin main 2>&1 | Out-File -FilePath $log -Append -Encoding utf8 }
      Log "weekly ots done"
    } else { Log "weekly ots FAILED (次回の日曜に再試行)" }
  }
}
Log "done"
