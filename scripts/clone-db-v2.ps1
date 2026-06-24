# Clone the live "Finssentials" database into "Finssentials_v2" for the parallel
# reporting-v2 stack. The live DB is only READ (pg_dump); v2 is dropped & recreated.
#
# Requires PostgreSQL client tools (pg_dump, psql, dropdb, createdb) on PATH, or set $PgBin.
# DB_PASSWORD must be set (used for both source read and target write):
#   $env:DB_PASSWORD = "<postgres-password>"; .\scripts\clone-db-v2.ps1
$ErrorActionPreference = "Stop"

$DbHost = if ($env:DB_HOST) { $env:DB_HOST } else { "localhost" }
$DbPort = if ($env:DB_PORT) { $env:DB_PORT } else { "5432" }
$DbUser = if ($env:DB_USER) { $env:DB_USER } else { "postgres" }
$SrcDb  = if ($env:DB_NAME) { $env:DB_NAME } else { "Finssentials" }
$DstDb  = "finssentials_v2"   # lowercase → no SQL-identifier quoting needed; libpq passes dbname literally

if (-not $env:DB_PASSWORD) { throw "DB_PASSWORD not set." }
$env:PGPASSWORD = $env:DB_PASSWORD

# Resolve client tools (PATH or common EDB/PostgreSQL bin dirs)
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

$common = @("-h", $DbHost, "-p", $DbPort, "-U", $DbUser)
$dumpFile = Join-Path $env:TEMP "finssentials_clone.dump"

Write-Host "Dumping $SrcDb -> $dumpFile (read-only on live DB)..."
& $pgDump @common -F c -f $dumpFile $SrcDb
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed ($LASTEXITCODE)" }

Write-Host "Recreating $DstDb..."
& $psql @common -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DstDb;"
& $psql @common -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DstDb;"

Write-Host "Restoring into $DstDb..."
$pgRestore = Resolve-PgTool "pg_restore"
& $pgRestore @common -d $DstDb --no-owner --no-privileges $dumpFile
# pg_restore may return non-zero on benign warnings; verify table count instead.
$tables = & $psql @common -d $DstDb -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
Write-Host "Clone done. $DstDb public tables: $tables"
Remove-Item $dumpFile -ErrorAction SilentlyContinue
