# Clone finssentials_v2 → finssentials_v2_sandbox for the parallel reporting-v2 sandbox stack.
# Source is READ-only (pg_dump). Use after the main v2 DB exists (see clone-db-v2.ps1).
#
#   $env:DB_PASSWORD = "<postgres-password>"; .\scripts\clone-db-v2-sandbox.ps1
$ErrorActionPreference = "Stop"

$DbHost = if ($env:DB_HOST) { $env:DB_HOST } else { "localhost" }
$DbPort = if ($env:DB_PORT) { $env:DB_PORT } else { "5432" }
$DbUser = if ($env:DB_USER) { $env:DB_USER } else { "postgres" }
$SrcDb  = "finssentials_v2"
$DstDb  = "finssentials_v2_sandbox"

if (-not $env:DB_PASSWORD) {
  $root = Split-Path -Parent $PSScriptRoot
  $backend = Join-Path $root "backend"
  $dotenvLoader = Join-Path $backend ".venv\Scripts\python.exe"
  $envCandidates = @(
    (Join-Path $backend ".env"),
    (Join-Path (Split-Path -Parent $root) "Finssentials_GDPDU\backend\.env")
  )
  foreach ($envFile in $envCandidates) {
    if (-not (Test-Path $envFile)) { continue }
    $env:DB_PASSWORD = & $dotenvLoader -c "from pathlib import Path; from dotenv import dotenv_values; print(dotenv_values(Path(r'$envFile')).get('DB_PASSWORD','') or '')"
    if ($env:DB_PASSWORD) { break }
  }
}
if (-not $env:DB_PASSWORD) { throw "DB_PASSWORD not set." }
$env:PGPASSWORD = $env:DB_PASSWORD

function Resolve-PgTool($name) {
  if ($env:PgBin) { $p = Join-Path $env:PgBin "$name.exe"; if (Test-Path $p) { return $p } }
  $cmd = Get-Command "$name.exe" -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $cands = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\$name.exe","C:\Program Files\edb\*\bin\$name.exe" -ErrorAction SilentlyContinue
  if ($cands) { return ($cands | Sort-Object FullName -Descending | Select-Object -First 1).FullName }
  throw "$name not found. Set `$env:PgBin to your PostgreSQL bin directory."
}

$pgDump = Resolve-PgTool "pg_dump"
$psql   = Resolve-PgTool "psql"
$pgRestore = Resolve-PgTool "pg_restore"
$common = @("-h", $DbHost, "-p", $DbPort, "-U", $DbUser)
$dumpFile = Join-Path $env:TEMP "finssentials_v2_sandbox_clone.dump"

Write-Host "Dumping $SrcDb -> $dumpFile..."
& $pgDump @common -F c -f $dumpFile $SrcDb
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed ($LASTEXITCODE)" }

Write-Host "Recreating $DstDb..."
& $psql @common -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DstDb;"
& $psql @common -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DstDb;"

Write-Host "Restoring into $DstDb..."
& $pgRestore @common -d $DstDb --no-owner --no-privileges $dumpFile
$tables = & $psql @common -d $DstDb -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
Write-Host "Clone done. $DstDb public tables: $tables"
Remove-Item $dumpFile -ErrorAction SilentlyContinue
