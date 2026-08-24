<#
.SYNOPSIS
    Run the corpus and write a JSON + Markdown report, provenance block included.

.DESCRIPTION
    With no arguments this uses the gold-SQL oracle, which does NOT measure a model - the report
    says so in a banner at the top. To produce a figure that describes a model, name a real
    profile with --provider.

.EXAMPLE
    ./scripts/eval-report.ps1
    ./scripts/eval-report.ps1 --provider local-balanced
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
$label = if ($env:EVAL_LABEL) { $env:EVAL_LABEL } else { 'eval-custom' }
$outDir = if ($env:EVAL_OUT_DIR) { $env:EVAL_OUT_DIR } else { 'evaluation/results' }

& uv run python scripts/eval_run.py custom --out $outDir --label $label @Rest
exit $LASTEXITCODE
