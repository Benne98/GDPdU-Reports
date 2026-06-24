# install-orchestrator.ps1 — deploy orchestrator v2 into a target repo (Windows)
param(
    [Parameter(Mandatory = $false)]
    [string]$Target = (Join-Path (Split-Path $PSScriptRoot -Parent) "GDPdU-Reports"),
    [Parameter(Mandatory = $false)]
    [ValidateSet("gdpdu-reports", "finssentials")]
    [string]$Profile = "gdpdu-reports",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Src = Split-Path $PSScriptRoot -Parent
$Stamp = Get-Date -Format "yyyyMMddHHmmss"

function Say($msg) { Write-Host "  $msg" }

if (-not (Test-Path $Target)) {
    Write-Error "Target not found: $Target"
}

Say "Finssentials orchestrator v2 installer"
Say "source:  $Src"
Say "target:  $Target"
Say "profile: $Profile"
if ($DryRun) { Say "mode:    dry-run" }
Write-Host ""

function Backup-IfExists($path) {
    if (Test-Path $path) {
        $bak = "$path.bak-$Stamp"
        Say "backup: $path -> $bak"
        if (-not $DryRun) { Move-Item -Force $path $bak }
    }
}

function Copy-Tree($from, $to) {
    Backup-IfExists $to
    Say "copy: $from -> $to"
    if (-not $DryRun) {
        Copy-Item -Path $from -Destination $to -Recurse -Force
    }
}

# 1) .cursor
Copy-Tree (Join-Path $Src "cursor") (Join-Path $Target ".cursor")

# 2) AGENTS.md from profile
$agentsSrc = Join-Path $Src "profiles\$Profile\AGENTS.md"
if (-not (Test-Path $agentsSrc)) {
    Write-Error "Profile AGENTS.md not found: $agentsSrc"
}
$agentsDst = Join-Path $Target "AGENTS.md"
Backup-IfExists $agentsDst
Say "copy: $agentsSrc -> $agentsDst"
if (-not $DryRun) { Copy-Item -Force $agentsSrc $agentsDst }

# 3) docs (orchestrator docs — merge, don't delete existing)
$docsDst = Join-Path $Target "docs"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $docsDst | Out-Null }
foreach ($f in @(
    "architecture.md",
    "development-workflow.md",
    "financial-logic.md",
    "security.md",
    "benchmark-tasks.md",
    "phase0-audit.md"
)) {
    $from = Join-Path $Src "docs\$f"
    if (Test-Path $from) {
        $to = Join-Path $docsDst $f
        Say "copy: $from -> $to"
        if (-not $DryRun) { Copy-Item -Force $from $to }
    }
}
$profilesDst = Join-Path $docsDst "profiles"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $profilesDst | Out-Null }
$finProfile = Join-Path $Src "docs\profiles\finssentials.md"
if (Test-Path $finProfile) {
    Say "copy: profiles/finssentials.md"
    if (-not $DryRun) { Copy-Item -Force $finProfile (Join-Path $profilesDst "finssentials.md") }
}

# 4) .claude mirror (Claude Code CLI)
Copy-Tree (Join-Path $Src "claude") (Join-Path $Target ".claude")

# 5) CLAUDE.md for Claude Code (same as AGENTS for gdpdu)
if ($Profile -eq "gdpdu-reports") {
    $claudeDst = Join-Path $Target "CLAUDE.md"
    Backup-IfExists $claudeDst
    Say "copy: AGENTS.md -> CLAUDE.md"
    if (-not $DryRun) { Copy-Item -Force $agentsDst $claudeDst }
}

# 6) MCP template if present
$mcpSrc = Join-Path $Src ".mcp.json"
if (Test-Path $mcpSrc) {
    $mcpDst = Join-Path $Target ".mcp.json"
    if (-not (Test-Path $mcpDst)) {
        Say "copy: .mcp.json (new only)"
        if (-not $DryRun) { Copy-Item -Force $mcpSrc $mcpDst }
    }
}

Write-Host ""
Say "Done. Next:"
Say "  1) Reload Cursor window in target repo"
Say '  2) Settings - Hooks: confirm hooks enabled'
Say '  3) Multi-step work: use orchestrator agent'
Say '  4) /gdpdu-stack for port/proxy orientation'
