# フェーズB 初期設定（1回だけ実行）
# 1) Git を入れる  2) リポジトリを Documents\prereg-technical に置く  3) 既存の生データを移す
# 4) 必要なPythonパッケージ  5) タスクスケジューラに毎日 12:30 の実行を登録
$ErrorActionPreference = "Stop"
$docs = Join-Path $env:USERPROFILE "Documents"
$repo = Join-Path $docs "prereg-technical"
$old  = Join-Path $docs "prereg-research"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
if (-not (Test-Path $repo)) { git clone https://github.com/nukichi666/prereg-technical.git $repo }
Set-Location $repo
git config user.name "nukichi666"
git config user.email "nukichi666@users.noreply.github.com"

if ((Test-Path "$old\raw") -and -not (Test-Path "$repo\raw")) { Copy-Item "$old\raw" "$repo\raw" -Recurse }
python -m pip install --quiet -r requirements.txt
# ots は任意（Python 3.14 では入らないことがある。その場合は週1回ブラウザで代替）
$ErrorActionPreference = "Continue"
python -m pip install --quiet opentimestamps-client *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "ots は入りませんでした（任意。週1回のタイムスタンプはブラウザで代替）" -ForegroundColor Yellow }
$ErrorActionPreference = "Stop"

$act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\daily_run.ps1`"" -WorkingDirectory $repo
$trg = New-ScheduledTaskTrigger -Daily -At 12:30
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName "prereg-phase-b" -Action $act -Trigger $trg -Settings $set -Force | Out-Null
Write-Host "設定完了。テストとして今すぐ1回実行します（初回はデータ取得に時間がかかります）" -ForegroundColor Cyan
powershell -NoProfile -ExecutionPolicy Bypass -File "$repo\daily_run.ps1"
