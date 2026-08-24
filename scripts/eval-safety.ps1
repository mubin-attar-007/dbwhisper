<#
.SYNOPSIS
    Only the cases whose contract is to decline. Run this on every change to the policy engine.

.DESCRIPTION
    Fails when any unsafe request executed, and also when any unsafe request was not declined -
    a case that errored for an unrelated reason is not a passing refusal.

.EXAMPLE
    ./scripts/eval-safety.ps1
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
$label = if ($env:EVAL_LABEL) { $env:EVAL_LABEL } else { 'eval-safety' }

& uv run python scripts/eval_run.py safety --label $label @Rest
exit $LASTEXITCODE
