[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet("focused", "full")]
    [string]$Mode = "focused",

    [switch]$DryRun,

    [switch]$KeepPytestCache,

    [string]$RunId,

    [string]$ArtifactsDir,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$runner = Join-Path $PSScriptRoot "ci_verify.py"
$scriptArgs = @("--mode", $Mode)

if ($DryRun) {
    $scriptArgs += "--dry-run"
}
if ($KeepPytestCache) {
    $scriptArgs += "--keep-pytest-cache"
}
if ($RunId) {
    $scriptArgs += @("--run-id", $RunId)
}
if ($ArtifactsDir) {
    $scriptArgs += @("--artifacts-dir", $ArtifactsDir)
}
if ($PytestArgs) {
    $scriptArgs += $PytestArgs
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython $runner @scriptArgs
} else {
    & uv run python $runner @scriptArgs
}

exit $LASTEXITCODE
