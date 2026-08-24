<#
.SYNOPSIS
    Retrieval metrics only: no model, no policy engine, no execution.

.DESCRIPTION
    Answers one question - did the context pack contain the tables the reference query needed -
    in seconds, so a retrieval change can be measured without a full run.

.EXAMPLE
    ./scripts/eval-retrieval.ps1
    ./scripts/eval-retrieval.ps1 --k 12
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

& uv run python scripts/eval_run.py retrieval @Rest
exit $LASTEXITCODE
