<#
.SYNOPSIS
    The CI gate: run the whole evaluation corpus offline and fail on any unsafe execution.

.DESCRIPTION
    Deterministic, needs no credentials and no network. The exit code is the gate - a non-zero
    status means a request whose contract is to be declined returned rows, or a statement that
    executed does not pass an independent re-check by the policy engine.

.EXAMPLE
    ./scripts/eval-smoke.ps1
    ./scripts/eval-smoke.ps1 --dataset retail --limit 10
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Rest
)

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

if (-not $env:MODEL_PROFILE) { $env:MODEL_PROFILE = 'fake' }
if (-not $env:EMBEDDING_PROFILE) { $env:EMBEDDING_PROFILE = 'fake' }
$label = if ($env:EVAL_LABEL) { $env:EVAL_LABEL } else { 'eval-smoke' }

& uv run python scripts/eval_run.py smoke --label $label @Rest
exit $LASTEXITCODE
