$ErrorActionPreference = 'Stop'
$moduleRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $moduleRoot
try {
    & python 'tools/run_host_tests.py'
    if ($LASTEXITCODE -ne 0) { throw "Host tests failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
