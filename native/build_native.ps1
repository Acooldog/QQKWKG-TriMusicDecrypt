param(
    [string]$OutputPath = "O:\A_python\A_kudog\assets\kudog_native.dll"
)

$ErrorActionPreference = "Stop"

$gcc = (Get-Command gcc -ErrorAction SilentlyContinue)
if (-not $gcc) {
    throw "gcc not found in PATH"
}

$source = "O:\A_python\A_kudog\native\kudog_native.c"
$outputDir = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force $outputDir | Out-Null

& $gcc.Source `
    -shared `
    -O3 `
    -std=c11 `
    -s `
    -o $OutputPath `
    $source

Write-Host "built: $OutputPath"
